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
    "4. run is read-only: no redirects, sed -i, mv, cp, tee or scripts that write files. Use edit_file.\n"
    "5. Call finish when done. The harness then runs the existing tests; finish is rejected if they fail.\n"
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
HANDOFF_TOOL = _fn("handoff", "Hand the task to your teammate with a self-contained message. You are parked until they hand back.",
                   {"message": {"type": "string"}}, ["message"])
ALL_TOOLS = TOOLS + [HANDOFF_TOOL]
ALL_TOOL_NAMES = TOOL_NAMES | {"handoff"}


WRITE_PATTERNS = [
    (r"\bsed\b[^|;&]*\s['\"]?(-[a-zA-Z]*i|--in-place)", "sed -i"),
    (r"\bperl\b[^|;&]*\s-[a-zA-Z]*i", "perl -i"),
    (r"\btee\b", "tee"),
    (r"\b(mv|cp|rm|install|truncate|dd|ln|touch|chmod|chown|mkdir|rmdir)\b", "file mutation command"),
    (r"\bgit\b(\s+(-c|-C)\s+\S+|\s+--\S+)*\s+(apply|am|checkout|switch|restore|reset|stash|clean|commit|rm|mv|merge|rebase|cherry-pick)\b", "git write"),
    (r"(^|[;&|\s/])patch\b", "patch"),
    (r"open\([^)]*,\s*(mode\s*=\s*)?['\"][^'\"]*[wax+]", "python file write"),
    (r"\.write_(text|bytes)\(|\.(write|writelines)\(", "python file write"),
    (r"\b(shutil|os|pathlib|Path)\b[.\w]*\.(copy\w*|move|remove|unlink|rename|replace|rmtree|makedirs|mkdir|symlink|chmod|truncate|touch)\b", "python file mutation"),
]
# /tmp scratch writes are allowed, but not paths that climb out of /tmp.
_SAFE_REDIRECT = re.compile(r"\d?>>?\s*(/dev/null\b|/tmp/(?![^\s;&|]*\.\.)[^\s;&|]*)|\d?>&\d|&>\s*/dev/null\b")


def write_violation(command):
    """Return a reason if a run command looks like it writes files (edits must go through edit_file).
    Mutation patterns are matched on the raw command, quotes included, so `python -c '...shutil.copy...'`
    and `os.system("cp ...")` are caught. Redirects are matched with quoted text removed so
    `python -c "print(1 > 0)"` is allowed. Fail closed: a false positive only costs the model a rephrase."""
    for pat, why in WRITE_PATTERNS:
        if re.search(pat, command):
            return why
    if _has_redirect(command):
        return "output redirect to a file"
    # Quoted text is normally skipped for redirects (python -c "print(1 > 0)"), but a quoted string
    # handed to a shell (os.system, subprocess, popen, sh -c, bash -c, eval) is itself a command.
    if re.search(r"\bos\.(system|popen|exec\w*|spawn\w*)|\bsubprocess\b|\bpopen\b|\b(ba|z|da)?sh\s+-\w*c\b|\beval\b", command):
        for quoted in re.findall(r"'([^']*)'|\"([^\"]*)\"", command):
            inner = quoted[0] or quoted[1]
            if _has_redirect(inner) or _has_redirect_raw(inner):
                return "output redirect inside a shell string"
    return None


def _has_redirect(text):
    stripped = _SAFE_REDIRECT.sub(" ", text)
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "''", stripped)
    return bool(re.search(r"(?<![<>=!-])>>?(?!=)", stripped))


def _has_redirect_raw(text):
    """Redirect check without quote stripping (for strings that a shell will execute)."""
    stripped = _SAFE_REDIRECT.sub(" ", text)
    return bool(re.search(r"(?<![<>=!-])>>?(?!=)", stripped))


def _msize(m):
    return len(m.get("content") or "") + sum(len(json.dumps(c)) for c in (m.get("tool_calls") or []))


def compact(messages, budget_chars, keep_tail=8):
    """Bring the history under budget_chars. Never touches messages[0:2] (system, task).
    Pass 1: shrink tool results older than the last keep_tail messages, oldest first, to a one-line stub.
    Pass 2: if still over, stub every tool result except the newest one, and clip long assistant text.
    Pass 3: if still over, clip the newest tool result and older user nudges/handoffs to 2000 chars.
    Pass 4: blank the oldest tool-result bodies. Pass 5: clip large old tool-call arguments.
    Pass 6: delete the oldest whole exchanges (assistant call + its results). Pass 7: clip long
    user text including the task. Pairing is preserved throughout. Mutates the list. Returns chars saved.
    Only the system prompt and short recent messages are irreducible."""
    total = sum(_msize(m) for m in messages)
    if total <= budget_chars:
        return 0
    start_total = total

    def stub(m, keep=160):
        c = m.get("content") or ""
        first = c.splitlines()[0][:keep] if c else ""
        new = "[compacted %d chars] %s" % (len(c), first)
        if len(new) < len(c):
            m["content"] = new
            return len(c) - len(new)
        return 0

    body = list(range(2, len(messages)))
    tool_idx = [i for i in body if messages[i].get("role") == "tool"]
    for i in [i for i in tool_idx if i < len(messages) - keep_tail]:
        if total <= budget_chars * 0.7:
            break
        total -= stub(messages[i])
    if total > budget_chars:
        for i in tool_idx[:-1]:
            if total <= budget_chars * 0.9:
                break
            total -= stub(messages[i], keep=80)
        for i in body[:-1]:
            m = messages[i]
            if total <= budget_chars * 0.9:
                break
            if m.get("role") == "assistant" and len(m.get("content") or "") > 500:
                c = m["content"]
                m["content"] = c[:500] + " [clipped]"
                total -= len(c) - len(m["content"])
    if total > budget_chars:
        for i in body:
            m = messages[i]
            if total <= budget_chars:
                break
            c = m.get("content") or ""
            if m.get("role") in ("tool", "user") and len(c) > 2000:
                m["content"] = c[:1000] + "\n...[compacted]...\n" + c[-1000:]
                total -= len(c) - len(m["content"])
    if total > budget_chars:
        # Pass 4: blank the oldest tool-result bodies (keeps the result message, so pairing holds).
        for i in range(2, len(messages) - 2):
            if total <= budget_chars:
                break
            m = messages[i]
            if m.get("role") == "tool" and len(m.get("content") or "") > 9:
                total -= len(m["content"]) - len("[dropped]")
                m["content"] = "[dropped]"
    if total > budget_chars:
        # Pass 5: clip large tool_call arguments in older assistant messages (e.g. big edit_file
        # payloads). The call id and name stay, so its result stays paired.
        for i in range(2, len(messages) - 2):
            if total <= budget_chars:
                break
            for c in messages[i].get("tool_calls") or []:
                f = c.get("function") or {}
                args = f.get("arguments") or ""
                if len(args) > 300:
                    f["arguments"] = json.dumps({"_compacted_chars": len(args)})
                    total -= len(args) - len(f["arguments"])
    k = 2
    while total > budget_chars and k < len(messages) - 2:
        # Pass 6: delete the oldest whole exchange (assistant message plus its contiguous tool
        # results) whose results are not in the protected last two messages. Skip, never stall.
        if messages[k].get("role") != "assistant":
            k += 1
            continue
        j = k + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            j += 1
        if j > len(messages) - 2:
            k = j
            continue
        removed = messages[k:j]
        del messages[k:j]
        total -= sum(_msize(m) for m in removed)
    if total > budget_chars:
        # Pass 7: clip any remaining long user text, including the task / first handoff at index 1,
        # keeping its head and tail.
        for i in range(1, len(messages)):
            if total <= budget_chars:
                break
            m = messages[i]
            c = m.get("content") or ""
            if m.get("role") in ("user", "tool") and len(c) > 1200:
                m["content"] = c[:800] + "\n...[compacted]...\n" + c[-400:]
                total -= len(c) - len(m["content"])
    if total > budget_chars:
        # Pass 8 (hard floor): repeatedly halve the largest non-system content or tool-call
        # argument until the history fits. Only the system prompt is irreducible.
        while total > budget_chars:
            best, size = None, 0
            for i in range(1, len(messages)):
                m = messages[i]
                if len(m.get("content") or "") > size:
                    best, size = ("c", m, None), len(m["content"])
                for c in m.get("tool_calls") or []:
                    f = c.get("function") or {}
                    if len(f.get("arguments") or "") > size:
                        best, size = ("a", m, f), len(f["arguments"])
            if best is None or size <= 40:
                break
            over = total - budget_chars
            keep = max(20, min(size // 2, size - over - 20))
            kind, m, f = best
            if kind == "c":
                new = m["content"][:keep] + " [clip]"
                total -= len(m["content"]) - len(new)
                m["content"] = new
            else:
                new = json.dumps({"_compacted_chars": size})
                if len(new) >= size:
                    break
                total -= size - len(new)
                f["arguments"] = new
    return start_total - total


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

    def chat(self, messages, timeout, retries=6, tools=None, max_tokens=None):
        """Retry 429s with backoff (15, 30, 60, 60... s); counts each 429 in self.rate_limit_hits."""
        for attempt in range(retries + 1):
            try:
                return self._chat(messages, timeout, tools or TOOLS, max_tokens or self.max_tokens)
            except RateLimited:
                self.rate_limit_hits += 1
                if attempt == retries:
                    raise
                time.sleep(min(60, 15 * (2 ** attempt)))

    def _chat(self, messages, timeout, tools, max_tokens):
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
            r = _post(self.base + "/api/chat", {"model": self.model, "messages": msgs, "tools": tools,
                      "stream": False, "options": {"num_predict": max_tokens, "num_ctx": self.num_ctx,
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
        payload = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": self.tool_choice,
                   "max_tokens": max_tokens, "temperature": 0.2,
                   "usage": {"include": True}}
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key,
                   "X-Title": "gma-393-harness"}
        try:
            r = _post(self.base + "/chat/completions", payload, headers, timeout)
        except LLMError as exc:
            msg_400 = str(exc)
            if (self.tool_choice == "required" and msg_400.startswith("HTTP 400")
                    and re.search(r"tool_choice[^\n]{0,80}(not supported|unsupported|invalid|must be)", msg_400, re.I)):
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
        summary = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed|errors?)\b.* in [\d.]+ ?s", l)]
        last = summary[-1] if summary else ""
        ok = (rc == 0 and bool(re.search(r"\b\d+ passed\b", last))
              and not re.search(r"\b\d+ (failed|errors?)\b", last))
        return ok, "pytest rc=%s\n%s\nstderr:\n%s" % (rc, clip(out, 6000), clip(err, 2000))

    def call(self, name, raw_args, agent=None):
        """Return tool result text. Errors are returned to the model (fail closed), never executed.
        agent (team mode): its own read set (read-before-edit is per agent), stats and allowed tools."""
        saved = (self.stats, self.read_paths)
        allowed = TOOL_NAMES
        if agent is not None:
            self.stats, self.read_paths = agent.stats, agent.read_paths
            allowed = agent.tool_names
        try:
            return self._call(name, raw_args, allowed)
        finally:
            self.stats, self.read_paths = saved

    def _call(self, name, raw_args, allowed):
        if name == "handoff":
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: handoff is only available in team mode."
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except Exception as exc:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: invalid tool arguments JSON (%s). Re-issue the call with valid JSON." % exc
        if name not in allowed or name not in ALL_TOOL_NAMES:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: unknown or not-allowed tool %r. Your tools: %s" % (name, ", ".join(sorted(allowed)))
        missing = [k for k in next(t for t in ALL_TOOLS if t["function"]["name"] == name)["function"]["parameters"]["required"]
                   if not isinstance(args.get(k), str)]
        if missing:
            self.stats["invalid_tool_calls"] += 1
            return "ERROR: missing or non-string argument(s): %s" % ", ".join(missing)
        self.stats["valid_tool_calls"] += 1
        self.stats["by_tool"][name] = self.stats["by_tool"].get(name, 0) + 1  # attempts, incl. rejected
        if name == "run":
            why = write_violation(args["command"])
            if why:
                self.stats["protocol_rejections"]["run_write_blocked"] += 1
                self.sb.log("run_blocked", args["command"], None, "", why)
                return "REJECTED: run is read-only (%s). Use edit_file to change files; scratch output may go to /tmp." % why
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
            if not lines:
                self.read_paths.add(p)
                return "%s (empty file)" % p
            def as_int(v, default):
                if isinstance(v, bool):
                    return default
                if isinstance(v, int):
                    return v
                if isinstance(v, str) and v.strip().isdigit():
                    return int(v)
                return default
            s = max(1, as_int(args.get("start_line"), 1))
            e = as_int(args.get("end_line"), None)
            e = min(len(lines), s + 399) if e is None or e == 0 else e
            if e < s:
                return "ERROR: end_line must be >= start_line"
            if lines and s > len(lines):
                return "ERROR: start_line %d is past the end of the file (%d lines)" % (s, len(lines))
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
    saved = compact(slot.messages, getattr(slot, "budget_chars", 120000))
    if saved:
        stats["compactions"] += 1
        stats["compacted_chars"] += saved
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
        if slot.episode.done:
            res = "SKIPPED: the episode already ended in this turn."
        else:
            res = slot.episode.call(f.get("name"), f.get("arguments", "{}"))
        slot.messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": f.get("name"), "content": res})
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
                                     "finish_no_edit": 0, "finish_tests_failed": 0, "run_write_blocked": 0},
             "compactions": 0, "compacted_chars": 0}
    started = time.time()
    deadline = started + args.agent_timeout
    client = None
    episode = Episode(Sandbox(container, os.path.join(run_dir, "exec.jsonl")), test_files, stats)
    slot = AgentSlot("solo", SYSTEM, episode)
    slot.budget_chars = getattr(args, "compact_chars", 120000)
    slot.wake(prompt)
    try:
        client = Client(args.driver, args.openai_base, args.model, args.max_tokens_per_turn,
                        key_cmd=args.key_helper, num_ctx=args.num_ctx)
        while stats["turns"] < args.max_turns and time.time() < deadline:
            checkpoint()
            if not step(client, slot, stats, deadline, args.text_fallback, os.path.join(run_dir, "agent.jsonl")):
                break
        result["agent_timeout"] = time.time() >= deadline
        if not episode.done:
            if result["agent_timeout"]:
                result["guard_stop"] = "agent_timeout"
            elif stats["turns"] >= args.max_turns:
                result["guard_stop"] = "max_turns"
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
        result["guards"] = {"max_turns": args.max_turns, "agent_timeout_sec": args.agent_timeout}
        result.setdefault("guard_stop", None)
        result["timings"]["agent_sec"] = round(time.time() - started, 3)
        result["model_reported"] = stats.pop("model_reported", None)
        result["llm_stats"] = stats
        result["exec_calls"] = _count_logged(os.path.join(run_dir, "exec.jsonl"))


# ---------------------------------------------------------------------------
# Team mode (MCA 2-agent): investigator + editor, one shared client, one shared container.

INVESTIGATOR_SYSTEM = (
    "You are the INVESTIGATOR on a two-agent team fixing a bug in the Python repository at /testbed "
    "(Linux container, no network). Your teammate is the EDITOR, who makes the code change.\n"
    "Your tools: read_file, run (read-only: no redirects, sed -i, mv, cp, tee or scripts that write files), handoff.\n"
    "You cannot edit files or finish.\n"
    "1. Find the root cause: locate the exact file, function and lines.\n"
    "2. Then call handoff with a self-contained message: files and line numbers, root cause, the minimal change "
    "you propose (exact old and new code if you can), and how to verify it.\n"
    "Be efficient. If the editor hands back a question, answer it and hand off again."
)

EDITOR_SYSTEM = (
    "You are the EDITOR on a two-agent team fixing a bug in the Python repository at /testbed "
    "(Linux container, no network). The INVESTIGATOR has studied the bug and hands you findings.\n"
    "Your tools: read_file, edit_file, run (read-only), finish, handoff.\n"
    "Protocol: read_file a file before edit_file on it; edit_file replaces an exact, unique snippet; "
    "make a minimal source fix; do not modify tests; verify with run (e.g. a quick python check or pytest).\n"
    "Call finish when done. The harness runs the existing tests; finish is rejected if they fail.\n"
    "Only if the findings are wrong or insufficient, call handoff with a specific question for the investigator."
)

ROLE_TOOLS = {
    "investigator": ("read_file", "run", "handoff"),
    "editor": ("read_file", "edit_file", "run", "finish", "handoff"),
}


def new_stats(backend):
    return {"backend": backend, "turns": 0, "llm_requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "llm_sec": 0.0, "valid_tool_calls": 0, "invalid_tool_calls": 0, "fallback_tool_calls": 0,
            "no_tool_turns": 0, "parked_skips": 0, "by_tool": {}, "rate_limited": False, "llm_error": None,
            "protocol_rejections": {"edit_unread": 0, "edit_tests": 0, "edit_not_unique": 0,
                                    "finish_no_edit": 0, "finish_tests_failed": 0, "run_write_blocked": 0},
            "compactions": 0, "compacted_chars": 0, "cost_usd": 0.0,
            "handoffs_sent": 0, "handoffs_rejected": 0, "parked_sec": 0.0}


class TeamAgent(AgentSlot):
    def __init__(self, name, system, episode, backend, max_tokens, budget_chars):
        AgentSlot.__init__(self, name, system, episode)
        self.tool_names = frozenset(ROLE_TOOLS[name])
        self.tools = [t for t in ALL_TOOLS if t["function"]["name"] in self.tool_names]
        self.stats = new_stats(backend)
        self.read_paths = set()
        self.max_tokens = max_tokens
        self.budget_chars = budget_chars
        self.parked_since = time.time()
        self.woken_once = False

    def wake(self, message):
        if self.parked and self.parked_since is not None:
            self.stats["parked_sec"] += time.time() - self.parked_since
        self.parked_since = None
        self.woken_once = True
        AgentSlot.wake(self, message)

    def park(self):
        if not self.parked:
            self.parked_since = time.time()
        AgentSlot.park(self)

    def close(self):
        if self.parked and self.parked_since is not None:
            self.stats["parked_sec"] += time.time() - self.parked_since
            self.parked_since = None
        self.stats["parked_sec"] = round(self.stats["parked_sec"], 3)


def team_step(client, agent, deadline, agent_log, max_handoffs, team, text_fallback=False):
    """One LLM turn for the active agent. Returns ("continue"|"handoff"|"done", handoff_message)."""
    st = agent.stats
    if agent.parked:  # never call the model for a parked agent
        st["parked_skips"] += 1
        return "continue", None
    saved = compact(agent.messages, agent.budget_chars)
    if saved:
        st["compactions"] += 1
        st["compacted_chars"] += saved
    t = time.time()
    msg, usage, model = client.chat(agent.messages, timeout=max(10, min(300, deadline - time.time())),
                                    tools=agent.tools, max_tokens=agent.max_tokens)
    st["llm_sec"] += time.time() - t
    st["turns"] += 1
    st["llm_requests"] += 1
    st["prompt_tokens"] += usage.get("prompt_tokens") or 0
    st["completion_tokens"] += usage.get("completion_tokens") or 0
    st["cost_usd"] = round(st["cost_usd"] + float(usage.get("cost") or 0), 6)
    team["model_reported"] = team.get("model_reported") or model
    with open(agent_log, "a", encoding="utf-8") as h:
        h.write(json.dumps({"ts": time.time(), "agent": agent.name, "usage": usage, "message": msg}) + "\n")
    calls = msg.get("tool_calls") or []
    if not calls and text_fallback:
        calls = [c for c in text_fallback_calls(msg.get("content")) if c["function"]["name"] in agent.tool_names]
        st["fallback_tool_calls"] += len(calls)
    agent.messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
    if not calls:
        st["no_tool_turns"] += 1
        agent.messages.append({"role": "user", "content": "No tool call received. Use one of your tools."})
        return "continue", None
    outcome, handoff_msg = "continue", None
    for c in calls:
        f = c.get("function") or {}
        name = f.get("name")
        if outcome != "continue":
            res = "SKIPPED: an earlier call in this turn ended your turn."
        elif name == "handoff" and "handoff" in agent.tool_names:
            try:
                a = json.loads(f.get("arguments") or "{}")
                text = a.get("message") if isinstance(a, dict) else None
            except ValueError:
                text = None
            if not isinstance(text, str) or not text.strip():
                st["invalid_tool_calls"] += 1
                res = "ERROR: handoff needs a non-empty string message."
            elif agent.name == "editor" and team["handoffs"] >= max_handoffs:
                # Only the editor can finish, so the cap only blocks handing back to the investigator;
                # the investigator can always hand the work to the editor.
                st["handoffs_rejected"] += 1
                st["valid_tool_calls"] += 1
                res = "REJECTED: handoff limit (%d) reached; finish the fix yourself." % max_handoffs
            else:
                st["valid_tool_calls"] += 1
                st["by_tool"]["handoff"] = st["by_tool"].get("handoff", 0) + 1
                st["handoffs_sent"] += 1
                team["handoffs"] += 1
                outcome, handoff_msg = "handoff", text.strip()
                res = "OK: handed off. You are parked until your teammate hands back."
        else:
            res = agent.episode.call(name, f.get("arguments", "{}"), agent=agent)
            if agent.episode.done:
                outcome = "done"
        agent.messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": name, "content": res})
    return outcome, handoff_msg


def _sum_stats(parts, backend):
    tot = new_stats(backend)
    for st in parts:
        for k, v in st.items():
            if isinstance(v, bool) or k in ("backend", "llm_error"):
                continue
            if isinstance(v, (int, float)) and k in tot:
                tot[k] = round(tot[k] + v, 6) if isinstance(v, float) else tot[k] + v
            elif isinstance(v, dict) and k in tot:
                for kk, vv in v.items():
                    tot[k][kk] = tot[k].get(kk, 0) + vv
        tot["rate_limited"] = tot["rate_limited"] or st.get("rate_limited", False)
        tot["llm_error"] = tot["llm_error"] or st.get("llm_error")
    return tot


def run_team(args, prompt, container, run_dir, result, test_files, checkpoint):
    started = time.time()
    deadline = started + args.agent_timeout
    agent_log = os.path.join(run_dir, "agent.jsonl")
    shared = new_stats(args.driver)  # episode-level stats (visible-test runs share the episode)
    episode = Episode(Sandbox(container, os.path.join(run_dir, "exec.jsonl")), test_files, shared)
    inv = TeamAgent("investigator", INVESTIGATOR_SYSTEM, episode, args.driver,
                    args.investigator_max_tokens, args.compact_chars)
    ed = TeamAgent("editor", EDITOR_SYSTEM, episode, args.driver, args.editor_max_tokens, args.compact_chars)
    team = {"handoffs": 0, "model_reported": None, "turn_limit": args.max_turns, "events": []}
    client = None
    active, other = inv, ed
    inv.wake(prompt.replace("Use the tools read_file, edit_file, run and finish.\n",
                            "Use your tools read_file, run and handoff.\n").replace(
        "Call finish when the fix is done.\n", "Call handoff to the editor when you know the fix.\n"))
    inv_nudged = False
    # Wake the editor no later than half the pooled turn limit, even if --max-turns is small.
    force_at = max(1, min(args.investigator_turn_budget + args.investigator_grace_turns, args.max_turns // 2))
    nudge_at = max(1, min(args.investigator_turn_budget, force_at - 1))
    stop_reason = "turn_limit"
    try:
        client = Client(args.driver, args.openai_base, args.model, args.max_tokens_per_turn,
                        key_cmd=args.key_helper, num_ctx=args.num_ctx)
        while inv.stats["turns"] + ed.stats["turns"] < args.max_turns:
            if time.time() >= deadline:
                stop_reason = "timeout"
                break
            checkpoint()
            if (active is inv and not inv_nudged and not ed.woken_once
                    and inv.stats["turns"] >= nudge_at):
                inv.messages.append({"role": "user", "content":
                                     "Investigation turn budget reached. Call handoff now with your best findings."})
                inv_nudged = True
            outcome, text = team_step(client, active, deadline, agent_log, args.max_handoffs, team, args.text_fallback)
            if (outcome == "continue" and active is inv and not ed.woken_once
                    and inv.stats["turns"] >= force_at):
                # Hard cap: the investigator ignored the nudge. Hand off its own notes so the
                # editor can still act; counted as a forced handoff, not an agent handoff.
                notes = [m.get("content") for m in inv.messages if m.get("role") == "assistant" and m.get("content")]
                cmds = []
                for m in inv.messages:
                    for c in m.get("tool_calls") or []:
                        cmds.append("%s %s" % ((c.get("function") or {}).get("name"), (c.get("function") or {}).get("arguments", "")[:160]))
                text = ("[forced handoff: investigator turn cap reached]\nInvestigator notes:\n%s\n\nInvestigator's last tool calls:\n%s"
                        % (clip("\n".join(notes[-3:]), 3000) or "(none)", "\n".join(cmds[-8:])))
                team["forced_handoffs"] = team.get("forced_handoffs", 0) + 1
                outcome = "handoff"
            if outcome == "done":
                stop_reason = "finish_accepted" if episode.tests_passed else "finish_rejected_final"
                break
            if outcome == "handoff":
                header = "Handoff from the %s:\n%s" % (active.name, text)
                if not other.woken_once and other is ed:
                    header = "TASK:\n" + prompt + "\n\n" + header
                team["events"].append({"t": round(time.time() - started, 2), "from": active.name,
                                       "to": other.name, "chars": len(text)})
                active.park()
                other.wake(header)
                active, other = other, active
    except RateLimited:
        active.stats["rate_limited"] = True
        active.stats["llm_error"] = "HTTP 429"
        stop_reason = "rate_limited"
    except LLMError as exc:
        active.stats["llm_error"] = str(exc).split(":", 1)[0][:80]
        stop_reason = "llm_error"
    finally:
        inv.close()
        ed.close()
        per_agent = {}
        for a in (inv, ed):
            st = a.stats
            st["llm_sec"] = round(st["llm_sec"], 3)
            tot = st["valid_tool_calls"] + st["invalid_tool_calls"]
            st["tool_call_success_rate"] = round(st["valid_tool_calls"] / tot, 3) if tot else None
            st["max_tokens_per_turn"] = a.max_tokens
            per_agent[a.name] = st
        team_stats = _sum_stats(list(per_agent.values()), args.driver)
        tot = team_stats["valid_tool_calls"] + team_stats["invalid_tool_calls"]
        team_stats["tool_call_success_rate"] = round(team_stats["valid_tool_calls"] / tot, 3) if tot else None
        team_stats["llm_requests"] = client.requests if client else 0
        team_stats["rate_limit_hits"] = client.rate_limit_hits if client else 0
        team_stats["handoffs"] = team["handoffs"]
        team_stats["forced_handoffs"] = team.get("forced_handoffs", 0)
        team_stats["handoff_events"] = team["events"]
        team_stats["finish_accepted"] = episode.done and bool(episode.tests_passed)
        team_stats["edits"] = episode.edits
        team_stats["stop_reason"] = stop_reason
        team_stats["turn_limit_pooled"] = args.max_turns
        result["guards"] = {"max_turns": args.max_turns, "agent_timeout_sec": args.agent_timeout}
        result["guard_stop"] = {"turn_limit": "max_turns", "timeout": "agent_timeout"}.get(stop_reason)
        result["agent_timeout"] = stop_reason == "timeout"
        result["layout"] = "team2"
        result["agents"] = per_agent
        result["timings"]["agent_sec"] = round(time.time() - started, 3)
        result["model_reported"] = team.get("model_reported")
        result["llm_stats"] = team_stats
        result["exec_calls"] = _count_logged(os.path.join(run_dir, "exec.jsonl"))
