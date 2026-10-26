# P-GMA-12 architecture plan (plan-only)

> Plan-only. Produced 2026-10-23 by headless Claude Code with pstack /poteto-mode, /architect, /interrogate, /blast-radius, /show-me-your-work (session 58c097a3-6d1b-4d3a-83e9-b3f3acf4e5dd). No implementation. Mirrors the Linear doc on project P-GMA-12.
>
> Revision 2 (2026-10-24, Bo - Bench Eng, no Claude used): folds in Tom's answers to Q1-Q3 and fixes the three round-1 review blockers. Tom's answers: **Q1** no budget or runtime cap; **Q2** the lesser model is paid `poolside/laguna-s-2.1` (OpenRouter) for the MCA team (2 agents) and the single lesser arm; **Q3** grok-4.7 is both the judge and the frontier control. No Claude anywhere in the experiment.
>
> Revision 3 (2026-10-24, Bo - Bench Eng, Tom-approved final review round): removes every turn and wall-clock cap, makes the turn contract one rule that matches the merged harness, and refreshes harness cites against origin/main 88641058.
>
> **Amendment A1 (2026-10-24, Tom via Morgan): the Strategic Commander is a model agent (same lesser model, paid laguna) with 2 to 4 squads. The parts listed in the Precedence note of `docs/p-gma-12-commander-amendment.md` are superseded (each carries an inline A1 note); where the two differ, the amendment wins. Guards: 200 turns per agent; 14400 s is one episode wall clock (amendment section 5). GMA-394 waits until the model Commander lands.**

Scope is GMA-393 (MCA command layer on the same SWE session), GMA-394 (three-arm run on a small task set) and GMA-395 (collective-advantage RLCR loop). The `mca/` package is not built yet (the 2-role smoke-test `run_team` from PR #6 is on main).

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
- The GMA-393 lesser-model driver, `harness/lesser_driver.py` (PR #4): structured tools, read-before-edit, test-gated finish, fail-closed tool calls, cost logging.
- The wiki says "implementation not started" (wiki:20), and the repo `README.md` status line says the same. Both are stale, since GMA-392 is merged.

The `agent-swarm` and `agent-council` repos are Rust. I saw file lists only and took no reuse from them.

## 1. Q1 to Q5

| Q | Answer | Tag |
|---|---|---|
| Q1 budget | **Tom: no budget or runtime cap** for P-GMA-12 on paid laguna: no spend cap, no turn cap and no wall-clock cap. Parity with plan:15 ("the same token or wall-clock budget") holds because every arm runs under the same uncapped condition. Every P-GMA-12 run, in every arm, sets the harness flags `--max-turns 200` and `--agent-timeout 14400` (4 h) (`run_single_agent.py:326-327`; enforced in `lesser_driver.py:636` for one agent; for the team the pooled turn test is `:844` and the deadline check is `:845-847`, on main 7a587659; A1 makes the turn guard per agent). **Tom chose these 2026-10-24 as emergency hang guards only: they exist to catch a hung or looping run, not as a budget or turn cap.** They are set far above observed episodes (laguna: 10-30 turns, under 1 min, PR #4/#5), a run that hits one is logged as `guard_stop` and investigated as a fault, and the values are recorded in `result.json`. Wall-clock, tokens and `cost_usd` are recorded in every `result.json`, never enforced. | Tom decision + plan rule |
| Q2 reuse and lesser model | **Tom: lesser model = paid `poolside/laguna-s-2.1`** via OpenRouter's OpenAI-compatible API, one shared client for the team and the same model for arm 2. Reuse: the plan says "reuse the MCA PoC scaffolding where it exists" (plan:29). The PoC's `command/`, `squad/` and `protocol/` are empty (poc:136-141). What exists is the Rust message vocabulary (protocol-messages:27-142), the anchored evidence types (protocol-messages:152-184), and the GMA-393 driver (`harness/lesser_driver.py`). The message vocabulary and the full anchored evidence shape are ported to Python dataclasses (section 2). | Tom decision + plan rule |
| Q3 messages and board | The plan names the messages and "one message board" (plan:13). Field shapes come from the wiki, not the plan. Board default is an append-only JSONL file on the host, owned by the orchestrator. `Claim` is new, since the wiki has `SquadBid` plus a `BidAccepted` event but no `Claim` type. `Bid` keeps only a subset of `SquadBid`, and `MissionIntent` drops the wiki's `token_budget` ceiling (protocol-messages:35) because Q1 has no cap; both are proposed simplifications, not wiki shapes. | plan names, proposed default |
| Q4 team (**superseded by Amendment A1**: model Commander + 2-4 squads) | The plan gives one Strategic Commander plus "two to four" squads, all on the same lesser model (plan:13). ~~**Tom: start with 2 agents.** So the team is 2 laguna squads, one agent each, sequential turns.~~ *A1 replaces this cell:* the Strategic Commander is a model agent on the same paid laguna model as the squads, and there are 2 to 4 squads (`--squads N`, chosen by the unit 7b sweep). See `docs/p-gma-12-commander-amendment.md`. ~~The Strategic Commander is deterministic orchestrator code (intent broadcast, bid acceptance, claim arbitration), not a model agent; this is a deviation from plan:13 forced by the 2-agent limit and is logged.~~ Squads are role-neutral. They share one base prompt and differ only in `squad_id`. Roles are read off their bids, not assigned (open question 9). An idle squad is parked (no model calls) until the orchestrator wakes it (`AgentSlot`, PR #4). | Tom decision + plan range |
| Q5 done | Study level (plan:41):<br>• The team beats the single lesser agent.<br>• After training the gap grows or closes on the frontier control.<br>• Role emergence, peer-review frequency and Leadership Capital usage are logged.<br>Per ticket (proposed; this is the single definition, section 3 refers back to it):<br>• *(A1: GMA-393 is done when units 4, 7 and 7b pass; see the amendment section 8.)* ~~**GMA-393 is done** when unit 4 passes (scripted protocol check) **and** unit 7 runs: one real-model MCA run on `pallets__flask-5014` that ends with `board.jsonl`, `result.json`, an eval and `containers_removed: true`. Resolving in unit 7 is not required.~~<br>• GMA-394 is done when N tasks × 3 arms × K repeats have result records and blind judge notes.<br>• GMA-395 is done only after the gate in section 7. | plan study level, proposed per ticket |

**Leadership Capital.** The plan only says to log its usage (plan:41). The wiki defines it in four places.
- Commanders spend it only on conflicts or escalation thresholds (command-and-flow:41). *(A1: the model Commander spends capital; the orchestrator keeps the ledger.)*
- The sketch intervenes only while `capital > 5` and costs 1 per intervention (decentralized-command:79-87).
- Capital writeback happens only at anchor-terminal states (anchors P1.6, line 85). It must not substitute for anchors (anchors:44).
- Under RLCR it is an environment affordance, not a reward head (rlcr-and-mission-claim:46).

What that means for the logs:
- Arm 1 keeps a `CapitalLedger`. Each entry has an event, a cost and the balance after. The starting balance is 10 (arbitrary, proposed). Each claim-conflict arbitration costs 1 unit (balance 10 → 9), and arbitration is allowed only while the balance is above 5. *(A1, proposed: each `reassign` also costs 1 unit under the same > 5 rule; amendment section 2. Open question 10 stays open.)*
- The ledger is a diagnostic. It never enters `reward`. Arms 2 and 3 record `null`.
- Real runs may have zero conflicts, so the ledger can legitimately be empty. Record `claim_conflicts: 0` instead of treating the absence as a bug.
- A post-eval writeback is computed and logged, never fed to the reward.

## 2. Modules, types, interfaces

Everything lives alongside `harness/` in Python 3.9 stdlib. Bodies are `...`. The union syntax below is pseudocode.

```
harness/
  session.py          # extracted from run_single_agent.py
  runtime.py          # AgentRuntime: ScriptedRuntime, ApiRuntime (OpenAI-compatible; wraps lesser_driver)
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
class TurnRequest: role; system; prompt; tools: tuple[str, ...]; timeout_sec; session; history_id
# history_id names a per-agent, per-episode message history held by the runtime (compacted when long).
# A reviewer turn always gets a fresh history_id (I-Fresh-Checker, anchors:118).
@dataclass(frozen=True)
class TurnResult: final_text; exec_calls; wall_sec; tokens_in; tokens_out; model_reported; raw_path
class AgentRuntime(Protocol):
    name: str; credit_source: str
    def run_turn(req: TurnRequest) -> TurnResult: ...
# mca/protocol.py  (ported from protocol-messages:152-184)
Verdict = Pass | Fail | Partial | Unanchored | BlockedOnAnchor
EvidenceKind = Observational | Normative | AgentClaim | PeerOpinion
Evidence(kind, anchor_id: Optional[str], artifact_ref, observed_at, observer, payload)
                                                          # artifact_ref: exec.jsonl seq, patch hash or run_id; never prose alone
AnchorSpec(anchor_id, family, check, required, freshness) # v1: one required anchor, family=Observational, check=TestSuite (SWE eval)
AnchorResult(anchor_id, status, evidence, runner_id)      # status: Satisfied | Failed | Unavailable | Expired
MissionIntent(mission_id, parent_id, objective, end_state, constraints, isolation=False)   # no budget field (Q1)
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
Phase = INTENT -> BIDDING -> ASSIGNED -> CLAIMED -> WORKING -> VERIFYING -> REPORTING -> DONE | FAILED   # A1: ASSIGNED added (amendment section 3)
def run_team(session, runtime, cfg: TeamConfig, instance) -> TeamOutcome: ...
# arms.py, judge.py, results.py
class Arm(Enum): MCA; SINGLE; FRONTIER
def run_arm(arm, instance, runtimes, logs_root) -> ArmResult: ...
def blind_packet(results) -> list[BlindEntry]: ...        # shuffled, opaque ids, model names scrubbed
def judge(entries, runtime) -> list[JudgeNotes]: ...
ArmResult(schema_version, instance_id, arm, model_requested, model_reported, runtime_name, credit_source,
          status, reward: int, f2p, p2p, timings, turns, exec_calls, tokens, cost_usd: float, run_dir,
          containers_removed, diagnostics: dict)
```

**Design choices.**
- **Turn contract (one rule for all arms, matching `harness/lesser_driver.py` on main).** One shared OpenRouter `Client` (`lesser_driver.py:274`, main 7a587659) serves both team squads *(A1: the Commander and all N squads)*; the model is loaded once and no agent gets its own client. Each worker agent is an `AgentSlot` (`:552-566`) with one continuous message history per episode, compacted when long (`compact`, `:109`; called in `step`, `:573`). The orchestrator wakes a slot with an explicit handoff message (`wake`, `:560`): the board entries since that agent last acted, rendered by `Board.render`. The agent then works with structured tool calls (`step`, `:568`) and posts its board message through the `post_message` tool *(A1: replaced by the board tools in the amendment section 3)*. After `post_message` or `finish` the slot parks (`park`, `:564`) and makes no model calls until the next handoff. Only one slot is awake at a time (sequential turns). Arm 2 and arm 3 use the same slot and history rule with one agent and no handoffs, so there is no fresh-turn versus continuous-history confound. Nothing runs through `claude -p` (section 5). The only fresh-context turn is peer verification, by design (I-Fresh-Checker, anchors:118): the reviewer gets a new `history_id`.
- **Board messages are tool calls.** *(A1: `post_message` is replaced by the board tools in amendment section 3; the parse and `parse_failed` rules below apply to them.)* `post_message` arguments are validated by `parse`. A bad call gets an error back (fail closed, as in PR #4) and counts as a turn. Two consecutive failures end the turn as `parse_failed` and are logged. Free-text final answers are never parsed as board messages.
- **`isolation` is forced false in v1.** All agents share one workspace and can read each other's edits through exec, so hiding board bodies would be cosmetic.
- **Mount layout.** Only `run_dir/shared` is mounted at `/logs`. `board.jsonl`, `result.json` and `exec.jsonl` live in `run_dir/host`. Today the run dir is mounted read-write, so an agent can write the same dir that holds `exec.jsonl`, which `count_exec_calls` trusts (`run_single_agent.py:547-566`). The eval reads `/logs/model.filtered.patch` and `/logs/test.patch` and writes `/logs/pytest.txt` (`:765,791,793`). Those move into `shared`, and `session.py` owns that move.
- **Container prefix.** Today's reap and the cleanup check use `name=gma392-` (`:696`, README:53), and Docker name filters match substrings. A new prefix would be invisible to them. `remove_names` takes the prefix as a parameter. New runs use `gma12-`. The reap docs list both prefixes.
- **Peer verification.** The wiki wants a *different* model or prompt to verify (command-and-flow:90) and a different model family for peer review (protocol-messages:127, rlcr-and-mission-claim:48). The plan puts all agents on the same lesser model (plan:13), and Tom kept that (Q2). So in v1 the reviewer is the other laguna squad *(A1: a different squad than the worker, chosen per amendment A1-2, with a separate reviewer prompt)* with a different prompt and a fresh context that excludes the worker transcript (I-Fresh-Checker, anchors:118). This is a known deviation from the wiki, recorded in `diagnostics` and in open question 11, not a wiki rule.
- **Terminal verdict.** A peer verdict is `PeerOpinion` evidence and never terminal (I-Peer-Non-Terminal, anchors:112; "peer alone cannot terminal-Pass", protocol-messages:184). `Pass` requires every required `AnchorSpec` to be `Satisfied` (protocol-messages:183-184). In this experiment the only required anchor is the SWE eval (`TestSuite`); that choice is a proposed default for this study, not a wiki statement. An `Unavailable` eval yields `BlockedOnAnchor`, never `Pass`.
- **Rejection.** The default is one bounded redispatch with the reviewer's fixes attached. Automatic rollback to the pre-claim snapshot (command-and-flow:90) is deferred.
- **Reward.** Proposed encoding: `reward` is one integer, 1 when the required anchor is `Satisfied` (FAIL_TO_PASS pass and PASS_TO_PASS stay green, plan:23), else 0. Tests passing is the primary automatic score (plan:19). Arm 1 logs only this collective outcome (plan:13). Per-agent stats live in `diagnostics`.

## 3. Verifiable units

| # | Unit | Check |
|---|---|---|
| 0 | Pure extraction into `session.py`, with no behavior change. The prefix becomes a parameter. | `--mode gold` gives `resolved`, F2P 1/1 and P2P 59/59 (session:70). `result.json` keys match a saved old run. `docker ps -a --filter name=gma392-` is empty. |
| 1 | Mount relocation to `run_dir/shared` | Gold is `resolved` again. `docker inspect` shows one bind whose source ends in `/shared`. `ls /logs` in the work container shows no `exec.jsonl`, no `result.json` and no board file. |
| 2 | `protocol.py` and `board.py` | A round-trip test covers every ported type: the messages (`MissionIntent`, `Bid`, `Claim`, `PeerVerification`, `OwnershipReport`, `Envelope`) and the evidence types (`Evidence`, `AnchorSpec`, `AnchorResult`, `Verdict`). Malformed, unknown-field and missing-field input is rejected. Two processes appending concurrently yield strictly increasing `seq`. |
| 3 | `runtime.py` with `ScriptedRuntime` and `ApiRuntime` (wraps `lesser_driver.py`) | A golden test asserts the exact tool list (`read_file`, `edit_file`, `run`, `finish`, plus `post_message` for team roles; *A1: the team tool lists are `SQUAD_TOOLS` and `COMMANDER_TOOLS`, amendment section 8*). A scripted agent applying the gold patch evaluates to `resolved`. A unit test shows the API key is read from the helper at runtime and never appears in any run-dir file. |
| 4 | `orchestrator.py` and `capital.py`, scripted (plumbing precondition for GMA-393; see Q5 for "done"; *A1: adds a scripted Commander and the board order in amendment section 8*) | *(A1 order: intent, bids, assign, claim, WorkDone, review request, peer verification, OwnershipReport, close.)* ~~The board shows intent, two bids, claim, work, peer verification, report.~~ The scripted squad applies the gold patch, so the eval is `resolved`; this checks plumbing, not agent skill. A reject script exercises redispatch. A double-claim script exercises one arbitration (ledger balance 10 → 9). A malformed-output script hits the error and `parse_failed` paths. `docker ps` is clean afterward. |
| 5 | `arms.py` and `results.py` (*A1: work tools = `read_file`, `edit_file`, `run`, `finish`*) | Scripted arms 1 and 2 write schema-valid records. Tool parity (plan:15): the tool list given to arm 2 and arm 3 equals the team's work tools, asserted by a golden test. The logged container names are distinct per arm. Arm 1 logs exactly one work container name. Arms run sequentially (start and end times do not overlap). |
| 6 | `ApiRuntime` security check | An API-side canary asks the model to touch a host file and passes only if the file stays absent. A write-guard test shows `run` rejects file writes (redirects, `sed -i`, `mv`, `cp`, `tee`). |
| 7 | One real-model MCA run on flask-5014 (*A1:* a laguna Commander plus N=2 laguna squads; unit 7b is the N=2..4 sweep) | The Q5 GMA-393 definition: `board.jsonl`, `result.json` and the eval exist and `containers_removed` is true. Resolving is not required. |
| 8 | `judge.py` (grok-4.7 via OpenRouter `x-ai/grok-4.7`) | Grep over the blind packet finds no arm or model strings. Sanity: the judge ranks a gold patch above an empty patch and a wrong patch. A guess-the-arm test compares the judge's guesses to chance. |
| 9 | GMA-394 task loader (extends the directory allowlist on main, `ALLOWED_INSTANCES`, `:20-23`, checked at `:297`) plus `instances/allowlist.json` | Gold gate runs 3 times per allowlisted instance before any agent spend. Tasks whose gold does not resolve all 3 times are dropped. (Units 0 and 1 run gold once as a regression check; the 3-times gate is only here.) |
| 10 | GMA-394 matrix, N tasks × 3 arms × K repeats | One summary table of pass rates plus one record per cell. |
| 11 | GMA-395 offline pieces, pure functions only: rollout export and advantage | Within an episode every token of every agent gets the same advantage (plan:37). For a GRPO-style group baseline, advantages sum to 0 across a group of rollouts; the CISPO variant is tested only if open question 15 picks it. Identical rewards give 0 advantage. No training happens here. |

## 4. Laptop limits and run-time estimates

- **Containers.** `--platform linux/amd64`, `--network none`, `--memory 4g` (`run_single_agent.py:665-667`). Reported by Tom, not re-measured: the amd64 emulation cost.
- **Concurrency.** The work box is removed before the eval box starts (`:1024-1028`), and arms run one at a time. Peak is one 4g container plus the Docker VM. The VM memory and CPU settings are unverified, because `docker info` needed approval.
- **Disk.** The flask-5014 image is 4.23GB (`docker images`, observed). For N=5 this is about 21GB of image cache (inferred).
- **Image pull.** The first pull per task has a 1800 s timeout (`:826`). Later runs hit the cache.

| Unit | Estimate | Basis |
|---|---|---|
| 0, 1, 4, 5 (scripted) | about 15 to 25 s per run, mostly eval | reported by Tom, full single run about 20 s |
| 7 (real MCA, 2 squads; *A1: Commander + N=2 squads, so 3 model agents*) | about 60 to 150 s agent plus about 5 s eval | scaled from the measured laguna single-agent smoke: 5/5 resolved on flask-5014, 16-56 s agent time, 10-30 turns (PR #4) |
| 8 judge | about 10 to 30 s per task | guess |
| 10 (N=5, K=3, 45 runs) | about 30 to 60 min plus first pulls | guess |

## 5. Cost and model routing

Tom's answers remove the credit questions.
- **No Claude anywhere** (Q3). No `claude -p`, no Claude judge, no Anthropic models. The GMA-392 Claude agent path in `run_single_agent.py` (`claude_argv`, `:597-620`) stays only as legacy code and is not used by any arm.
- **Lesser model** (Q2): paid `poolside/laguna-s-2.1` on OpenRouter for arm 1 (*A1:* a model Commander plus 2 to 4 squads, one shared client) and arm 2.
- **Frontier control and judge** (Q3): grok-4.7, called as OpenRouter `x-ai/grok-4.7` (listed on 2026-10-24) through the same `ApiRuntime`. The judge runs blind (unit 8).
- **Keys** come only from the existing `op read` helper (`openrouter-token`) at runtime, held in memory, never written to files or logs.
- **Budget** (Q1): no cap. Every `result.json` records tokens and `cost_usd` from the OpenRouter usage field. Measured: about $0.001 per laguna flask-5014 episode (PR #4).
- **GMA-395 consequence.** laguna-s-2.1 is served through an API. A token-level update (plan:37) needs open weights, token ids and logprobs, and a trainer. Whether laguna weights are available for that is open question 15.

Units 0 to 5 need no model calls because `ScriptedRuntime` drives them.

## 6. Risks and how to check each

| Risk | Check |
|---|---|
| Mount move breaks the eval path | Unit 1 gold is `resolved` after the move. |
| Cleanup misses new containers (prefix mismatch) | After every run, `docker ps -a` with the new prefix and with `gma392-` are both empty. |
| `ApiRuntime` exposes more tools than intended | Unit 3 tool-list golden test and the unit 6 API canary. |
| Agent writes files through `run`, skipping read-before-edit | Unit 6 write-guard test; blocked attempts are logged and counted. |
| API key leaks into logs | Unit 3 key test: grep every run-dir file for the key prefix. |
| Agent edits tests | `filter_patch` strips test-file diffs (`filter_patch`, `:424-439`). Assert the filtered patch is a subset of the raw one in every run. |
| Agents tamper with the audit log | Unit 1 check: `exec.jsonl` is not inside the mounted dir. |
| Workspace clobbering in the shared container | Sequential turns. Assert one active runtime process at a time from logged start and end times. |
| Model emits broken tool calls | Fail-closed errors are counted per episode (PR #4 smoke: 0 invalid of 86). Unit 4 malformed-output script. Report the `parse_failed` rate. |
| Same-model peer review is weaker than the wiki's cross-family review | Logged as a deviation; log the reviewer's false-pass rate against the eval. |
| Blind judge can still tell the arms apart | Unit 8 guess-the-arm test against chance. Plan:19 wants coordination notes, which leak team structure. |
| Peer verification rubber-stamps | Log the reviewer's false-pass rate against the eval. |
| Instance variance (the model is nondeterministic) | K=3 repeats per cell, with the spread reported. |
| Emulated-amd64 flakiness or timeouts | Unit 9 runs gold 3 times per instance. Log `status=error` separately from `unresolved`. |
| RLCR has no signal | Binary reward with all-pass or all-fail groups gives zero advantage. Check per-task pass-rate variance before any training. |
| Leaked containers | The `docker ps` check after every run. |
| Dependents of the GMA-392 harness | The `run_single_agent|exec_mcp|gma392|GMA392` search found hits only in `run_single_agent.py`, `exec_mcp.py`, `harness/README.md` and `docs/gma-392-session.md`. Wiki (excluding `.council`), `agent-swarm` and `agent-council` (excluding `target/`) had none. The GMA-392 run dir was unreadable from this session, so whether anything reads it is unproven. |

## 7. Tom gates

1. ~~Credits and runtime~~ — answered 2026-10-24: no Claude anywhere; OpenRouter for all model calls; no budget or runtime cap.
2. ~~Lesser model~~ — answered: paid `poolside/laguna-s-2.1`.
3. ~~Frontier model~~ — answered: grok-4.7 (also the judge).
4. **Task set.** Which N and which instances, plus K. Image sizes (about 4GB each) are reviewed first.
4a. **(A1) Squad count.** After unit 7b, Tom signs off on N (2 to 4) before GMA-394.
5. **After unit 4.** Review one scripted board transcript and the schema before any real-model run.
6. ~~Spend cap before the full matrix~~ — answered: no cap; cost is reported.
7. **Before the RLCR loop (GMA-395).** The loop does not start until all of these hold.
   - The three-arm eval is stable. Proposed definition: two consecutive full runs where every arm's per-task outcome changes on at most one task, with zero `error` statuses. With N=5 this is descriptive only, and no significance claim is made unless N grows (open question 6).
   - A trainable open-weights policy and a training stack are chosen.
   - Tom approves the compute and time budget.
   - Status has been reported to Morgan - Chief of Staff (AGENTS.md).
8. Work stays on a branch and goes through a PR, never to main (AGENTS.md).

## 8. Questions the plan leaves open

1. ~~Budget~~ — answered: no budget or runtime cap; cost is recorded (Q1).
2. ~~Lesser model and judge~~ — answered: laguna-s-2.1; grok-4.7 judges.
3. ~~Frontier control~~ — answered: grok-4.7.
4. What counts as a "small task set", and which images. The plan allows the OpenReward port or the official Docker images (plan:23). The GMA-393 baseline branch adds more SWE-bench Verified instances with official images.
5. How many repeats K per cell.
6. How "stable" is defined, and the statistical bar for "scores higher" (plan:41). N=5 cannot support significance.
7. ~~Squad count~~ — reopened and answered by Amendment A1: 2 to 4 squads plus a model Commander; N is chosen by the unit 7b sweep and Tom signs off.
8. ~~Concurrency~~ — settled: sequential turns (Q4, section 2 turn contract). Parallel is out of scope for v1.
9. Whether squads are role-neutral (default) or given hand-assigned roles. Assigned roles defeat the "role emergence" log (plan:41).
10. Initial Leadership Capital and its cost schedule. The wiki gives only the example `>5` and `−1`.
11. Peer reviewer diversity. All agents share one model (plan:13, Tom Q2), but the wiki wants a different model or prompt (command-and-flow:90) and a different model family for peer verify (protocol-messages:127, rlcr-and-mission-claim:48). v1 uses a different prompt and fresh context only. Should a later run add grok-4.7 or another family as reviewer, at the cost of breaking the same-model rule? (Agent state is settled: the section 2 turn contract.)
12. ~~Rollback on rejection~~ — settled: deferred in v1 (section 2, Rejection).
13. Whether arm 1 always logs only the collective outcome (plan:13), or only during the RLCR phase (AGENTS.md).
14. A blind judge that must also assess coordination (plan:19). How to scrub transcripts.
15. The RLCR substrate. Open-weights model (are laguna weights available?), trainer, token logprobs, and CISPO versus GRPO (plan:37). Whether GMA-395 is in scope for this laptop at all.
16. Whether the mount and prefix changes in section 2 are acceptable, and whether the stale wiki status should be updated.

## Decision trail

What changed from the first draft, and why.
- **Timing labels.** The first draft called the 10 s and 20 s figures "measured". They came from Tom, not from `docs/gma-392-session.md`, so they are now "reported by Tom".
- **Budget.** The first draft reused `--max-turns` as a team-wide cap. That flag counts turns inside one `claude -p` call. Revision 3 removes every cap: no turn pool and no wall-clock budget (Tom Q1). Only emergency hang guards remain (`--max-turns 200`, `--agent-timeout 14400`, same in every arm, Tom-approved 2026-10-24); they catch hung runs and are not a cap.
- **Estimates.** N=5 now includes K=3 repeats. Units 0 and 1 run gold once as a regression check; the 3-times gold gate is only in unit 9.
- **Mount.** The mount move is now its own unit with a gold check, and the eval-path files move with it. The first draft hid this inside a design note.
- **Prefix.** Cleanup and reap matched only `gma392-`. The prefix is now a parameter, and the docs list both prefixes.
- **Security.** The first draft had no check for the moved launcher. It now has a unit 3 tool-list golden test and API key test, and a unit 6 security canary plus write-guard test.
- **Missing unit.** `ApiRuntime` is on the critical path for any real run and was missing. It is now unit 3; unit 6 is its security canary.
- **Bad defaults.** Hard-prompted squad roles are now role-neutral squads. `isolation` is forced false. Agent state follows the single section 2 turn contract (continuous per-agent history, handoff messages, parked idle agents), so there is no arm-2 confound.
- **Malformed output.** Added a repair and `parse_failed` policy and a unit 4 test for it.
- **Checks.** The mid-run `docker ps` check became logged container names. The judge check gained a guess-the-arm test. The stability bar now says N=5 is descriptive only.
- **Credits.** Superseded by Tom's Q1-Q3: no Claude anywhere, OpenRouter for all model calls, no canary credit exception. The open-weights question stays open for GMA-395 (open question 15).
- **Facts added.** The flask-5014 image is 4.23GB (observed). The dependent search results and the unreadable run dir are now stated. The VM memory remains unverified.
- **Revision 2 (2026-10-24).** Folded in Tom's Q1-Q3. Fixed round-1 blockers: (1) wiki cites now match their lines, and the peer-review section states the wiki conflict instead of denying it; the full anchored `Evidence`/`AnchorSpec`/`AnchorResult`/`Verdict` shape is ported. (2) Arm state has one rule: continuous per-agent history in every arm, no `claude -p`; the contradiction is gone. (3) "Done" for GMA-393 is defined once in Q5; unit 4 is a plumbing precondition. Nits fixed: plan:23 for images, ledger wording, unit 11 GRPO/CISPO wording, tool-parity check, gold-run counts, README staleness, trailing newline.
- **Revision 3 (2026-10-24, Tom-approved final round).** Fixed round-2 blockers: (1) removed the 60-turn pooled limit and `wall_budget_sec` so the plan matches Q1 (no budget or runtime cap; `cost_usd` recorded). (2) One turn contract, matching `lesser_driver.py` on main: shared client, per-agent continuous history, explicit handoff messages, `post_message` tool, parked idle agents. Should-fixes: harness line cites re-checked against origin/main (88641058), `cost_usd` added to `ArmResult`, round-trip test names every ported type, concurrency and rollback marked settled, stale trail entries corrected.
- **Not folded in.** Interrogate finding 15 (empty Leadership Capital ledger) is kept as a documented outcome, not changed, since zero conflicts is a valid result.
