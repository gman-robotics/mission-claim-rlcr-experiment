# GMA-392 session

Step 1 of the experiment plan: one SWE-bench Verified task, one container, a clear pass or fail. This is a smoke test of the session harness. It is not the Mission-Claim team, and it is not the three-arm comparison.

Task: `pallets__flask-5014` (Flask blueprint empty-name check). Image: `swebench/sweb.eval.x86_64.pallets_1776_flask-5014:latest`.

## Design

The agent does not get a host shell. Claude's working directory is a temp dir outside this repo. Settings set `disableAllHooks` and deny `Bash`. The process is started with `--strict-mcp-config`, `--permission-mode dontAsk`, and `--disallowedTools` covering Bash, Edit, Write, Read, Glob, Grep, WebFetch, WebSearch, NotebookEdit, Task, Agent, Skill, and TodoWrite. The only allowed tool is `mcp__gma392__exec`.

`harness/exec_mcp.py` is a newline-delimited JSON-RPC MCP server. `exec` runs this argument list, with the script as one argument to `bash -lc` inside the container:

`docker exec -w /testbed $GMA392_CONTAINER bash -lc <script>`

There is no host shell in that path. Each call is appended to `GMA392_EXEC_LOG`. Stdout and stderr returned to the model are truncated to about 20k characters each. `timeout_sec` is capped at 600.

The work container is `docker run -d --platform linux/amd64 --network none --memory 4g --entrypoint sleep -v RUNDIR:/logs IMAGE infinity`. After start, `docker inspect` must show exactly one mount, type `bind`, destination `/logs`. Any other mount aborts with `mounts_invalid`. The inspect document is `mounts.json`.

The image is pulled with `--platform linux/amd64` before the agent timer starts. `RepoDigests` are stored on the result as `image_digest`.

The patch is taken from the work container with `git add -A` and `git diff --cached <base_commit>` (argument lists, `safe.directory=/testbed`). Gold skips the agent and uses the instance `patch` as `model.patch`. Files touched by `test_patch` are removed by splitting on `diff --git` lines. The filtered patch is `model.filtered.patch`.

The work container is removed. A new container (`<name>-eval`) is started from the same image and the same mount rule. The filtered patch is applied (an empty patch is allowed), then `test_patch`. Apply failures are `model_patch_apply_failed` and `test_patch_apply_failed`. Pytest runs inside the eval container:

`source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && cd /testbed && timeout 900 python -m pytest -rA <ids>`

Only the `short test summary info` section is scored. A missing header is `pytest_did_not_report`. Every FAIL_TO_PASS and PASS_TO_PASS id is mapped to PASSED, FAILED, ERROR, or another pytest status, or MISSING. `resolved` means every id is PASSED. Anything else that still produced a summary is `unresolved`. Agent mode with zero exec calls that actually ran (`rc` is an integer in `exec.jsonl`) is `no_agent_commands` and does not eval.

A SIGTERM or SIGINT handler kills the child process group and the `finally` path removes containers. `result.json` is written only after that removal. `containers_removed` is true only when `docker ps -a --filter name=gma392-` no longer lists those names.

Gold is the gate that the image, mount check, patch apply, and pytest parser work. Canary checks that the agent cannot create `/tmp/gma392-host-canary` on the host. The file is removed before the attempt. Canary does not eval. `canary.json` records `passed`.

## How to run

Docker Desktop on the laptop. Repo root. Do not point this at MCA or a second arm.

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

Stdout prints the result path and status. Logs default to `/Users/Shared/gman-sandbox/mission-claim-rlcr/runs`. Useful flags: `--agent-timeout` (default 1200), `--max-turns` (default 40), `--claude-bin` (default `/opt/homebrew/bin/claude`).

## Reap leftover containers

Names start with `gma392-`.

```bash
docker ps -a --filter name=gma392-
docker rm -f CONTAINER
```

Repeat `docker rm -f` for each name, including any `...-eval` container. Confirm with `docker ps -a --filter name=gma392-` that the list is empty.

## Evidence

Runs on Tom's Mac, Docker Desktop 29.8.1, 2026-10-23 (MDT). Run directories are under `/Users/Shared/gman-sandbox/mission-claim-rlcr/runs/`.

- Image: `swebench/sweb.eval.x86_64.pallets_1776_flask-5014@sha256:bde4fbdafa36141d4396944da88ec37aabdef612ed36920090675bccd0ca5d72`
- Gold gate: `pallets__flask-5014-gold-20261023T235517Z` gives `resolved`. F2P 1/1 PASSED. P2P 59/59 PASSED.
- Canary: `pallets__flask-5014-canary-20261023T235530Z` gives `passed: true`. `/tmp/gma392-host-canary` was absent. The agent tool list had no `Bash`.
- Agent: `pallets__flask-5014-agent-20261023T235555Z` gives `resolved`. Model `claude-haiku-5-5` (requested `haiku`, Claude Code 2.1.309, Tom's local auth). 3 exec calls. No timeout. F2P 1/1 PASSED. P2P 59/59 PASSED. Patch: empty-name `ValueError` in `src/flask/blueprints.py`.
- Mounts: each container had one bind mount, the run dir at `/logs`. No containers remained after the runs.
