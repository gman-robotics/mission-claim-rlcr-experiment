#!/usr/bin/env python3
"""Stdio MCP server: one `exec` tool that runs inside the task container.

JSON-RPC 2.0, one message per line. stdout is reserved for protocol
messages. The container name comes from GMA392_CONTAINER. Each call is
appended to the jsonl path in GMA392_EXEC_LOG.

The script is an argument to `bash -lc` inside `docker exec`. This process
does not invoke a host shell.
"""

import json
import os
import subprocess
import sys
import time

PROTOCOL = "2024-11-05"
OUTPUT_LIMIT = 20000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def truncate(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + "\n...[truncated]"


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def log_err(text):
    sys.stderr.write(text)
    if not text.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def respond_ok(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def respond_error(msg_id, code, message):
    send({
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    })


def tool_result(msg_id, text, is_error):
    respond_ok(msg_id, {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    })


def append_log(script, rc, stdout, stderr):
    path = os.environ.get("GMA392_EXEC_LOG")
    if not path:
        return
    record = {
        "ts": iso_now(),
        "script": script,
        "rc": rc,
        "stdout": truncate(stdout),
        "stderr": truncate(stderr),
    }
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
    except OSError as exc:
        log_err("exec log write failed: %s" % exc)


def valid_container(name):
    if not name or len(name) > 128:
        return False
    first = name[0]
    if not (first.isalnum()):
        return False
    for char in name[1:]:
        if not (char.isalnum() or char in "_.-"):
            return False
    return True


def parse_timeout(arguments):
    if "timeout_sec" not in arguments:
        return DEFAULT_TIMEOUT, None
    value = arguments.get("timeout_sec")
    # bool is a subclass of int; reject it.
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "timeout_sec must be an integer"
    if value < 1 or value > MAX_TIMEOUT:
        return None, "timeout_sec must be between 1 and %d" % MAX_TIMEOUT
    return value, None


def format_output(rc, stdout, stderr):
    return "exit_code: %s\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        rc,
        truncate(stdout),
        truncate(stderr),
    )


def run_exec(script, timeout_sec):
    container = os.environ.get("GMA392_CONTAINER") or ""
    if not valid_container(container):
        return None, "", "GMA392_CONTAINER is missing or not a container name"
    command = [
        "docker",
        "exec",
        "-w",
        "/testbed",
        container,
        "bash",
        "-lc",
        script,
    ]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout_sec,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if not isinstance(stdout, str):
            stdout = stdout.decode("utf-8", "replace")
        if not isinstance(stderr, str):
            stderr = stderr.decode("utf-8", "replace")
        stderr = (stderr + "\n[timed out after %ds]" % timeout_sec).strip()
        return 124, stdout, stderr
    except OSError as exc:
        return None, "", "failed to start docker exec: %s" % exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def handle_exec(msg_id, arguments):
    if not isinstance(arguments, dict):
        tool_result(msg_id, "arguments must be an object", True)
        return
    script = arguments.get("script")
    if not isinstance(script, str):
        tool_result(msg_id, "script must be a string", True)
        return
    timeout_sec, timeout_err = parse_timeout(arguments)
    if timeout_err:
        tool_result(msg_id, timeout_err, True)
        return
    rc, stdout, stderr = run_exec(script, timeout_sec)
    append_log(script, rc, stdout, stderr)
    if rc is None:
        tool_result(msg_id, format_output("none", stdout, stderr), True)
        return
    tool_result(msg_id, format_output(rc, stdout, stderr), False)


def tools_list():
    return {
        "tools": [
            {
                "name": "exec",
                "description": (
                    "Run a bash script inside the task container at /testbed. "
                    "No network. Returns exit_code, stdout, and stderr. "
                    "timeout_sec is optional, default %d, maximum %d."
                    % (DEFAULT_TIMEOUT, MAX_TIMEOUT)
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "Bash script. The working directory is /testbed.",
                        },
                        "timeout_sec": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_TIMEOUT,
                            "description": "Seconds before the exec is killed. Maximum 600.",
                        },
                    },
                    "required": ["script"],
                    "additionalProperties": False,
                },
            }
        ]
    }


def handle(message):
    if not isinstance(message, dict):
        respond_error(None, -32600, "Invalid request")
        return
    method = message.get("method")
    msg_id = message.get("id")
    has_id = "id" in message
    is_note = (not has_id) or (
        isinstance(method, str) and method.startswith("notifications/")
    )
    if not isinstance(method, str):
        if not is_note:
            respond_error(msg_id, -32600, "Invalid request")
        return
    params = message.get("params")
    if params is None:
        params = {}

    if method == "initialize":
        version = PROTOCOL
        if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
            version = params["protocolVersion"]
        respond_ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gma392", "version": "0.1.0"},
        })
        return
    if method in ("notifications/initialized", "initialized"):
        return
    if method == "ping":
        if is_note:
            return
        respond_ok(msg_id, {})
        return
    if method == "tools/list":
        respond_ok(msg_id, tools_list())
        return
    if method == "tools/call":
        if not isinstance(params, dict):
            tool_result(msg_id, "params must be an object", True)
            return
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "exec":
            tool_result(msg_id, "unknown tool", True)
            return
        handle_exec(msg_id, arguments)
        return
    if is_note:
        return
    respond_error(msg_id, -32601, "Method not found")


def main():
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            respond_error(None, -32700, "Parse error")
            continue
        try:
            handle(message)
        except Exception as exc:
            log_err("mcp handler error: %s" % exc)
            msg_id = message.get("id") if isinstance(message, dict) else None
            if isinstance(message, dict) and "id" in message:
                respond_error(msg_id, -32603, "Internal error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
