"""Unit tests for the GMA-393 baseline gaps and hang guards. Run: python3 -m unittest discover -s tests"""
import json, os, random, sys, types, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "harness"))
import lesser_driver as ld  # noqa: E402
import run_single_agent as rsa  # noqa: E402


class WriteGuard(unittest.TestCase):
    def blocked(self, cmd):
        self.assertIsNotNone(ld.write_violation(cmd), cmd)

    def allowed(self, cmd):
        self.assertIsNone(ld.write_violation(cmd), cmd)

    def test_redirect_in_os_system(self):
        self.blocked('''python -c "import os; os.system('echo x > /testbed/a.py')"''')
        self.blocked("""python3 -c 'import os; os.system("cat b >> src/m.py")'""")

    def test_redirect_in_python_c_subprocess_and_sh_c(self):
        self.blocked('''python -c "import subprocess; subprocess.run('echo 1 > f.py', shell=True)"''')
        self.blocked("bash -c 'echo hi > /testbed/x.py'")

    def test_sed_in_place(self):
        for c in ("sed -i s/a/b/ f.py", "sed --in-place s/a/b/ f.py", "sed -n -i.bak 's/a/b/' f.py",
                  "sed --in-place=.bak 's/a/b/' f.py", "sed -e 's/a/b/' -i f.py"):
            self.blocked(c)

    def test_read_only_allowed(self):
        for c in ('python -c "print(1 > 0)"', "sed -n 1,20p f.py", "grep -n foo src > /dev/null",
                  "pytest -q tests 2>&1 | tail", "echo x > /tmp/scratch.txt"):
            self.allowed(c)


class TruncatedIds(unittest.TestCase):
    def test_truncated_param_does_not_match_longer_sibling(self):
        found = {"t.py::test_x[a-b c]": "FAILED", "t.py::test_x[a-b c d]": "PASSED"}
        self.assertEqual(rsa.map_ids(["t.py::test_x[a-b"], found)["t.py::test_x[a-b"], rsa.AMBIGUOUS)

    def test_unique_continuation_ok(self):
        found = {"t.py::test_x[a b]": "PASSED", "t.py::test_y[z]": "FAILED"}
        self.assertEqual(rsa.map_ids(["t.py::test_x[a"], found)["t.py::test_x[a"], "PASSED")

    def test_plain_name_not_prefix_of_other_name(self):
        found = {"t.py::test_x_attrs": "PASSED"}
        self.assertEqual(rsa.map_ids(["t.py::test_x"], found)["t.py::test_x"], "MISSING")

    def test_ambiguous_fails_gate(self):
        self.assertFalse(rsa.all_passed({"a": rsa.AMBIGUOUS}, {}))


class VisibleFiles(unittest.TestCase):
    def test_artifact_ids_are_not_files(self):
        self.assertEqual(rsa.visible_test_files(["a.py::t", "[100%]", "b.py::u[x]", "a.py::v"]), ["a.py", "b.py"])


class Compaction(unittest.TestCase):
    def history(self, rng, n):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "T" * rng.randint(10, 20000)}]
        for i in range(n):
            msgs.append({"role": "assistant", "content": "a" * rng.randint(0, 3000),
                         "tool_calls": [{"id": str(i), "type": "function",
                                         "function": {"name": "edit_file", "arguments": json.dumps({"x": "y" * rng.randint(0, 9000)})}}]})
            msgs.append({"role": "tool", "tool_call_id": str(i), "content": "r" * rng.randint(0, 30000)})
        if rng.random() < 0.5:
            msgs.append({"role": "user", "content": "H" * rng.randint(0, 40000)})
        return msgs

    def test_never_over_budget(self):
        rng = random.Random(7)
        for _ in range(300):
            msgs = self.history(rng, rng.randint(0, 12))
            budget = rng.randint(400, 60000)
            ld.compact(msgs, budget)
            self.assertLessEqual(sum(ld._msize(m) for m in msgs), budget)
            ids = {c["id"] for m in msgs for c in (m.get("tool_calls") or [])}
            for m in msgs:
                if m.get("role") == "tool":
                    self.assertIn(m["tool_call_id"], ids)

    def test_huge_last_two_messages(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t" * 50000},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
                 "function": {"name": "run", "arguments": "{\"command\": \"%s\"}" % ("z" * 40000)}}]},
                {"role": "tool", "tool_call_id": "1", "content": "o" * 90000}]
        ld.compact(msgs, 1000)
        self.assertLessEqual(sum(ld._msize(m) for m in msgs), 1000)


class Guards(unittest.TestCase):
    def test_defaults_match_plan(self):
        a = rsa.parse_args(["--instance", "x.json"])
        self.assertEqual((a.max_turns, a.agent_timeout), (200, 14400))

    def _fake(self, monkey_step_true=True):
        class FakeClient:
            requests = 0
            rate_limit_hits = 0
            def __init__(self, *a, **k): pass
        return FakeClient

    def test_solo_max_turns_guard_stop(self):
        orig_client, orig_step, orig_sandbox = ld.Client, ld.step, ld.Sandbox
        ld.Client = self._fake()
        ld.Sandbox = lambda *a, **k: None

        def fake_step(client, slot, stats, *a, **k):
            stats["turns"] += 1
            return True
        ld.step = fake_step
        try:
            args = types.SimpleNamespace(agent_timeout=60, max_turns=3, driver="openrouter", openai_base=None,
                                         model="m", max_tokens_per_turn=10, key_helper="true", num_ctx=1,
                                         text_fallback=False, compact_chars=1000)
            result = {"timings": {}}
            ld.run(args, "p", "c", "/tmp", result, [], lambda: None)
        finally:
            ld.Client, ld.step, ld.Sandbox = orig_client, orig_step, orig_sandbox
        self.assertEqual(result["guard_stop"], "max_turns")
        self.assertEqual(result["guards"], {"max_turns": 3, "agent_timeout_sec": 60})

    def _team(self, step_fn, checkpoint=lambda: None):
        orig = ld.Client, ld.team_step, ld.Sandbox
        ld.Client, ld.team_step, ld.Sandbox = self._fake(), step_fn, (lambda *a, **k: None)
        args = types.SimpleNamespace(agent_timeout=60, max_turns=4, driver="openrouter", openai_base=None,
                                     model="m", max_tokens_per_turn=10, key_helper="true", num_ctx=1,
                                     text_fallback=False, compact_chars=1000, investigator_max_tokens=10,
                                     editor_max_tokens=10, max_handoffs=6, investigator_turn_budget=12,
                                     investigator_grace_turns=3)
        result = {"timings": {}}
        try:
            ld.run_team(args, "p", "c", "/tmp", result, [], checkpoint)
        except KeyboardInterrupt:
            pass
        finally:
            ld.Client, ld.team_step, ld.Sandbox = orig
        return result

    def test_team_max_turns_guard_stop(self):
        def step(client, agent, *a, **k):
            agent.stats["turns"] += 1
            return "continue", ""
        r = self._team(step)
        self.assertEqual(r["guard_stop"], "max_turns")
        self.assertEqual(r["guards"], {"max_turns": 4, "agent_timeout_sec": 60})

    def test_team_interrupt_is_not_guard_stop(self):
        def boom():
            raise KeyboardInterrupt
        r = self._team(lambda *a, **k: ("continue", ""), checkpoint=boom)
        self.assertIsNone(r["guard_stop"])
        self.assertEqual(r["llm_stats"]["stop_reason"], "interrupted")


if __name__ == "__main__":
    unittest.main()
