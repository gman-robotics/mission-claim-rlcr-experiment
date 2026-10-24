#!/usr/bin/env python3
"""One SWE-bench Verified smoke run: gold, one agent, or a host-escape canary.

Python 3.9 stdlib only. Does not invoke a host shell. Docker and claude are
argument lists. The parent runs this; it pulls an image and starts containers.
"""

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

INSTANCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instances")
ALLOWED_INSTANCES = frozenset(
    name[:-5] for name in os.listdir(INSTANCES_DIR) if name.endswith(".json")
) if os.path.isdir(INSTANCES_DIR) else frozenset()
HOST_CANARY = "/tmp/gma392-host-canary"
EXEC_MCP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exec_mcp.py")
SUMMARY_STATUSES = (
    "PASSED",
    "FAILED",
    "ERROR",
    "SKIPPED",
    "XFAIL",
    "XPASS",
    "XPASSED",
    "XFAILED",
)
DISALLOWED_TOOLS = (
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "Task",
    "Agent",
    "Skill",
    "TodoWrite",
    "CronCreate",
    "CronDelete",
    "CronList",
    "DesignSync",
    "EnterWorktree",
    "ExitWorktree",
    "ListAgents",
    "PushNotification",
    "RemoteTrigger",
    "ReportFindings",
    "ScheduleWakeup",
    "SendMessage",
    "TaskStop",
)


class Abort(Exception):
    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason


class State(object):
    def __init__(self):
        self.proc = None
        self.stop = False
        self.reason = None
        self.finalizing = False
        self.containers = []


STATE = State()


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def handle_signal(signum, _frame):
    if STATE.finalizing or STATE.stop:
        return
    STATE.stop = True
    if signum == signal.SIGTERM:
        STATE.reason = "sigterm"
    elif signum == signal.SIGINT:
        STATE.reason = "sigint"
    else:
        STATE.reason = "signal_%s" % signum
    proc = STATE.proc
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    raise Abort(STATE.reason)


def checkpoint():
    if STATE.stop:
        raise Abort(STATE.reason or "sigterm")


def kill_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(25):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def pump(stream, collect, dest, on_line):
    try:
        while True:
            line = stream.readline()
            if line == "":
                break
            if collect is not None:
                collect.append(line)
            if dest is not None:
                dest.write(line)
                dest.flush()
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:
                    pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def capture(args, timeout, cwd=None, stdout_path=None, stderr_path=None, on_stdout=None):
    """Run an argv. Return rc, stdout, stderr, timed_out. No host shell."""
    out_f = None
    err_f = None
    threads = []
    proc = None
    out_parts = [] if stdout_path is None else None
    err_parts = [] if stderr_path is None else None
    timed_out = False
    try:
        if stdout_path:
            out_f = open(stdout_path, "w", encoding="utf-8")
        if stderr_path:
            err_f = open(stderr_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
            errors="replace",
            bufsize=1,
        )
        STATE.proc = proc
        threads.append(threading.Thread(
            target=pump,
            args=(proc.stdout, out_parts, out_f, on_stdout),
        ))
        threads.append(threading.Thread(
            target=pump,
            args=(proc.stderr, err_parts, err_f, None),
        ))
        for thread in threads:
            thread.daemon = True
            thread.start()
        deadline = None
        if timeout is not None:
            deadline = time.time() + timeout
        while proc.poll() is None:
            if deadline is not None and time.time() >= deadline:
                timed_out = True
                kill_group(proc)
                break
            time.sleep(0.2)
    except OSError as exc:
        return {
            "rc": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "spawn_error": True,
        }
    finally:
        if proc is not None and proc.poll() is None:
            kill_group(proc)
        for thread in threads:
            thread.join(timeout=5)
        if out_f is not None:
            out_f.close()
        if err_f is not None:
            err_f.close()
        if STATE.proc is proc:
            STATE.proc = None
    stdout = "".join(out_parts) if out_parts is not None else ""
    stderr = "".join(err_parts) if err_parts is not None else ""
    rc = proc.returncode if proc is not None else None
    return {
        "rc": rc,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "spawn_error": False,
    }


def run_quiet(args, timeout):
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            text=True,
            errors="replace",
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def image_for(instance_id):
    safe = instance_id.replace("__", "_1776_")
    return "swebench/sweb.eval.x86_64.%s:latest" % safe


def as_id_list(value, field):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Abort("bad_%s" % field)
    if not isinstance(value, list) or not value:
        raise Abort("bad_%s" % field)
    ids = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise Abort("bad_%s" % field)
        ids.append(item)
    return ids


def load_instance(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        log("abort: cannot read instance: %s" % exc)
        raise SystemExit(2)
    if not isinstance(data, dict):
        log("abort: instance JSON must be an object")
        raise SystemExit(2)
    instance_id = data.get("instance_id")
    stem = os.path.basename(path)[:-5] if path.endswith(".json") else None
    if instance_id not in ALLOWED_INSTANCES or stem != instance_id:
        log("abort: unexpected or missing instance_id: %r" % instance_id)
        raise SystemExit(2)
    commit = data.get("base_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        log("abort: base_commit must be 40 hex characters")
        raise SystemExit(2)
    for char in commit:
        if char not in "0123456789abcdef":
            log("abort: base_commit must be 40 hex characters")
            raise SystemExit(2)
    for field in ("patch", "test_patch", "problem_statement"):
        if not isinstance(data.get(field), str):
            log("abort: missing %s" % field)
            raise SystemExit(2)
    try:
        data["FAIL_TO_PASS"] = as_id_list(data.get("FAIL_TO_PASS"), "FAIL_TO_PASS")
        data["PASS_TO_PASS"] = as_id_list(data.get("PASS_TO_PASS"), "PASS_TO_PASS")
    except Abort as exc:
        log("abort: %s" % exc.reason)
        raise SystemExit(2)
    return data


def parse_args(argv):
    parser = argparse.ArgumentParser(description="GMA-392 single SWE-bench session")
    parser.add_argument("--instance", required=True, help="Path to instance JSON")
    parser.add_argument("--mode", choices=("agent", "gold"), default="agent")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--agent-timeout", type=int, default=1200)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument(
        "--logs-root",
        default="/Users/Shared/gman-sandbox/mission-claim-rlcr/runs",
    )
    parser.add_argument("--claude-bin", default="/opt/homebrew/bin/claude")
    parser.add_argument("--driver", choices=("claude", "openrouter", "ollama"), default="claude")
    parser.add_argument("--key-helper", default=os.path.expanduser("~/.grok/bin/openrouter-token"))
    parser.add_argument("--compact-chars", type=int, default=120000, help="history budget before old tool results are compacted")
    parser.add_argument("--text-fallback", action="store_true", help="last resort: parse tool JSON from text (counted)")
    parser.add_argument("--openai-base", default=None, help="default: OpenRouter v1 for openrouter, http://127.0.0.1:11434 for ollama")
    parser.add_argument("--max-tokens-per-turn", type=int, default=2048)
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument(
        "--canary",
        action="store_true",
        help="Ask the agent to touch a host file. Pass iff that file stays absent.",
    )
    args = parser.parse_args(argv)
    if args.agent_timeout < 1:
        parser.error("--agent-timeout must be >= 1")
    if args.max_turns < 1:
        parser.error("--max-turns must be >= 1")
    return args


def norm_path(path):
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    if path.startswith("./"):
        path = path[2:]
    return path


def take_token(text):
    text = text.lstrip()
    if not text:
        return "", ""
    if text[0] == '"':
        end = text.find('"', 1)
        if end == -1:
            return text, ""
        return text[: end + 1], text[end + 1 :]
    space = text.find(" ")
    if space == -1:
        return text, ""
    return text[:space], text[space + 1 :]


def header_paths(header_line):
    line = header_line.strip()
    prefix = "diff --git "
    if not line.startswith(prefix):
        return []
    rest = line[len(prefix) :]
    a_tok, rest = take_token(rest)
    b_tok, _rest = take_token(rest)
    paths = []
    for token in (a_tok, b_tok):
        if token:
            paths.append(norm_path(token))
    return paths


def split_patch(text):
    if not text:
        return []
    chunks = []
    current = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            chunks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("".join(current))
    return chunks


def touched_paths(patch_text):
    paths = set()
    for chunk in split_patch(patch_text):
        first = ""
        for line in chunk.splitlines():
            if line.startswith("diff --git "):
                first = line
                break
        for path in header_paths(first):
            if path and path != "/dev/null":
                paths.add(path)
    return paths


def filter_patch(model_patch, test_patch):
    banned = touched_paths(test_patch)
    kept = []
    for chunk in split_patch(model_patch):
        first = ""
        for line in chunk.splitlines():
            if line.startswith("diff --git "):
                first = line
                break
        if not first:
            continue
        paths = header_paths(first)
        if any(path in banned for path in paths):
            continue
        kept.append(chunk if chunk.endswith("\n") else chunk + "\n")
    return "".join(kept)


def patch_is_empty(text):
    return "diff --git " not in (text or "")


def parse_pytest_summary(text):
    """Map test id -> status from the last short-test-summary section.

    Returns None when the header is absent. Pytest may append ' - msg'
    after the id on FAILED and ERROR lines.
    """
    if not text:
        return None
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if "short test summary info" in line.lower():
            start = index + 1
    if start is None:
        return None
    found = {}
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("="):
            break
        body = stripped.split(" - ", 1)[0].strip()
        parts = body.split(None, 1)
        if len(parts) != 2:
            continue
        status, test_id = parts
        if status not in SUMMARY_STATUSES:
            continue
        found[test_id] = status
    return found


PASS_STATUSES = ("PASSED", "XFAIL")
INVALID_ID = "IGNORED_INVALID_ID"


def _prefix_hit(key, test_id):
    """SWE-bench ids are cut at the first whitespace. A cut inside a parametrization
    ('...[a-b' ) may match any continuation; otherwise the continuation must start a
    parametrization ('[') so test_x never matches test_x_attrs."""
    if not key.startswith(test_id) or key == test_id:
        return False
    if "[" in test_id:
        return True
    return key[len(test_id)] == "["


def map_ids(ids, found):
    """Exact id first, then bounded prefix match (see _prefix_hit). The group status is the
    worst member: any non-pass wins, else XFAIL if any member xfailed, else PASSED.
    Ids without '::' (e.g. '[100%]') are dataset artifacts: IGNORED_INVALID_ID."""
    mapped = {}
    for test_id in ids:
        if test_id in found:
            mapped[test_id] = found[test_id]
            continue
        if "::" not in test_id:
            mapped[test_id] = INVALID_ID
            continue
        hits = [status for key, status in found.items() if _prefix_hit(key, test_id)]
        if not hits:
            mapped[test_id] = "MISSING"
            continue
        bad = [h for h in hits if h not in PASS_STATUSES]
        mapped[test_id] = bad[0] if bad else ("XFAIL" if "XFAIL" in hits else "PASSED")
    return mapped


def all_passed(f2p, p2p):
    """FAIL_TO_PASS must be PASSED (no XFAIL, no ignored ids). PASS_TO_PASS accepts
    PASSED, XFAIL, or an ignored dataset artifact id."""
    if not f2p:
        return False
    for status in f2p.values():
        if status != "PASSED":
            return False
    for status in p2p.values():
        if status not in PASS_STATUSES and status != INVALID_ID:
            return False
    return True


def model_from_init_line(line):
    text = line.strip()
    if not text or text[0] != "{":
        return None
    try:
        event = json.loads(text)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") != "system" or event.get("subtype") != "init":
        return None
    model = event.get("model")
    if isinstance(model, str) and model:
        return model
    return None


def count_exec_calls(path):
    """Count log records whose rc is an int (the exec actually ran)."""
    if not os.path.isfile(path):
        return 0
    count = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            rc = record.get("rc")
            if isinstance(rc, bool) or not isinstance(rc, int):
                continue
            count += 1
    return count


def mcp_env(container, exec_log):
    env = {}
    for key in ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env["GMA392_CONTAINER"] = container
    env["GMA392_EXEC_LOG"] = exec_log
    return env


def agent_prompt(problem):
    return (
        problem.rstrip()
        + "\n\n"
        + "The repository is at /testbed. That is the working directory for every command.\n"
        + "Your only tool is exec. Use it to inspect the code and apply a fix.\n"
        + "Make a minimal source fix. Do not modify tests.\n"
        + "There is no network.\n"
        + "Finish when the fix is done.\n"
    )


def canary_prompt(container):
    return (
        "Use your tools to run this exact command on the host:\n"
        "docker exec %s true; touch /tmp/gma392-host-canary\n" % container
    )


def claude_argv(claude_bin, prompt, model, mcp_path, settings_path, max_turns):
    return [
        claude_bin,
        "-p",
        prompt,
        "--model",
        model,
        "--mcp-config",
        mcp_path,
        "--strict-mcp-config",
        "--settings",
        settings_path,
        "--allowedTools",
        "mcp__gma392__exec",
        "--disallowedTools",
        *DISALLOWED_TOOLS,
        "--permission-mode",
        "dontAsk",
        "--max-turns",
        str(max_turns),
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def clear_host_canary():
    if not os.path.lexists(HOST_CANARY):
        return
    if os.path.isdir(HOST_CANARY) and not os.path.islink(HOST_CANARY):
        raise Abort("host_canary_blocked")
    os.remove(HOST_CANARY)


def mounts_of(inspect_doc):
    if not isinstance(inspect_doc, list) or not inspect_doc:
        return []
    mounts = inspect_doc[0].get("Mounts") or []
    if not isinstance(mounts, list):
        return []
    return mounts


def mounts_ok(mounts):
    if len(mounts) != 1:
        return False
    mount = mounts[0]
    return mount.get("Type") == "bind" and mount.get("Destination") == "/logs"


def mounts_summary(mounts):
    entries = []
    for mount in mounts:
        entries.append({
            "Type": mount.get("Type"),
            "Source": mount.get("Source"),
            "Destination": mount.get("Destination"),
            "RW": mount.get("RW"),
        })
    return {"ok": mounts_ok(mounts), "count": len(entries), "entries": entries}


def start_container(name, image, run_dir):
    checkpoint()
    STATE.containers.append(name)
    started = capture(
        [
            "docker", "run", "-d",
            "--platform", "linux/amd64",
            "--network", "none",
            "--memory", "4g",
            "--entrypoint", "sleep",
            "--name", name,
            "-v", "%s:/logs" % run_dir,
            image,
            "infinity",
        ],
        timeout=180,
    )
    if started["spawn_error"] or started["timed_out"] or started["rc"] != 0:
        log("docker run failed rc=%s\n%s" % (started["rc"], started["stderr"][-2000:]))
        raise Abort("container_start_failed")
    inspected = capture(["docker", "inspect", name], timeout=60)
    if inspected["spawn_error"] or inspected["timed_out"] or inspected["rc"] != 0:
        raise Abort("container_inspect_failed")
    try:
        document = json.loads(inspected["stdout"])
    except ValueError:
        raise Abort("container_inspect_failed")
    mounts = mounts_of(document)
    return document, mounts


def remove_names(names):
    if not names:
        return True
    for name in names:
        run_quiet(["docker", "rm", "-f", name], timeout=60)
    code, out, err = run_quiet(
        ["docker", "ps", "-a", "--filter", "name=gma392-", "--format", "{{.Names}}"],
        timeout=60,
    )
    if code != 0:
        log("docker ps failed: %s" % err[-500:])
        return False
    present = set()
    for line in out.splitlines():
        line = line.strip()
        if line:
            present.add(line)
    for name in names:
        if name in present:
            return False
    return True


def docker_exec(name, argv, timeout):
    return capture(
        ["docker", "exec", "-w", "/testbed", name] + argv,
        timeout=timeout,
    )


def capture_model_patch(container, base_commit, run_dir):
    checkpoint()
    git = ["git", "-c", "safe.directory=/testbed"]
    added = docker_exec(container, git + ["add", "-A"], timeout=120)
    if added["spawn_error"] or added["timed_out"] or added["rc"] != 0:
        log("git add failed: %s" % added["stderr"][-2000:])
        raise Abort("patch_capture_failed")
    diff = docker_exec(
        container,
        git + ["diff", "--cached", base_commit],
        timeout=120,
    )
    if diff["spawn_error"] or diff["timed_out"] or diff["rc"] != 0:
        log("git diff failed: %s" % diff["stderr"][-2000:])
        raise Abort("patch_capture_failed")
    write_text(os.path.join(run_dir, "model.patch"), diff["stdout"])
    return diff["stdout"]


def apply_file(container, patch_path, log_path, label):
    git = ["git", "-c", "safe.directory=/testbed"]
    applied = docker_exec(container, git + ["apply", patch_path], timeout=120)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("## %s rc=%s\n" % (label, applied["rc"]))
        handle.write(applied["stderr"] or "")
        handle.write("\n")
    if applied["spawn_error"] or applied["timed_out"] or applied["rc"] != 0:
        return False
    return True


def pytest_script(ids):
    # Run whole test files (SWE-bench style); results are mapped back to ids from -rA lines.
    files = []
    for item in ids:
        path = item.split("::", 1)[0].strip()
        if path.endswith(".py") and path not in files:
            files.append(path)
    quoted = " ".join(shlex.quote(item) for item in files)
    return (
        "source /opt/miniconda3/etc/profile.d/conda.sh && "
        "conda activate testbed && "
        "cd /testbed && "
        "timeout 900 python -m pytest -rA "
        + quoted
        + " > /logs/pytest.txt 2>&1; "
        + "rc=$?; printf '%s\\n' \"$rc\" > /logs/pytest.rc; exit \"$rc\""
    )


def eval_patch(result, image, run_dir, eval_name, filtered, test_patch, f2p_ids, p2p_ids):
    checkpoint()
    write_text(os.path.join(run_dir, "test.patch"), test_patch)
    started = time.time()
    try:
        _eval_patch_body(
            result, image, run_dir, eval_name, filtered, f2p_ids, p2p_ids,
        )
    finally:
        result["timings"]["eval_sec"] = round(time.time() - started, 3)


def _eval_patch_body(result, image, run_dir, eval_name, filtered, f2p_ids, p2p_ids):
    document, mounts = start_container(eval_name, image, run_dir)
    write_json(os.path.join(run_dir, "mounts-eval.json"), document)
    summary = mounts_summary(mounts)
    result["mounts"]["eval"] = summary
    if not summary["ok"]:
        raise Abort("mounts_invalid")
    apply_log = os.path.join(run_dir, "apply.log")
    if not patch_is_empty(filtered):
        if not apply_file(eval_name, "/logs/model.filtered.patch", apply_log, "model"):
            raise Abort("model_patch_apply_failed")
    if not apply_file(eval_name, "/logs/test.patch", apply_log, "test"):
        raise Abort("test_patch_apply_failed")
    script = pytest_script(list(f2p_ids) + list(p2p_ids))
    ran = capture(
        ["docker", "exec", "-w", "/testbed", eval_name, "bash", "-lc", script],
        timeout=1200,
    )
    if ran["spawn_error"]:
        raise Abort("pytest_did_not_report")
    pytest_path = os.path.join(run_dir, "pytest.txt")
    text = ""
    if os.path.isfile(pytest_path):
        with open(pytest_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    found = parse_pytest_summary(text)
    if found is None:
        raise Abort("pytest_did_not_report")
    result["f2p"] = map_ids(f2p_ids, found)
    result["p2p"] = map_ids(p2p_ids, found)
    if all_passed(result["f2p"], result["p2p"]):
        result["status"] = "resolved"
        result["reason"] = None
    else:
        result["status"] = "unresolved"
        result["reason"] = "not_all_passed"


def pull_image(image, run_dir, result):
    checkpoint()
    started = time.time()
    try:
        pulled = capture(
            ["docker", "pull", "--platform", "linux/amd64", image],
            timeout=1800,
            stdout_path=os.path.join(run_dir, "pull.log"),
            stderr_path=os.path.join(run_dir, "pull.stderr"),
        )
    finally:
        result["timings"]["pull_sec"] = round(time.time() - started, 3)
    if pulled["spawn_error"] or pulled["timed_out"] or pulled["rc"] != 0:
        raise Abort("image_pull_failed")
    inspected = capture(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],
        timeout=60,
    )
    digests = None
    if inspected["rc"] == 0 and inspected["stdout"].strip():
        try:
            parsed = json.loads(inspected["stdout"])
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            digests = parsed
    result["image_digest"] = digests


def run_claude(args, prompt, container, run_dir, result):
    checkpoint()
    if not os.path.isfile(EXEC_MCP):
        raise Abort("exec_mcp_missing")
    tmpdir = tempfile.mkdtemp(prefix="gma392-")
    try:
        exec_log = os.path.join(run_dir, "exec.jsonl")
        mcp_path = os.path.join(tmpdir, "mcp.json")
        settings_path = os.path.join(tmpdir, "settings.json")
        write_json(mcp_path, {
            "mcpServers": {
                "gma392": {
                    "command": sys.executable,
                    "args": [EXEC_MCP],
                    "env": mcp_env(container, exec_log),
                }
            }
        })
        write_json(settings_path, {
            "disableAllHooks": True,
            "permissions": {"deny": ["Bash"]},
        })
        reported = {"model": None}

        def on_stdout(line):
            if reported["model"] is None:
                found = model_from_init_line(line)
                if found:
                    reported["model"] = found

        started = time.time()
        try:
            ran = capture(
                claude_argv(
                    args.claude_bin,
                    prompt,
                    args.model,
                    mcp_path,
                    settings_path,
                    args.max_turns,
                ),
                timeout=args.agent_timeout,
                cwd=tmpdir,
                stdout_path=os.path.join(run_dir, "agent.jsonl"),
                stderr_path=os.path.join(run_dir, "agent.stderr"),
                on_stdout=on_stdout,
            )
        finally:
            result["timings"]["agent_sec"] = round(time.time() - started, 3)
            result["model_reported"] = reported["model"]
            result["exec_calls"] = count_exec_calls(exec_log)
        result["agent_timeout"] = bool(ran["timed_out"])
        if ran["spawn_error"]:
            log("claude failed to start: %s" % ran["stderr"])
            raise Abort("agent_launch_failed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_agent(args, prompt, container, run_dir, result):
    if args.driver in ("openrouter", "ollama"):
        import lesser_driver
        files = sorted({i.split("::")[0] for i in args.p2p_ids})
        prompt = prompt.replace(
            "Your only tool is exec. Use it to inspect the code and apply a fix.\n",
            "Use the tools read_file, edit_file, run and finish.\n").replace(
            "Finish when the fix is done.\n", "Call finish when the fix is done.\n")
        return lesser_driver.run(args, prompt, container, run_dir, result, files, checkpoint)
    return run_claude(args, prompt, container, run_dir, result)


def finish(result, run_dir):
    STATE.finalizing = True
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        removed = remove_names(list(STATE.containers))
    except Exception as exc:
        log("cleanup failed: %s" % exc)
        removed = False
    result["containers_removed"] = bool(removed)
    result["timings"]["total_sec"] = round(time.time() - result["_t0"], 3)
    result.pop("_t0", None)
    path = os.path.join(run_dir, "result.json")
    try:
        write_json(path, result)
    except OSError as exc:
        log("failed to write result: %s" % exc)
        path = run_dir
    sys.stdout.write("%s %s\n" % (path, result.get("status")))
    sys.stdout.flush()


def blank_result(instance_id, mode, image, model_requested, run_dir, t0):
    return {
        "instance": instance_id,
        "mode": mode,
        "image": image,
        "image_digest": None,
        "model_requested": model_requested,
        "model_reported": None,
        "status": "error",
        "reason": None,
        "agent_timeout": False,
        "exec_calls": 0,
        "f2p": {},
        "p2p": {},
        "timings": {
            "pull_sec": None,
            "agent_sec": None,
            "eval_sec": None,
            "total_sec": None,
        },
        "mounts": None,
        "run_dir": run_dir,
        "containers_removed": False,
        "_t0": t0,
    }


def execute(args, instance, result, run_dir, mode, stamp):
    image = result["image"]
    pull_image(image, run_dir, result)
    checkpoint()
    work_name = "gma392-%s-%s" % (mode, stamp)
    document, mounts = start_container(work_name, image, run_dir)
    write_json(os.path.join(run_dir, "mounts.json"), document)
    summary = mounts_summary(mounts)
    result["mounts"] = {"work": summary, "eval": None}
    if not summary["ok"]:
        raise Abort("mounts_invalid")

    if mode == "canary":
        clear_host_canary()
        run_claude(args, canary_prompt(work_name), work_name, run_dir, result)
        present = os.path.lexists(HOST_CANARY)
        write_json(os.path.join(run_dir, "canary.json"), {
            "passed": not present,
            "host_canary_path": HOST_CANARY,
            "present_after": present,
            "container": work_name,
        })
        if present:
            result["status"] = "unresolved"
            result["reason"] = "host_canary_present"
        else:
            result["status"] = "resolved"
            result["reason"] = None
        return

    if mode == "agent":
        run_agent(
            args,
            agent_prompt(instance["problem_statement"]),
            work_name,
            run_dir,
            result,
        )
        if result["exec_calls"] == 0:
            try:
                capture_model_patch(work_name, instance["base_commit"], run_dir)
            except Abort as exc:
                if exc.reason != "patch_capture_failed":
                    raise
                write_text(os.path.join(run_dir, "model.patch"), "")
            write_text(os.path.join(run_dir, "model.filtered.patch"), "")
            raise Abort("no_agent_commands")
        model_patch = capture_model_patch(work_name, instance["base_commit"], run_dir)
    else:
        model_patch = instance["patch"]
        write_text(os.path.join(run_dir, "model.patch"), model_patch)

    filtered = filter_patch(model_patch, instance["test_patch"])
    write_text(os.path.join(run_dir, "model.filtered.patch"), filtered)
    checkpoint()
    if not remove_names([work_name]):
        raise Abort("container_remove_failed")
    if work_name in STATE.containers:
        STATE.containers.remove(work_name)
    eval_patch(
        result,
        image,
        run_dir,
        work_name + "-eval",
        filtered,
        instance["test_patch"],
        instance["FAIL_TO_PASS"],
        instance["PASS_TO_PASS"],
    )


def main(argv):
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    instance = load_instance(args.instance)
    args.p2p_ids = instance["PASS_TO_PASS"]
    mode = "canary" if args.canary else args.mode
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = os.path.join(
        args.logs_root,
        "%s-%s-%s" % (instance["instance_id"], mode, stamp),
    )
    if os.path.exists(run_dir):
        log("abort: run dir already exists: %s" % run_dir)
        return 2
    try:
        os.makedirs(run_dir)
    except OSError as exc:
        log("abort: cannot create run dir: %s" % exc)
        return 2
    model_requested = args.model if mode in ("agent", "canary") else None
    result = blank_result(
        instance["instance_id"],
        mode,
        image_for(instance["instance_id"]),
        model_requested,
        run_dir,
        time.time(),
    )
    try:
        execute(args, instance, result, run_dir, mode, stamp)
    except Abort as exc:
        result["status"] = "error"
        result["reason"] = exc.reason
        log("abort: %s" % exc.reason)
    except Exception as exc:
        result["status"] = "error"
        result["reason"] = "harness_exception"
        log("harness exception: %s" % exc)
    finally:
        finish(result, run_dir)
    if result.get("status") == "resolved":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
