"""GMA-393 lesser-model driver. Python 3.9 stdlib only.

Backends: OpenRouter (OpenAI /chat/completions with tools) and Ollama native /api/chat tools.
Structured tools: read_file, edit_file, run, finish. Protocol enforced by the driver:
- read before edit: edit_file on a path not read this episode is rejected.
- finish is accepted only after at least one edit and a passing visible test run
  (pre-existing test files from PASS_TO_PASS, run by the driver, never the hidden test_patch).
- fail closed: malformed or unknown tool calls return an error to the model and are counted.
- text tool-call parsing only behind --text-fallback, counted separately.
API keys come from a helper command at runtime and are held in memory only.
"""

import json
import os
import posixpath
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request

ROOT = "/testbed"

SYSTEM = (
    "You fix a bug in the Python repository at /testbed inside a Linux container with no network.\n"
    "Use only the provided tools. Protocol:\n"
    "1. Investigate with run (grep, ls) and read_file before changing anything.\n"
    "2. You must read_file a file before edit_file on it. edit_file replaces an exact, unique snippet.\n"
    "3. Make a minimal source fix. Do not modify tests.\n"
    "4. Call finish when done. The harness then runs the existing tests; finish is rejected if they fail.\n"
)

def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}

TOOLS = [
    _fn("read_file", "Read a file under /testbed with line numbers.",
        {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        ["path"]),
    _fn("edit_file", "Replace exactly one occurrence of old_text with new_text in a file you have read.",
        {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
        ["path", "old_text", "new_text"]),
    _fn("run", "Run a bash command in /testbed (timeout 120 s). Use for grep, ls, python, pytest.",
        {"command": {"type": "string"}}, ["command"]),
    _fn("finish", "Declare the fix done. The harness runs the existing tests before accepting.",
        {"summary": {"type": "string"}}, ["summary"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


class RateLimited(Exception):
    pass


class LLMError(Exception):
    pass


def clip(text, limit=8000):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2:]


def key_from_helper(cmd):
    out = subprocess.run([cmd], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=True)
    key = out.stdout.decode().strip()
    if not key:
        raise LLMError("empty key from helper")
    return key


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:800]
        if exc.code == 429:
            raise RateLimited(body)
        raise LLMError("HTTP %s: %s" % (exc.code, body))


class Client:
    """One shared client; agents in a team call the same instance (same weights, same quota)."""

    def __init__(self, backend, base, model, max_tokens, key_cmd=None, num_ctx=32768):
        if not base:
            base = "https://openrouter.ai/api/v1" if backend == "openrouter" else "http://127.0.0.1:11434"
        self.backend, self.base, self.model = backend, base.rstrip("/"), model
        self.max_tokens, self.num_ctx = max_tokens, num_ctx
        self.key = key_from_helper(key_cmd) if backend == "openrouter" else None
        self.requests = 0
        self.rate_limit_hits = 0
        self.tool_choice = "required"

    def chat(self, messages, timeout, retries=6):
        """Retry 429s with backoff (15, 30, 60, 60... s); counts each 429 in self.rate_limit_hits."""
        for attempt in range(retries + 1):
            try:
                return self._chat(messages, timeout)
            except RateLimited:
                self.rate_limit_hits += 1
                if attempt == retries:
                    raise
                time.sleep(min(60, 15 * (2 ** attempt)))

    def _chat(self, messages, timeout):
        """Return (assistant_message, usage, model_name). tool_calls arguments are raw strings."""
        self.requests += 1
        if self.backend == "ollama":
            msgs = []
            for m in messages:
                m = dict(m)
                if m.get("tool_calls"):
                    m["tool_calls"] = [{"function": {"name": c["function"]["name"],
                                        "arguments": _loads_or(c["function"]["arguments"])}}
                                       for c in m["tool_calls"]]
                m.pop("tool_call_id", None)
                if m.get("role") == "tool" and "name" in m:
                    m["tool_name"] = m.pop("name")
                msgs.append(m)
            r = _post(self.base + "/api/chat", {"model": self.model, "messages": msgs, "tools": TOOLS,
                      "stream": False, "options": {"num_predict": self.max_tokens, "num_ctx": self.num_ctx,
                                                   "temperature": 0.2}}, {"Content-Type": "application/json"}, timeout)
            msg = r.get("message") or {}
            calls = []
            for i, c in enumerate(msg.get("tool_calls") or []):
                f = c.get("function") or {}
                a = f.get("arguments")
                calls.append({"id": "o%d_%d" % (self.requests, i), "type": "function",
                              "function": {"name": f.get("name"), "arguments": a if isinstance(a, str) else json.dumps(a)}})
            usage = {"prompt_tokens": r.get("prompt_eval_count", 0), "completion_tokens": r.get("eval_count", 0)}
            return {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}, usage, r.get("model")
        payload = {"model": self.model, "messages": messages, "tools": TOOLS, "tool_choice": self.tool_choice,
                   "max_tokens": self.max_tokens, "temperature": 0.2,
                   "usage": {"include": True}}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key,
                   "X-Title": "gma-393-harness"}
        try:
            r = _post(self.base + "/chat/completions", payload, headers, timeout)
        except LLMError as exc:
            if self.tool_choice == "required" and str(exc).startswith("HTTP 400") and "tool_choice" in str(exc):
                self.tool_choice = "auto"
                self.requests += 1
                payload["tool_choice"] = "auto"
                r = _post(self.base + "/chat/completions", payload, headers, timeout)
            else:
                raise
        if r.get("error"):
            if (r["error"] or {}).get("code") == 429:
                raise RateLimited(json.dumps(r["error"])[:800])
            raise LLMError(json.dumps(r["error"])[:800])
        msg = r["choices"][0]["message"]
        out = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
        return out, r.get("usage") or {}, r.get("model")


def _loads_or(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


def text_fallback_calls(content):
    """Last resort only: tool JSON written as text. Strict JSON, no repair."""
    found = []
    blobs = re.findall(r"<tool_call>(.*?)</tool_call>", content or "", re.S) or re.findall(
        r"```(?:json)?\s*(\{.*?\})\s*```", content or "", re.S) or [content or ""]
    for blob in blobs:
        try:
            obj = json.loads(blob.strip())
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name") in TOOL_NAMES:
            a = obj.get("arguments")
            found.append({"id": "t%d" % len(found), "type": "function",
                          "function": {"name": obj["name"], "arguments": a if isinstance(a, str) else json.dumps(a or {})}})
    return found


class Sandbox:
    def __init__(self, container, exec_log):
        self.container, self.exec_log = container, exec_log

    def _docker(self, argv, timeout=120, stdin=None):
        try:
            p = subprocess.run(
                               (["docker", "exec"] + (["-i"] if stdin is not None else []) + ["-w", ROOT, self.container] + argv),
                               input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
            return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return 124, "", "timed out after %s s" % timeout

    def log(self, kind, detail, rc, out, err):
        with open(self.exec_log, "a", encoding="utf-8") as h:
            h.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": kind,
                                "script": detail, "rc": rc, "stdout": out[-20000:], "stderr": err[-5000:]}) + "\n")

    def run(self, command, timeout=120):
        rc, out, err = self._docker(["timeout", "-k", "5", str(timeout), "bash", "-lc", command], timeout + 15)
        self.log("run", command, rc, out, err)
        return rc, out, err

    def read(self, path):
        rc, out, err = self._docker(["cat", "--", path])
        self.log("read_file", path, rc, "", err)
        return rc, out, err

    def write(self, path, text):
        rc, out, err = self._docker(["python3", "-c", "import sys;open(sys.argv[1],'w').write(sys.stdin.read())", path],
                                    stdin=text.encode())
        self.log("edit_file", path, rc, out, err)
        return rc, err


def norm(path):
    if not isinstance(path, str) or not path:
        return None
    p = posixpath.normpath(path if path.startswith("/") else posixpath.join(ROOT, path))
    return p if p == ROOT or p.startswith(ROOT + "/") else None


class Episode:
    """One agent's protocol state. Team layouts create one Episode per agent sharing a Sandbox and Client."""

    def __init__(self, sandbox, test_files, stats, max_finish_rejects=3):
        self.sb, self.test_files, self.stats = sandbox, test_files, stats
        self.read_paths, self.edits, self.finish_rejects = set(), 0, 0
        self.max_finish_rejects = max_finish_rejects
        self.done = False
        self.tests_passed = None

    def visible_tests(self):
        """Same interpreter as the harness eval (conda testbed, python -m pytest), PASS_TO_PASS files only.
        Accept only pytest rc 0 with a parsed summary that has passes and no failures/errors."""
        if not self.test_files:
            return True, "no visible tests configured"
        files = " ".join(shlex.quote(f) for f in self.test_files)
        cmd = ("source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && "
               "python -m pytest -q -p no:cacheprovider " + files)
        rc, out, err = self.sb.run(cmd, 900)
        summary = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed|errors?)\b.* in [\d.]+s", l)]
        last = summary[-1] if summary else ""
        ok = (rc == 0 and bool(re.search(r"\b\d+ passed\b", last))
              and not re.search(r"\b\d+ (failed|errors?)\b", last))
        return ok, "pytest rc=%s\n%s" % (rc, clip(out, 6000))

    def call(self, name, raw_args):
        """Return tool result text. Errors are returned to the model (fail closed), never executed."""
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except Exception as exc:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: invalid tool arguments JSON (%s). Re-issue the call with valid JSON." % exc
        if name not in TOOL_NAMES:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: unknown tool %r. Tools: %s" % (name, ", ".join(sorted(TOOL_NAMES)))
        missing = [k for k in next(t for t in TOOLS if t["function"]["name"] == name)["function"]["parameters"]["required"]
                   if not isinstance(args.get(k), str)]
        if missing:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: missing or non-string argument(s): %s" % ", ".join(missing)
        self.stats["valid_tool_calls"] += 1
        self.stats["by_tool"][name] = self.stats["by_tool"].get(name, 0) + 1  # attempts, incl. rejected
        if name == "run":
            rc, out, err = self.sb.run(args["command"])
            return "rc=%s\nstdout:\n%s\nstderr:\n%s" % (rc, clip(out), clip(err, 3000))
        if name == "read_file":
            p = norm(args["path"])
            if not p:
                return "ERROR: path must be under /testbed"
            rc, out, err = self.sb.read(p)
            if rc != 0:
                return "ERROR: cannot read %s: %s" % (p, err.strip()[:500])
            lines = out.splitlines()
            s = max(1, int(args.get("start_line") or 1)) if str(args.get("start_line") or "1").isdigit() else 1
            e = int(args.get("end_line")) if str(args.get("end_line") or "").isdigit() else min(len(lines), s + 399)
            if e < s and lines:
                return "ERROR: end_line must be >= start_line"
            self.read_paths.add(p)
            body = "\n".join("%6d  %s" % (i, lines[i - 1]) for i in range(s, min(e, len(lines)) + 1))
            return "%s (%d lines total, showing %d-%d)\n%s" % (p, len(lines), s, min(e, len(lines)), clip(body, 24000))
        if name == "edit_file":
            p = norm(args["path"])
            if not p:
                return "ERROR: path must be under /testbed"
            if p not in self.read_paths:
                self.stats["protocol_rejections"]["edit_unread"] += 1
                return "REJECTED: read_file %s before editing it." % p
            base = posixpath.basename(p)
            if ("/tests/" in p or "/testing/" in p or base.startswith("test_") or base.endswith("_test.py")
                    or base in ("conftest.py", "tests.py")):
                self.stats["protocol_rejections"]["edit_tests"] += 1
                return "REJECTED: do not modify tests."
            rc, text, err = self.sb.read(p)
            n = text.count(args["old_text"]) if args["old_text"] else 0
            if n != 1:
                self.stats["protocol_rejections"]["edit_not_unique"] += 1
                return "REJECTED: old_text found %d times; it must match exactly once. Re-read the file." % n
            rc, err = self.sb.write(p, text.replace(args["old_text"], args["new_text"], 1))
            if rc != 0:
                return "ERROR: write failed: %s" % err[:500]
            self.edits += 1
            return "OK: edited %s" % p
        # finish
        if self.edits == 0:
            self.stats["protocol_rejections"]["finish_no_edit"] += 1
            return "REJECTED: no edit has been made yet."
        ok, out = self.visible_tests()
        self.tests_passed = ok
        if ok:
            self.done = True
            return "ACCEPTED: existing tests pass."
        self.finish_rejects += 1
        self.stats["protocol_rejections"]["finish_tests_failed"] += 1
        if self.finish_rejects >= self.max_finish_rejects:
            self.done = True
            return "REJECTED (final): tests still fail. Episode ends."
        return "REJECTED: existing tests fail. Fix and call finish again.\n" + clip(out, 4000)


class AgentSlot:
    """Team-layout hook: a parked agent makes no LLM calls until woken with a message."""

    def __init__(self, name, system, episode):
        self.name, self.episode = name, episode
        self.messages = [{"role": "system", "content": system}]
        self.parked = True

    def wake(self, message):
        self.messages.append({"role": "user", "content": message})
        self.parked = False

    def park(self):
        self.parked = True


def step(client, slot, stats, deadline, text_fallback, agent_log):
    """One LLM turn for one awake slot. Returns False when the slot should park."""
    if slot.parked:
        stats["parked_skips"] += 1
        return False
    t = time.time()
    msg, usage, model = client.chat(slot.messages, timeout=max(10, min(300, deadline - time.time())))
    stats["llm_sec"] += time.time() - t
    stats["turns"] += 1
    stats["prompt_tokens"] += usage.get("prompt_tokens") or 0
    stats["completion_tokens"] += usage.get("completion_tokens") or 0
    stats["cost_usd"] = round(stats.get("cost_usd", 0.0) + float(usage.get("cost") or 0), 6)
    stats["model_reported"] = stats.get("model_reported") or model
    with open(agent_log, "a", encoding="utf-8") as h:
        h.write(json.dumps({"ts": time.time(), "agent": slot.name, "usage": usage, "message": msg}) + "\n")
    calls = msg.get("tool_calls") or []
    if not calls and text_fallback:
        calls = text_fallback_calls(msg.get("content"))
        stats["fallback_tool_calls"] += len(calls)
    slot.messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
    if not calls:
        stats["no_tool_turns"] += 1
        slot.messages.append({"role": "user", "content": "No tool call received. Use a tool; call finish when done."})
        return True
    for c in calls:
        f = c.get("function") or {}
        res = slot.episode.call(f.get("name"), f.get("arguments", "{}"))
        slot.messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": f.get("name"), "content": res})
        if slot.episode.done:
            break
    return not slot.episode.done


def _count_logged(path):
    n = 0
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            try:
                rc = json.loads(line).get("rc")
            except ValueError:
                continue
            if isinstance(rc, int) and not isinstance(rc, bool):
                n += 1
    return n


def run(args, prompt, container, run_dir, result, test_files, checkpoint):
    stats = {"backend": args.driver, "turns": 0, "llm_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "llm_sec": 0.0, "valid_tool_calls": 0, "invalid_tool_calls": 0, "fallback_tool_calls": 0,
             "no_tool_turns": 0, "parked_skips": 0, "by_tool": {}, "rate_limited": False, "llm_error": None,
             "protocol_rejections": {"edit_unread": 0, "edit_tests": 0, "edit_not_unique": 0,
                                     "finish_no_edit": 0, "finish_tests_failed": 0}}
    started = time.time()
    deadline = started + args.agent_timeout
    client = None
    episode = Episode(Sandbox(container, os.path.join(run_dir, "exec.jsonl")), test_files, stats)
    slot = AgentSlot("solo", SYSTEM, episode)
    slot.wake(prompt)
    try:
        client = Client(args.driver, args.openai_base, args.model, args.max_tokens_per_turn,
                        key_cmd=args.key_helper, num_ctx=args.num_ctx)
        while stats["turns"] < args.max_turns and time.time() < deadline:
            checkpoint()
            if not step(client, slot, stats, deadline, args.text_fallback, os.path.join(run_dir, "agent.jsonl")):
                break
        result["agent_timeout"] = time.time() >= deadline
    except RateLimited as exc:
        stats["rate_limited"] = True
        stats["llm_error"] = "HTTP 429"
    except LLMError as exc:
        stats["llm_error"] = str(exc).split(":", 1)[0][:80]
    finally:
        stats["llm_requests"] = client.requests if client else 0
        stats["rate_limit_hits"] = client.rate_limit_hits if client else 0
        stats["llm_sec"] = round(stats["llm_sec"], 3)
        total = stats["valid_tool_calls"] + stats["invalid_tool_calls"] + stats["fallback_tool_calls"] * 0
        stats["tool_call_success_rate"] = round(stats["valid_tool_calls"] / total, 3) if total else None
        stats["finish_accepted"] = episode.done and bool(episode.tests_passed)
        stats["edits"] = episode.edits
        result["timings"]["agent_sec"] = round(time.time() - started, 3)
        result["model_reported"] = stats.pop("model_reported", None)
        result["llm_stats"] = stats
        result["exec_calls"] = _count_logged(os.path.join(run_dir, "exec.jsonl"))
