# P-GMA-12 plan amendment A1: a model Strategic Commander with 2 to 4 squads

> Docs only, no code. Bo - Bench Eng, 2026-10-24. No Claude used. The direction comes from Tom (via Morgan, 4:17 PM MT): the Strategic Commander becomes a model agent, and the team gets 2 to 4 squads as the plan intended.
>
> **Precedence.** Where `docs/p-gma-12-architecture-plan.md` and this amendment differ, this amendment wins. It supersedes these parts of the architecture plan:
> - Q4.
> - The GMA-393 line of Q5.
> - The code-Commander sentence on Leadership Capital in section 1.
> - In section 2: the `post_message` and handoff details of the turn contract, and the board-message rule.
> - Units 3, 4 and 5 (tool lists) and unit 7 in section 3.
> - The "2 squads" lines in sections 4 and 5.
> - In section 2: the turn-contract sentence that the shared client serves "both team squads" (it serves the Commander and all N squads), and the peer-verification sentence that the reviewer is "the other laguna squad" (it is a different squad, chosen per A1-2).
> - The Leadership Capital cost line in section 1 (adds `reassign`), and the `Phase` line in section 2 (adds `ASSIGNED`).
> - The unit 4 board order in section 3, and the unit 7 "2 squads" estimate row in section 4.
> - Open question 7.
>
> **Direction settled (Tom, 2026-10-26 8:34 AM MT: "Approve the direction on mission-claim #8").** The model Commander on laguna plus 2 to 4 squads is approved. Review objections to the direction itself (model vs. code Commander, same model, squad range) are closed by that approval; only execution and writing points stay open.
>
> The architecture plan carries inline A1 notes at each of those places. Everything else stays in force.

## 0. Why

The plan says: "One Strategic Commander plus two to four specialist squads. All agents use the same lesser model" (plan:13). Revision 3 of the architecture plan made the Commander deterministic code and fixed the team at 2 agents (architecture plan Q4, open question 7). Tom has now reversed that. PR #6 was only a smoke test of communication and task sharing between two agents (section 9). It is not the MCA team result.

**Model choice.** The Commander uses the same model as the squads: paid `poolside/laguna-s-2.1` on OpenRouter, through the one shared `Client`. I found no reason to use a different model:
- plan:13 says all agents use the same lesser model, and the arm comparison (team vs. single lesser agent, plan:41) depends on that.
- A stronger Commander (for example grok-4.7) would mix the frontier control into arm 1 and confound GMA-394.
- In PR #6, laguna made 2063 of 2080 tool calls validly (0.992), and it handed off cleanly in every resolved episode. So structured tool use is not a blocker for a coordinating role.

The one known weakness is long loops on hard instances: 8 of 30 PR #6 episodes hit the 200-turn guard. The Commander design addresses this directly: it can see loops and reassign work (section 3).

## 1. /architect output (method run by hand)

No runnable arena exists here without Claude, and the P-GMA-12 rule is no Claude. So I ran the architect method by hand: ground the existing system, sketch two whole-shape designs, screen them, then synthesize.

**Ground (how the system works today, on main 7a587659).**
- `harness/lesser_driver.py` has two layouts.
  - `run` is a single agent.
  - `run_team` hard-codes two fixed roles, `investigator` and `editor` (`ROLE_TOOLS`, `lesser_driver.py:691`). Control passes only through the `handoff` tool.
- The turn guard is pooled across the team: `inv.stats["turns"] + ed.stats["turns"] < args.max_turns` (`lesser_driver.py:844`). `result.json` records it as `turn_limit_pooled` (`:911`).
- There is no board, no `MissionIntent`, no bids and no Commander. The plan's `mca/` modules (architecture plan section 2) do not exist yet.
- Reused as-is: the shared `Client`, `AgentSlot` park/wake, the `Episode` protocol (read before edit, test-gated `finish`, fail-closed tool calls), `compact`, cost logging from the OpenRouter `usage.cost` field, and the write guard. In the team, a squad's `finish` ends that squad's assignment, not the episode (section 3).

**Candidate A: Commander as a router over a board.** The Commander is a parked `AgentSlot` that the orchestrator wakes only at decision points: start, after bids, after a squad report, after a peer verdict, and on a guard event. It never touches the workspace. Its tools are board-only:
- `broadcast_intent`
- `accept_bid`
- `assign`
- `request_review`
- `reassign`
- `close_mission`

Squads keep the work tools and add the board tools `bid`, `report` and `ask_commander`. The orchestrator is a thin, deterministic loop. It validates every board message (`parse`, the only door in), enforces guards, and wakes exactly one slot at a time.

**Candidate B: Commander as the hub with delegate-as-tool-call.** The Commander runs one long conversation. Each `delegate(squad, task)` call runs a squad sub-episode to completion inside that tool call, then returns the squad's report as the tool result. There is no board: the Commander's own history is the record.

**Screen (design red flags).**
- B hides the protocol inside one history. Bids, claims and peer verification become prose inside tool results, so plan:13's "one message board" and the role-emergence log (plan:41) are lost.
- In B, the Commander's context grows with every squad report, and compaction would delete coordination evidence.
- In B, the nested sub-episodes break the one-awake-slot rule's simple timing logs.
- A keeps the board as the single source of truth, keeps each agent's history small, and reuses park/wake unchanged. The cost of A is one more message vocabulary in the prompt.

**Synthesis decision: A, plus one idea from B.** The sketch below replaces candidate A's draft tool list (no `accept_bid`; adds `claim`, `answer`, `verify` and `finish`). The squad's `finish` posts a short structured `WorkDone` summary to the Commander (B's good idea: the Commander does not need to read transcripts). The full detail stays on the board for audit. Squads stay role-neutral (architecture plan open question 9), so roles emerge from bids, not from prompts. Interface depth: the orchestrator public surface stays `run_team(session, runtime, cfg, instance) -> TeamOutcome` (architecture plan section 2), and the Commander is one more `AgentSlot` with a different tool list.

**Sketch (pseudocode, replaces the code-Commander parts of architecture plan section 2):**
```python
# mca/roles.py
COMMANDER_TOOLS = ("broadcast_intent", "assign", "answer", "request_review",
                   "reassign", "close_mission")   # no work tools; the board render arrives on each wake
WORK_TOOLS      = ("read_file", "edit_file", "run", "finish")   # identical to arms 2 and 3 (tool parity, unit 5)
SQUAD_TOOLS     = WORK_TOOLS + ("bid", "claim", "ask_commander", "verify", "report")
# Squad finish = test-gated (Episode); it ends the squad's assignment and posts WorkDone. The episode ends only by
# close_mission (Commander), the Commander's turn guard, the episode wall clock, or all squads at their turn guard.

@dataclass(frozen=True)
class TeamConfig:
    squads: int                      # 2..4, chosen per section 4
    max_turns_per_agent: int = 200   # guard, per agent (section 5)
    episode_timeout_sec: int = 14400 # guard: episode wall clock, shared by all agents (section 5)
    max_identical_failures: int = 3  # editor-retry fix (section 7)

# mca/orchestrator.py
def run_team(session, runtime, cfg, instance) -> TeamOutcome:
    board = Board(run_dir / "host/board.jsonl")
    cmdr = AgentSlot("commander", COMMANDER_PROMPT, tools=COMMANDER_TOOLS)
    squads = [AgentSlot(f"squad-{i}", SQUAD_PROMPT, tools=SQUAD_TOOLS) for i in range(cfg.squads)]
    wake(cmdr, render(board, instance))                      # phase INTENT
    while not closed and not episode_guard_hit():   # Commander turn guard, all squads at turn guard, or wall clock
        slot = next_slot(board)                              # deterministic, one awake slot
        step(slot)                                           # board tools validated by parse()
        park(slot)
    return outcome(board, eval_result)                       # reward = collective anchor (unchanged)
```

## 2. Commander role and prompt contract

**Role.** The Commander turns the problem into one or more `MissionIntent`s, chooses which squad does what, reads `WorkDone` and `PeerVerification` messages, and decides when the mission is done. It does not read code, edit code or run tests. It works only through board tools. Leadership Capital (architecture plan section 1) stays, as a **proposed** schedule (open question 10 stays open): a starting balance of 10, 1 unit per claim-conflict arbitration and (new in A1, proposed) 1 unit per `reassign`, allowed only while the balance is above 5. It is logged and never enters `reward`.

**Prompt contract (the system prompt must state each item; a golden test checks the prompt for these clauses):**
1. Identity: "You are the Strategic Commander of a team of N squads on the same model. You coordinate; you never edit code."
2. Inputs it gets on each wake: the problem statement (first wake only), board entries since its last turn, the Leadership Capital balance, and a per-squad status line (turns used and last outcome).
3. Allowed actions are the Commander tools only. It must make exactly one tool call per turn. A free-text reply gets a fail-closed error and counts as a turn.
4. Assignment rule: assign each `MissionIntent` to the squad whose bid best matches. **Rework vs. reassign (one rule, shared with item 6):** after a failed `WorkDone` without `repeat_failure`, or after a `Fail` peer verdict, send it back to the same squad once with the reviewer's notes. On the second failure for the same intent, or on any `WorkDone` with `repeat_failure: true`, use `reassign` to a different squad or narrow the intent with a new `broadcast_intent`. Never repeat the same assignment unchanged.
5. Done rule: call `close_mission` only after a successful `WorkDone` (test-gated `finish` accepted), then a `Pass` `PeerVerification` from a different squad (the reviewer), then the working squad's `OwnershipReport`. The orchestrator enforces this: it rejects `close_mission` otherwise. The harness still decides the result from the SWE eval (anchor). `close_mission` never sets `Pass` by itself (I-Peer-Non-Terminal, anchors:112).
6. Loop rule: the harness sets `repeat_failure: true` when a squad makes the same failing tool call 3 times (same tool and arguments, for example the same non-unique `edit_file`, section 7). That ends the squad's assignment. The Commander then applies item 4.
7. Brevity: every intent is self-contained (files, goal, verification), with a limit of 1500 characters.

**Squad prompt contract (role-neutral, same for every squad, differs only in `squad_id`):** bid on intents with confidence and a short rationale; `claim` an intent only after it is assigned to you; work only on claimed intents; `finish` when the visible tests pass (this posts `WorkDone` with `files`, `tests_run` and an `evidence_ref`); after a `Pass` peer verdict, post `report` (`OwnershipReport`); use `ask_commander` instead of guessing.

**Reviewer prompt (a different prompt, as the architecture plan's peer-review section and open question 11 require):** a verify turn runs on a fresh `history_id` (I-Fresh-Checker, anchors:118) with a separate reviewer system prompt. That prompt says: you did not write this change; check the diff against the intent and the tests; you may `read_file` and `run` (read-only); you may not edit; answer with `verify` only. On a verify turn the harness narrows that squad's tool list to `read_file`, `run` and `verify` (no `edit_file`, `finish`, `bid`, `claim`, `report` or `ask_commander`); `run` keeps the write guard.

## 3. Tools and messages between the Commander and squads; assignment and hand-back

All messages go through the board as structured tool calls validated by `parse` (architecture plan section 2: "the only door in"). The message types are the ones already planned (`MissionIntent`, `Bid`, `Claim`, `PeerVerification`, `OwnershipReport`, `Envelope`), plus six new ones:
- `Assignment(mission_id, squad_id, notes)`: the Commander's acceptance of a bid. It replaces the code-side `BidAccepted`.
- `WorkDone(mission_id, squad_id, status, files, tests_run, evidence_ref, repeat_failure)`: posted by the harness when a squad's `finish` is accepted or rejected for the last time, when `repeat_failure` ends the assignment, or when the squad hits its turn guard (`status=guard_stop`, section 5).
- `ReviewRequest(mission_id, reviewer_squad)`: posted by `request_review`.
- `Question(mission_id, squad_id, text)` (posted by `ask_commander`) and `Answer(mission_id, squad_id, text)` (posted by the Commander's `answer`).
- `Close(mission_id, reason)`: `close_mission`.

| Tool | Who | Writes to the board | Wakes |
|---|---|---|---|
| `broadcast_intent(objective, end_state, constraints)` | Commander | `MissionIntent` | every squad, in squad order, for one `bid` turn each |
| `bid(mission_id, confidence, estimated_turns, rationale)` | squad | `Bid` | Commander once all squads have bid or passed |
| `assign(mission_id, squad_id, notes)` | Commander | `Assignment` | the assigned squad |
| `claim(mission_id)` | squad | `Claim` (the harness fills `snapshot_ref` with the git diff hash at claim time) | the claiming squad continues |
| `finish(summary)` | squad | `WorkDone` (test-gated, as in `Episode`) | Commander |
| `report(mission_id, verdict, evidence, corrective_actions)` | squad, after a `Pass` peer verdict | `OwnershipReport` (planned fields, architecture plan section 2) | Commander |
| `request_review(mission_id, reviewer_squad)` | Commander | `ReviewRequest` | reviewer squad (fresh `history_id`, reviewer prompt) |
| `verify(mission_id, verdict, disagreements, evidence)` | reviewer squad | `PeerVerification` (planned fields; the harness fills `reviewer_squad` from the caller, and `evidence` must cite an `exec.jsonl` seq or the patch hash) | Commander |
| `ask_commander(mission_id, text)` | squad | `Question` | Commander |
| `answer(mission_id, squad_id, text)` | Commander | `Answer` | the asking squad |
| `reassign(mission_id, from_squad, to_squad, notes)` | Commander | `Assignment` (costs 1 capital) | new squad |
| `close_mission(reason)` | Commander | `Close` | nobody; episode ends, eval runs |

**Who wakes next (this table and the Scheduling rule agree):** `bid` wakes the next squad that has not bid yet, and wakes the Commander after the last bid or pass. `claim` keeps the claiming squad awake, so it starts work in the same wake. Every other squad message wakes the Commander, and every Commander message wakes the squad it names.

**Assignment flow.** The steps run in this order, which matches plan:13 ("Specialists bid and claim. Peer verification happens before OwnershipReports go up"):
1. INTENT: the Commander calls `broadcast_intent`.
2. BIDDING: each squad gets exactly one bid turn, and may bid or pass.
3. ASSIGNED: the Commander calls `assign`.
4. CLAIMED: the assigned squad calls `claim`.
5. WORKING: the squad works until its `finish` posts `WorkDone`.
6. VERIFYING: the Commander calls `request_review`; a different squad answers with `verify`.
7. REPORTING: after a `Pass`, the working squad posts `report` (`OwnershipReport`).
8. DONE: the Commander calls `close_mission`. Otherwise the work goes to rework or a reassign (prompt item 4).

A `claim` by a squad that was not assigned is a claim conflict. The orchestrator posts it, and the Commander arbitrates with `assign` or `reassign`, which costs 1 capital.

**Hand-back.** A squad never hands work directly to another squad. The PR #6 `handoff` tool is removed in this layout. All hand-backs go squad → board → Commander. This puts every coordination decision on the board, where it can be logged.

**Scheduling.** The orchestrator wakes exactly one slot at a time. The choice is deterministic and follows the "Who wakes next" rule above: bids go in squad order, `claim` continues the same squad, any other squad message wakes the Commander, and any Commander message wakes the named squad. This is the architecture plan's sequential turn contract, unchanged.

## 4. Squad count 2 to 4, and how it is chosen

- The count is a run parameter, `--squads N` with N in {2, 3, 4}. It is fixed for the whole episode and recorded in `result.json` as `team.squads`. The Commander does not change the count during a run. It decides only which squads get work. An unassigned squad stays parked and costs nothing.
- The Commander is an extra agent on top of the squads. N=2 means 3 model agents (Commander plus 2 squads).
- **How N is chosen for the experiment:** run a small sweep first. Use N ∈ {2, 3, 4}, K=3 episodes each, on flask-5014 plus the two hard xarray instances (4094, 6599). Then fix one N for GMA-394, chosen by highest resolve rate, with lower cost breaking ties. The sweep result is reported to Morgan for Tom's sign-off before GMA-394 (new Tom gate, section 10).
- Default before the sweep: N=2 (the smallest team the plan allows).

## 5. Guards

- **Turn guard, per agent:** every agent, the Commander included, may take at most 200 model turns (`--max-turns 200`). This changes PR #6, where the 200 turns were pooled across the team (`lesser_driver.py:844`). A per-agent guard catches a hung agent, not a busy team.
- **Wall-clock guard, per episode:** `--agent-timeout 14400` s is measured from the episode start and applies to every agent at once. When it is reached, the episode stops (`guard_stop.guard = "agent_timeout"`, `agent = "all"`). There is no separate per-squad clock.
- A guard hit is logged as `guard_stop: {"agent": <name>, "guard": "max_turns"|"agent_timeout"}`. `result.json` also records `guards: {max_turns_per_agent: 200, agent_timeout_sec: 14400}`. An interrupt is not a guard stop (as already done in PR #6).
- **What happens on a turn-guard hit:**
  - A squad at its turn guard is parked for the rest of the episode. Its current assignment ends with `WorkDone(status=guard_stop)`, and the Commander is woken and may reassign.
  - The episode stops when the Commander hits its turn guard, or when every squad has hit its turn guard.
  - The wall-clock guard always stops the episode.
- These are still Tom's emergency hang guards only (architecture plan Q1). They are not a budget.

## 6. Cost logging

- Each model call adds its OpenRouter `usage.cost`, prompt tokens and completion tokens to that agent's stats, as `lesser_driver.py` does today.
- `result.json` gets `agents.<name>.cost_usd` for the Commander and for each squad, plus `team.cost_usd` (the sum) and `team.commander_share` (Commander cost ÷ total). This shows directly what the model Commander costs.
- `rate_limit_hits`, `llm_sec`, wall-clock time and `tool_call_success_rate` are logged per agent and for the team.
- No cap (architecture plan Q1). Keys come only from `~/.grok/bin/openrouter-token` at runtime. A unit test greps the run dir for the key prefix (architecture plan unit 3).

## 7. Should-fixes and the editor-retry fix folded into the implementation

The PR #6 round-2 review should-fixes and the run findings are WIP on branch `bo/gma-393-should-fixes` (79132745). That branch is not going to main as its own PR. It is folded into the Commander implementation:

| Item | How it lands in the Commander build |
|---|---|
| Compaction stays over budget when the system prompt alone exceeds it | `compact` fails closed (`ContextOverBudget`, stop reason `context_over_budget`). This matters more now because the Commander's prompt is the longest. |
| Quoted in-place flags (`perl "-i"`, `ruby "-i"`) get past the write guard | The write-guard pattern blocks them, for every squad. |
| A text-fallback `handoff` is dropped | Becomes: text fallback accepts each role's own tool names (Commander and squad tool lists). |
| A rejected handoff counts as a successful tool call | Every rejected board or handoff call counts as `invalid_tool_calls`. |
| The Claude path does not write `guard_stop` and guard values | Kept for parity, even though no Claude runs in P-GMA-12. |
| The LLM error text is missing from `result.json` | `result.json` gets `llm_error {type, kind, code, agent}`, with no provider body and no key. |
| **Editor retry loop** (xarray-4094: 140 non-unique `edit_file` retries in one run) | `edit_file` on non-unique text returns the match count, the line numbers and a hint. After 3 identical failing calls (same tool and arguments), the squad's assignment ends with `WorkDone(repeat_failure=true)`. The Commander applies prompt item 4 (reassign or narrow), instead of the episode ending. |

## 8. Which plan units change

- **Unit 3** (`ApiRuntime`): the tool-list golden test asserts `WORK_TOOLS` for arms 2 and 3. For the team it asserts `SQUAD_TOOLS` and `COMMANDER_TOOLS` (this replaces `post_message`).
- **Unit 4** (scripted orchestrator): add a scripted Commander (`ScriptedRuntime`). The board must show this order: intent, bids, assign, claim, `WorkDone`, review request, peer verification, `OwnershipReport`, close. The unit also covers:
  - N=3: bids in order, and one squad stays parked.
  - A double-claim script: one arbitration, capital 10 → 9.
  - A `repeat_failure` script that leads to a reassign.
  - A `close_mission` without a peer `Pass`, which must be rejected.
  - A Commander free-text turn, which gets a fail-closed error.
- **Unit 5** (tool parity): the work tools of arms 2 and 3 equal the squads' `WORK_TOOLS` (`read_file`, `edit_file`, `run`, `finish`). The board tools are team-only coordination, as in the architecture plan.
- **Unit 7** (first real MCA run): flask-5014 with a laguna Commander and N=2 squads. Done means `board.jsonl`, `result.json` with per-agent cost, an eval, and `containers_removed: true`. Resolving is not required (architecture plan Q5).
- **New unit 7b:** the squad-count sweep (section 4).
- GMA-393 "done" (architecture plan Q5) now means units 4, 7 and 7b.

## 9. Relabel PR #6 as a smoke test

- In `docs/results/gma-393-team-run.md`, the heading and a note now say that this is a communication and task-sharing smoke test (2 fixed roles, investigator → editor, handoff tool, no Commander, no board, pooled turn guard). It is not the MCA team result, and it must not be used as arm 1 data.
- PR #6 itself is merged and stays unchanged. I am not editing its title or body or posting on it without an OK. The relabel lives in the repo doc, and a short PR #6 note can be added if Morgan wants one.
- The results stay useful as a harness check: tool use 0.992, handoffs work, and the guard and logging work.

## 10. GMA-394 waits

The three-arm eval (GMA-394) does not start until the model Commander lands. That means units 4, 7 and 7b pass, and Tom signs off on the squad count (new Tom gate 4a, between gates 4 and 5 in the architecture plan section 7). Until then, no arm 1 numbers are reported. The PR #6 numbers are not to be quoted as team results.

## 11. /blast-radius output

**What changes.** These docs say the next implementation replaces the 2-role `run_team` with a Commander plus N squads. They also change the turn guard from pooled to per agent, remove the squad-to-squad `handoff` tool, and relabel the PR #6 results. This PR touches no code.

**The one fact it is safe because of: nothing in the repo or the run data depends on a Commander, a board or the pooled guard except the PR #6 team layout itself, which is now labelled as a smoke test.** Level 4 (ran it). I read all 30 PR #6 `result.json` files on the Mac with a script. Output:
```
team2 runs 30 agent sets {('editor', 'investigator'): 30} pooled turn limit {200} commander key present False board key False cost 1.3132
```
So every PR #6 result is the fixed 2-role layout with a pooled 200-turn guard, and none has a Commander or board key. Nothing will be confused for, or mixed into, arm 1 data as long as the summarizer keeps filtering on `layout == "team2"`. The future layout name must be different (proposed: `mca`). A `grep -rn -i commander harness docs` finds the word only in docs: the source plan (`docs/mission-claim-rlcr-experiment-plan.md:13,29`), the architecture plan, this amendment, and the relabel note added to `docs/results/gma-393-team-run.md` in this PR. It finds nothing in `harness/`.

**Risks**
1. **Guard semantics change (medium likelihood, medium cost).** `lesser_driver.py:844` pools turns, and `turn_limit_pooled` is in PR #6's `result.json`. A summarizer that compares `guard_stop` across layouts would compare unlike things. Check: the new layout writes `guards.max_turns_per_agent`, and the summarizer refuses to mix layouts.
2. **Cost goes up (high likelihood, low cost).** One more agent, and the Commander is woken after every squad message. On easy instances the Commander could double the turns. Check: `team.commander_share` in every result. Report it in the sweep.
3. **Commander rubber-stamps or loops (medium, medium).** Laguna as Commander may close without peer verification, or reassign in circles. Check: prompt clause 5 is enforced by the orchestrator as well (it rejects `close_mission` without a `PeerVerification` from a different squad). Capital stops reassign loops after 5 reassigns.
4. **Longest prompt hits compaction (low, high).** The Commander prompt plus the board render can be large with N=4. Check: compaction fails closed (section 7). The render shows board entries since the last turn, not the whole board.
5. **The `handoff` tool is removed (low, low).** `ROLE_TOOLS` (`lesser_driver.py:691`) and the PR #6 tests use it. Keep the old `team2` layout working behind its own flag for regression, or delete it in the same PR with its tests (principle: migrate callers, then delete).
6. **Same-model peer review (known).** This is unchanged from architecture plan open question 11.

**Cleared**
- Arms 2 and 3 do not change. The single-agent path (`run`) is not touched by the Commander layout.
- Key handling does not change: keys come only from the helper, and the unit 3 key test still applies.
- The eval and anchor rules do not change: `reward` is still the SWE eval only, and `close_mission` cannot set `Pass`.

**Before merging the implementation (not this PR):** the unit 4 scripted Commander test (N=2 and N=3), the reassign-after-`repeat_failure` test, and a key-grep test over a real unit 7 run dir.

## 12. Open questions added

- A1-1: Should the Commander see squad transcripts on demand (a `read_report_detail` tool), or only structured reports? Default: structured reports only.
- A1-2: Should verification always use a different squad when N ≥ 3, or the least-busy squad? Default: the next squad in order that did not do the work.
- A1-3: Is a separate per-squad wall clock needed? Default: no. The 14400 s guard is one episode wall clock for all agents (section 5).
