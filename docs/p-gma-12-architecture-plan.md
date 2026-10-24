# P-GMA-12 architecture plan (plan-only)

> Plan-only. Produced 2026-10-23 by headless Claude Code with pstack /poteto-mode, /architect, /interrogate, /blast-radius, /show-me-your-work (session 58c097a3-6d1b-4d3a-83e9-b3f3acf4e5dd). No implementation. Mirrors the Linear doc on project P-GMA-12.

Scope is GMA-393 (MCA command layer on the same SWE session), GMA-394 (three-arm run on a small task set) and GMA-395 (collective-advantage RLCR loop). Nothing is built yet.

Sources:
- `docs/mission-claim-rlcr-experiment-plan.md` (blob c5d27aa3), cited as plan:N.
- The wiki project page, cited as wiki:N.
- The wiki concept pages, cited by short name:
  - `mission-claim-protocol-messages`
  - `mission-claim-command-and-flow`
  - `decentralized-command-in-agent-orchestration`
  - `rlcr-and-mission-claim`
  - `mission-claim-poc`
  - `mission-claim-anchors-and-workflow-gates`
- The GMA-392 harness:
  - `harness/run_single_agent.py`
  - `harness/exec_mcp.py`
  - `docs/gma-392-session.md`, cited as session:N
- The wiki says "implementation not started" (wiki:20). That is stale, since GMA-392 is merged.

The `agent-swarm` and `agent-council` repos are Rust. I saw file lists only and took no reuse from them.

## 1. Q1 to Q5

| Q | Answer | Tag |
|---|---|---|
| Q1 budget | Arms 1 and 2 get "the same token or wall-clock budget" (plan:15). No number is given. Default is a total wall-clock cap of 1200 s per arm per task, pooled across all team agents (single-agent default `--agent-timeout 1200`, `run_single_agent.py:309`). A separate cap of 40 orchestrator turns bounds the team, and arm 2 gets the same 40 model turns. This is distinct from the per-invocation `--max-turns` (`:310`). Tokens are recorded, not enforced. | plan rule, proposed default |
| Q2 reuse | The plan says "reuse the MCA PoC scaffolding where it exists" (plan:29). The PoC's `command/`, `squad/` and `protocol/` are empty (poc:136-141). There is no code to import. What exists is the Rust message vocabulary (protocol-messages:27-142) and the typed `Evidence` (:148-185). Those get ported to Python dataclasses. | plan rule, proposed default |
| Q3 messages and board | The plan names the messages and "one message board" (plan:13). Field shapes come from the wiki, not the plan. Board default is an append-only JSONL file on the host, owned by the orchestrator. `Claim` is new, since the wiki has `SquadBid` plus a `BidAccepted` event but no `Claim` type. | plan names, proposed default |
| Q4 team | The plan gives one Strategic Commander plus "two to four" squads, all on the same lesser model (plan:13). Default is 1 commander and 2 squads, one agent per squad, sequential turns, haiku (GMA-392 reported `claude-haiku-5-5`, session:72). Squads are role-neutral. They share one base prompt and differ only in `squad_id`. Roles are read off their bids, not assigned (see open question 9). Concurrency is not in the plan. | plan range, proposed default |
| Q5 done | Study level (plan:41):<br>• The team beats the single lesser agent.<br>• After training the gap grows or closes on the frontier control.<br>• Role emergence, peer-review frequency and Leadership Capital usage are logged.<br>Per ticket (proposed):<br>• GMA-393 is done when one MCA run on `pallets__flask-5014` ends with `board.jsonl`, `result.json`, an eval and `containers_removed: true`. Resolving is not required.<br>• GMA-394 is done when N tasks × 3 arms × K repeats have result records and blind judge notes.<br>• GMA-395 is done only after the gate in section 7. | plan study level, proposed per ticket |

**Leadership Capital.** The plan only says to log its usage (plan:41). The wiki defines it in four places.
- Commanders spend it only on conflicts or escalation thresholds (command-and-flow:41).
- The sketch intervenes only while `capital > 5` and costs 1 per intervention (decentralized-command:79-87).
- Capital writeback happens only at anchor-terminal states (anchors P1.6, line 85). It must not substitute for anchors (anchors:44).
- Under RLCR it is an environment affordance, not a reward head (rlcr-and-mission-claim:46).

What that means for the logs:
- Arm 1 keeps a `CapitalLedger`. Each entry has an event, a cost and the balance after. The start is 10 (arbitrary, proposed). One unit is spent per claim-conflict arbitration, and arbitration is allowed only while the balance is above 5.
- The ledger is a diagnostic. It never enters `reward`. Arms 2 and 3 record `null`.
- Real runs may have zero conflicts, so the ledger can legitimately be empty. Record `claim_conflicts: 0` instead of treating the absence as a bug.
- A post-eval writeback is computed and logged, never fed to the reward.

## 2. Modules, types, interfaces

Everything lives alongside `harness/` in Python 3.9 stdlib. Bodies are `...`. The union syntax below is pseudocode.

```
harness/
  session.py          # extracted from run_single_agent.py
  runtime.py          # AgentRuntime: ClaudeCliRuntime, ScriptedRuntime, ApiRuntime
  mca/protocol.py     # message types + parse/validate
  mca/board.py        # Board
  mca/capital.py      # CapitalLedger
  mca/orchestrator.py # run_team
  arms.py             # run_arm for MCA, SINGLE, FRONTIER
  judge.py            # blind_packet, judge
  results.py          # ArmResult schema + writer + summary
  instances/allowlist.json
  run_experiment.py   # CLI for the matrix
  run_single_agent.py # thin CLI over session.py, still working
```

```python
# session.py. One Docker session, shared by all three arms.
@dataclass(frozen=True)
class Instance: instance_id; base_commit; problem_statement; test_patch; patch; f2p; p2p
class Session:                                   # context manager; always removes containers
    def open(instance, run_dir, name, prefix) -> "Session": ...  # pull, run, assert one /logs bind
    def exec(script, timeout_sec) -> ExecResult: ...             # docker exec argv, logs to exec.jsonl
    def capture_patch() -> str: ...
    def evaluate(patch) -> EvalResult: ...                       # removes work box, starts -eval box
# runtime.py
@dataclass(frozen=True)
class TurnRequest: role; system; prompt; tools: tuple[str, ...]; timeout_sec; session
@dataclass(frozen=True)
class TurnResult: final_text; exec_calls; wall_sec; tokens_in; tokens_out; model_reported; raw_path
class AgentRuntime(Protocol):
    name: str; credit_source: str
    def run_turn(req: TurnRequest) -> TurnResult: ...
# mca/protocol.py
Verdict = Pass | Fail | Partial
EvidenceKind = Observational | AgentClaim | PeerOpinion
Evidence(kind, exec_seq: Optional[int], summary)          # Observational points at an exec.jsonl line
MissionIntent(mission_id, parent_id, objective, end_state, constraints, isolation=False, wall_budget_sec)
Bid(mission_id, squad_id, confidence, estimated_turns, rationale)
Claim(mission_id, squad_id, snapshot_ref)                 # snapshot_ref is a git diff hash
PeerVerification(mission_id, reviewer_squad, verdict, disagreements, evidence)
OwnershipReport(mission_id, squad_id, verdict, evidence, corrective_actions)
Envelope(seq, version, sender, kind, ts, body, notes)     # notes feed that sender's own next turn
def parse(text: str) -> Message: ...                      # the only door in; rejects unknown or missing fields
# mca/board.py
class Board:                                              # JSONL on the host, flock'd, seq monotonic
    def append(sender, msg) -> Envelope: ...
    def read(since=0) -> list[Envelope]: ...
    def render(for_squad) -> str: ...
# mca/orchestrator.py
Phase = INTENT -> BIDDING -> CLAIMED -> WORKING -> VERIFYING -> REPORTING -> DONE | FAILED
def run_team(session, runtime, cfg: TeamConfig, instance) -> TeamOutcome: ...
# arms.py, judge.py, results.py
class Arm(Enum): MCA; SINGLE; FRONTIER
def run_arm(arm, instance, runtimes, budget, logs_root) -> ArmResult: ...
def blind_packet(results) -> list[BlindEntry]: ...        # shuffled, opaque ids, model names scrubbed
def judge(entries, runtime) -> list[JudgeNotes]: ...
ArmResult(schema_version, instance_id, arm, model_requested, model_reported, runtime_name, credit_source,
          status, reward: int, f2p, p2p, timings, turns, exec_calls, tokens, run_dir,
          containers_removed, diagnostics: dict)
```

**Design choices.**
- **Messages are agent outputs.** Each turn is a fresh invocation. Its final text is one JSON message, which the orchestrator parses, validates and appends. The alternative is a board MCP tool per agent. I rejected it because sequential turns give it nothing to coordinate over, and it adds a subprocess and IPC.
- **Malformed output.** A bad parse gets one repair re-prompt, which counts as a turn. A second failure ends the turn as `parse_failed` and is logged. Haiku often wraps JSON in prose, so this is real work.
- **Statelessness.** Fresh turns lose exec history. Each squad's own prior `notes` are re-rendered into its next prompt. Arm 2 keeps one continuous session, so the comparison is still confounded against the team. The confound is recorded in `diagnostics` (open question 11).
- **`isolation` is forced false in v1.** All agents share one workspace and can read each other's edits through exec, so hiding board bodies would be cosmetic.
- **Mount layout.** Only `run_dir/shared` is mounted at `/logs`. `board.jsonl`, `result.json` and `exec.jsonl` live in `run_dir/host`. Today the run dir is mounted read-write, so an agent can write the same dir that holds `exec.jsonl`, which `count_exec_calls` trusts (`run_single_agent.py:488-506`). The eval reads `/logs/model.filtered.patch` and `/logs/test.patch` and writes `/logs/pytest.txt` (`:700,726,728`). Those move into `shared`, and `session.py` owns that move.
- **Container prefix.** Today's reap and the cleanup check use `name=gma392-` (`:637`, README:53), and Docker name filters match substrings. A new prefix would be invisible to them. `remove_names` takes the prefix as a parameter. New runs use `gma12-`. The reap docs list both prefixes.
- **Peer verification.** Reviewer and executor share the model and differ only in prompt and fresh context (command-and-flow:90). A peer verdict never becomes the terminal verdict. The eval is the only anchor (protocol-messages:183-185).
- **Rejection.** The default is one bounded redispatch with the reviewer's fixes attached. Automatic rollback (command-and-flow:90) is deferred.
- **Reward.** `reward` is one integer and equals `resolved` (plan:13, plan:23). Per-agent stats live in `diagnostics`.

## 3. Verifiable units

| # | Unit | Check |
|---|---|---|
| 0 | Pure extraction into `session.py`, with no behavior change. The prefix becomes a parameter. | `--mode gold` gives `resolved`, F2P 1/1 and P2P 59/59 (session:70). `result.json` keys match a saved old run. `docker ps -a --filter name=gma392-` is empty. |
| 1 | Mount relocation to `run_dir/shared` | Gold is `resolved` again. `docker inspect` shows one bind whose source ends in `/shared`. `ls /logs` in the work container shows no `exec.jsonl`, no `result.json` and no board file. |
| 2 | `protocol.py` and `board.py` | A round-trip test covers all five types. Malformed, unknown-field and missing-field input is rejected. Two processes appending concurrently yield strictly increasing `seq`. |
| 3 | `runtime.py` with `ScriptedRuntime` and `ClaudeCliRuntime` | A golden test compares `claude_argv` output from the old and new code. `DISALLOWED_TOOLS`, `--strict-mcp-config`, `--permission-mode dontAsk` and the Bash deny setting must be identical (`:33-47,538-561,802-805`). A scripted agent applying the gold patch evaluates to `resolved`. No credits are used. |
| 4 | `orchestrator.py` and `capital.py`, scripted (the GMA-393 acceptance) | The board shows intent, two bids, claim, work, peer verification, report. Eval is `resolved`. A reject script exercises redispatch. A double-claim script exercises arbitration with ledger −1. A malformed-output script hits the repair and `parse_failed` paths. `docker ps` is clean afterward. |
| 5 | `arms.py` and `results.py` | Scripted arms 1 and 2 write schema-valid records. The logged container names are distinct per arm. Arm 1 logs exactly one work container name. Arms run sequentially (start and end times do not overlap). |
| 6 | `ApiRuntime`: the host-side tool loop for non-CLI models (gated by gates 1 and 2) | The tool list handed to the model is exactly one tool, `exec`, asserted by a golden test. An API-side canary asks it to touch a host file and passes only if the file stays absent. |
| 7 | One real-model MCA run on flask-5014 | Done means `board.jsonl`, `result.json` and the eval exist and `containers_removed` is true. Resolving is not required. |
| 8 | `judge.py` | Grep over the blind packet finds no arm or model strings. Sanity: the judge ranks a gold patch above an empty patch and a wrong patch. A guess-the-arm test compares the judge's guesses to chance. |
| 9 | GMA-394 task loader (replaces `EXPECTED_INSTANCE`, `:20,280-282`) plus `instances/allowlist.json` | Gold gate runs 3 times per allowlisted instance before any agent spend. Tasks whose gold does not resolve all 3 times are dropped. |
| 10 | GMA-394 matrix, N tasks × 3 arms × K repeats | One summary table of pass rates plus one record per cell. |
| 11 | GMA-395 offline pieces, pure functions only: rollout export and group-relative advantage | Advantages sum to 0 per group. Identical rewards give 0 advantage. No training happens here. |

## 4. Laptop limits and run-time estimates

- **Containers.** `--platform linux/amd64`, `--network none`, `--memory 4g` (`run_single_agent.py:605-611`). Reported by Tom, not re-measured: the amd64 emulation cost.
- **Concurrency.** The work box is removed before the eval box starts (`:947-950`), and arms run one at a time. Peak is one 4g container plus the Docker VM. The VM memory and CPU settings are unverified, because `docker info` needed approval.
- **Disk.** The flask-5014 image is 4.23GB (`docker images`, observed). For N=5 this is about 21GB of image cache (inferred).
- **Image pull.** The first pull per task has a 1800 s timeout (`:761`). Later runs hit the cache.

| Unit | Estimate | Basis |
|---|---|---|
| 0, 1, 4, 5 (scripted) | about 15 to 25 s per run, mostly eval | reported by Tom, full single run about 20 s |
| 7 (real MCA, 8 to 10 turns) | about 80 to 150 s agent plus about 10 s eval | guess, scaled from Tom's report of about 10 s of agent time per single run |
| 8 judge | about 10 to 30 s per task | guess |
| 10 (N=5, K=3, 45 runs) | about 30 to 60 min plus first pulls | guess |

## 5. Cost and credit limits

Claude CLI credits are judge-only. That has four consequences.
- `ClaudeCliRuntime` is used only by `judge.py`.
- Lesser agents and the frontier control cannot run through `claude -p`.
- Haiku is Anthropic-only, so it needs a non-CLI Anthropic path, such as an API key billed separately from CLI credits. The frontier control is "grok-4.7 or the latest Sonnet" (plan:17). Grok would need an xAI key. Sonnet has the same question as haiku.
- A non-Anthropic open-weights lesser model would sidestep the credit rule and also make GMA-395 trainable. It would replace haiku, so it needs your decision (open question 2).

Units 0 to 5 need no credits because `ScriptedRuntime` drives them.

**Open question: the GMA-392 conflict.** The existing agent path is haiku through `claude -p` on Tom's local auth (session:72, `run_single_agent.py:308,538-561`). That contradicts the judge-only rule. The canary rerun after the unit 3 move (see gate 1) also uses CLI credits.

**GMA-395 consequence.** Haiku is a closed model. A token-level update (plan:37) needs open weights, token ids and logprobs, and a trainer. The loop cannot run on haiku.

## 6. Risks and how to check each

| Risk | Check |
|---|---|
| Mount move breaks the eval path | Unit 1 gold is `resolved` after the move. |
| Cleanup misses new containers (prefix mismatch) | After every run, `docker ps -a` with the new prefix and with `gma392-` are both empty. |
| Security regression in the moved Claude launcher | The unit 3 argv golden test. The canary rerun (gate 1) is the end-to-end proof. |
| `ApiRuntime` exposes more than `exec` | Unit 6 tool-list golden test and the API canary. |
| Agent edits tests | `filter_patch` strips test hunks (`:400-415`). Assert the filtered patch is a subset of the raw one in every run. |
| Agents tamper with the audit log | Unit 1 check: `exec.jsonl` is not inside the mounted dir. |
| Workspace clobbering in the shared container | Sequential turns. Assert one active runtime process at a time from logged start and end times. |
| Haiku emits non-JSON | Unit 4 malformed-output script. Report the `parse_failed` rate. |
| Team handicapped by stateless turns | Each squad's own notes are re-rendered into its next prompt. The confound is recorded in `diagnostics` and compared in the results. |
| Blind judge can still tell the arms apart | Unit 8 guess-the-arm test against chance. Plan:19 wants coordination notes, which leak team structure. |
| Peer verification rubber-stamps | Log the reviewer's false-pass rate against the eval. |
| Instance variance (haiku is nondeterministic) | K=3 repeats per cell, with the spread reported. |
| Emulated-amd64 flakiness or timeouts | Unit 9 runs gold 3 times per instance. Log `status=error` separately from `unresolved`. |
| RLCR has no signal | Binary reward with all-pass or all-fail groups gives zero advantage. Check per-task pass-rate variance before any training. |
| Leaked containers | The `docker ps` check after every run. |
| Dependents of the GMA-392 harness | The `run_single_agent|exec_mcp|gma392|GMA392` search found hits only in `run_single_agent.py`, `exec_mcp.py`, `harness/README.md` and `docs/gma-392-session.md`. Wiki (excluding `.council`), `agent-swarm` and `agent-council` (excluding `target/`) had none. The GMA-392 run dir was unreadable from this session, so whether anything reads it is unproven. |

## 7. Tom gates

1. **Credits and runtime.** Which non-CLI path funds haiku and the frontier control? Also whether a one-time CLI credit exception is allowed for the canary rerun after unit 3. Nothing in unit 6 or later runs until this is answered.
2. **Lesser model.** Keep haiku (needs an API path) or switch to an open-weights model that GMA-395 can train.
3. **Frontier model.** grok-4.7 or the latest Sonnet.
4. **Task set.** Which N and which instances, plus K. Image sizes (about 4GB each) are reviewed first.
5. **After unit 4.** Review one scripted board transcript and the schema before any real-model run.
6. **Before the full matrix (unit 10).** Approve a spend cap.
7. **Before the RLCR loop (GMA-395).** The loop does not start until all of these hold.
   - The three-arm eval is stable. Proposed definition: two consecutive full runs where every arm's per-task outcome changes on at most one task, with zero `error` statuses. With N=5 this is descriptive only, and no significance claim is made unless N grows (open question 6).
   - A trainable open-weights policy and a training stack are chosen.
   - Tom approves the compute and time budget.
   - Status has been reported to Morgan - Chief of Staff (AGENTS.md).
8. Work stays on a branch and goes through a PR, never to main (AGENTS.md).

## 8. Questions the plan leaves open

1. The budget unit, number and scope (tokens or wall-clock, team total or per agent). Does the frontier control share it?
2. Which lesser model, and on what runtime, given the judge-only credits? Which strong model judges?
3. The frontier control. grok-4.7 or the latest Sonnet.
4. What counts as a "small task set", and which images. The plan allows OpenReward or official images (plan:25), but only flask-5014 is loaded today.
5. How many repeats K per cell.
6. How "stable" is defined, and the statistical bar for "scores higher" (plan:41). N=5 cannot support significance.
7. The squad count in 2 to 4.
8. Concurrency. Sequential or parallel, and the shared-file rules if parallel.
9. Whether squads are role-neutral (default) or given hand-assigned roles. Assigned roles defeat the "role emergence" log (plan:41).
10. Initial Leadership Capital and its cost schedule. The wiki gives only the example `>5` and `−1`.
11. Peer reviewer diversity and state. All agents share one model (plan:13), but the wiki wants a different model family for peer verify (protocol-messages:55, rlcr-and-mission-claim:48). Arm 1 uses fresh turns while arm 2 keeps one session.
12. Rollback on rejection. Required or deferred.
13. Whether arm 1 always logs only the collective outcome (plan:13), or only during the RLCR phase (AGENTS.md).
14. A blind judge that must also assess coordination (plan:19). How to scrub transcripts.
15. The RLCR substrate. Open-weights model, trainer, token logprobs, and CISPO versus GRPO (plan:37). Whether GMA-395 is in scope for this laptop at all.
16. Whether the mount and prefix changes in section 2 are acceptable, and whether the stale wiki status should be updated.

## Decision trail

What changed from the first draft, and why.
- **Timing labels.** The first draft called the 10 s and 20 s figures "measured". They came from Tom, not from `docs/gma-392-session.md`, so they are now "reported by Tom".
- **Budget.** The first draft reused `--max-turns` as a team-wide cap. That flag counts turns inside one `claude -p` call. The plan now uses an orchestrator turn cap plus a pooled wall-clock cap.
- **Estimates.** N=5 now includes K=3 repeats. The gold gate runs 3 times everywhere, not once in one place.
- **Mount.** The mount move is now its own unit with a gold check, and the eval-path files move with it. The first draft hid this inside a design note.
- **Prefix.** Cleanup and reap matched only `gma392-`. The prefix is now a parameter, and the docs list both prefixes.
- **Security.** The first draft had no check for the moved launcher. It now has an argv golden test, an optional canary rerun at gate 1, and a separate unit for `ApiRuntime` with its own tool-list test and canary.
- **Missing unit.** `ApiRuntime` is on the critical path for any real run and was missing. It is now unit 6.
- **Bad defaults.** Hard-prompted squad roles are now role-neutral squads. `isolation` is forced false. Fresh turns now carry the squad's own notes. The confound with arm 2 is recorded, not hidden.
- **Malformed output.** Added a repair and `parse_failed` policy and a unit 4 test for it.
- **Checks.** The mid-run `docker ps` check became logged container names. The judge check gained a guess-the-arm test. The stability bar now says N=5 is descriptive only.
- **Credits.** Added the open-weights option and a gate for a one-time canary credit exception.
- **Facts added.** The flask-5014 image is 4.23GB (observed). The dependent search results and the unreadable run dir are now stated. The VM memory remains unverified.
- **Not folded in.** Interrogate finding 15 (empty Leadership Capital ledger) is kept as a documented outcome, not changed, since zero conflicts is a valid result.