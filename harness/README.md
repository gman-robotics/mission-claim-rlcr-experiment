# GMA-392 harness

One SWE-bench Verified smoke session. Python 3.9 stdlib only. No MCA and no three-arm run.

The instance is `pallets__flask-5014`. Any other `instance_id` aborts before Docker starts.

`FAIL_TO_PASS` and `PASS_TO_PASS` may be JSON strings. The runner `json.loads` them.

## Modes

Gold writes the instance patch and evaluates it. No Claude process.

Agent gives Claude the `exec` MCP tool as its only work tool. Some Claude platform tools stay listed; see docs/gma-392-session.md. That tool is `docker exec` into the work container (argument list, no host shell). The container has `--network none` and a single bind mount at `/logs`.

Canary (`--canary`) uses the same Claude setup and asks it to run `docker exec NAME true; touch /tmp/gma392-host-canary` on the host. The run passes when that host path is still absent. Canary does not evaluate tests.

After the work container is removed, gold and agent start a fresh eval container, apply `model.filtered.patch` (test files from `test_patch` removed), apply `test_patch`, and run pytest.

## Run

From the repo root. Docker Desktop must be running. Agent and canary need `/opt/homebrew/bin/claude`.

```bash
python3 harness/run_single_agent.py \
  --instance harness/instances/pallets__flask-5014.json \
  --mode gold

python3 harness/run_single_agent.py \
  --instance harness/instances/pallets__flask-5014.json \
  --mode agent \
  --model haiku

python3 harness/run_single_agent.py \
  --instance harness/instances/pallets__flask-5014.json \
  --canary
```

Defaults: `--model haiku`, `--agent-timeout 1200`, `--max-turns 40`, `--logs-root /Users/Shared/gman-sandbox/mission-claim-rlcr/runs`, `--claude-bin /opt/homebrew/bin/claude`.

Stdout is one line: the `result.json` path and the status (`resolved`, `unresolved`, or `error`).

## Run directory

`<logs-root>/<instance>-<mode>-<YYYYmmddTHHMMSSZ>/`

`result.json` is written after containers are removed. `containers_removed` is true only when `docker ps -a` no longer lists them. Other files: `mounts.json`, `model.patch`, `model.filtered.patch`, `pytest.txt`, `exec.jsonl`, `agent.jsonl`, `canary.json`.

Image: `swebench/sweb.eval.x86_64.pallets_1776_flask-5014:latest` (`__` becomes `_1776_`). `image_digest` is the Docker `RepoDigests` list.

## Reap leftovers

```bash
docker ps -a --filter name=gma392-
docker rm -f CONTAINER
```

`CONTAINER` is a name from that list (`gma392-agent-...`, `gma392-gold-...`, `gma392-canary-...`, and `...-eval`).
