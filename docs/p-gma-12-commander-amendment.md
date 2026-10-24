# P-GMA-12 plan amendment A1: a model Strategic Commander with 2 to 4 squads

> Docs only, no code. Bo - Bench Eng, 2026-10-24. No Claude used. The direction comes from Tom (via Morgan, 4:17 PM MT): the Strategic Commander becomes a model agent, and the team gets 2 to 4 squads as the plan intended. This amendment replaces **Q4** of `docs/p-gma-12-architecture-plan.md` and the parts of sections 2, 3 and 8 that assume a code Commander and exactly 2 agents. All other parts of the plan stay in force.

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
- Reused as-is: the shared `Client`, `AgentSlot` park/wake, the `Episode` protocol (read before edit, test-gated finish, fail-closed tool calls), `compact`, cost logging from the OpenRouter `usage.cost` field, and the write guard.

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

**Synthesis decision: A, plus one idea from B.** The squad's `report` returns a short structured summary to the Commander (B's good idea: the Commander does not need to read transcripts). The full detail stays on the board for audit. Squads stay role-neutral (architecture plan open question 9), so roles emerge from bids, not from prompts. Interface depth: the orchestrator public surface stays `run_team(session, runtime, cfg, instance) -> TeamOutcome` (architecture plan section 2), and the Commander is one more `AgentSlot` with a different tool list.

**Sketch (pseudocode, replaces the code-Commander parts of architecture plan section 2):**
```python
# mca/roles.py
COMMANDER_TOOLS = ("read_board", "broadcast_intent", "accept_bid", "assign", "request_review",
                   "reassign", "close_mission")                 # no read_file/edit_file/run/finish
SQUAD_TOOLS     = ("read_file", "edit_file", "run", "bid", "report", "ask_commander", "verify")
# finish is not a squad tool: the episode ends only by close_mission (Commander) or a guard.

@dataclass(frozen=True)
class TeamConfig:
    squads: int                      # 2..4, chosen per section 4
    max_turns_per_agent: int = 200   # guard, per agent (section 5)
    agent_timeout_sec: int = 14400   # guard, per agent and per episode wall clock (section 5)
    max_identical_failures: int = 3  # editor-retry fix (section 7)

# mca/orchestrator.py
def run_team(session, runtime, cfg, instance) -> TeamOutcome:
    board = Board(run_dir / "host/board.jsonl")
    cmdr = AgentSlot("commander", COMMANDER_PROMPT, tools=COMMANDER_TOOLS)
    squads = [AgentSlot(f"squad-{i}", SQUAD_PROMPT, tools=SQUAD_TOOLS) for i in range(cfg.squads)]
    wake(cmdr, render(board, instance))                      # phase INTENT
    while not closed and not guard_hit():
        slot = next_slot(board)                              # deterministic, one awake slot
        step(slot)                                           # board tools validated by parse()
        park(slot)
    return outcome(board, eval_result)                       # reward = collective anchor (unchanged)
```

## 2. Commander role and prompt contract

**Role.** The Commander turns the problem into one or more `MissionIntent`s, chooses which squad does what, reads squad reports, orders peer verification, and decides when the mission is done. It does not read code, edit code or run tests. It works only through board tools. The plan's "Leadership Capital" (architecture plan section 1) stays: each `reassign` or bid-conflict arbitration costs 1 unit from a starting balance of 10. It is allowed only while the balance is above 5. It is logged, and it never enters `reward`.

**Prompt contract (the system prompt must state each item; a golden test checks the prompt for these clauses):**
1. Identity: "You are the Strategic Commander of a team of N squads on the same model. You coordinate; you never edit code."
2. Inputs it gets on each wake: the problem statement (first wake only), board entries since its last turn, the Leadership Capital balance, and a per-squad status line (turns used and last outcome).
3. Allowed actions are the Commander tools only. It must make exactly one tool call per turn. A free-text reply gets a fail-closed error and counts as a turn.
4. Assignment rule: assign each `MissionIntent` to the squad whose bid best matches, or to a different squad if a squad has reported failure twice on the same intent.
5. Done rule: call `close_mission` only after a squad report that says the visible tests pass **and** a `PeerVerification` from a different squad. The harness still decides the result from the SWE eval (anchor). `close_mission` never sets `Pass` by itself (I-Peer-Non-Terminal, anchors:112).
6. Loop rule: if a squad's report shows the same failure twice (the harness adds `repeat_failure: true`, section 7), reassign the work or narrow the intent. Do not repeat the same assignment.
7. Brevity: every intent is self-contained (files, goal, verification), with a limit of 1500 characters.

**Squad prompt contract (role-neutral, same for every squad, differs only in `squad_id`):** bid on intents with confidence and a short rationale; work only on assigned intents; `report` with a structured summary (`status`, `files`, `tests_run`, `evidence_ref`); `verify` other squads' work when asked, with a fresh history (I-Fresh-Checker, anchors:118); use `ask_commander` instead of guessing.

## 3. Tools and messages between the Commander and squads; assignment and hand-back

All messages go through the board as structured tool calls validated by `parse` (architecture plan section 2: "the only door in"). The message types are the ones already planned (`MissionIntent`, `Bid`, `Claim`, `PeerVerification`, `OwnershipReport`, `Envelope`), plus three new ones:
- `Assignment(mission_id, squad_id, notes)`: the Commander's acceptance of a bid. It replaces the code-side `BidAccepted`.
- `Question(mission_id, squad_id, text)` and `Answer(mission_id, text)`: `ask_commander` and the Commander's reply.
- `Close(mission_id, reason)`: `close_mission`.

| Tool | Who | Writes to the board | Wakes |
|---|---|---|---|
| `broadcast_intent(objective, end_state, constraints)` | Commander | `MissionIntent` | every squad, in squad order, for one `bid` turn each |
| `bid(mission_id, confidence, estimated_turns, rationale)` | squad | `Bid` | Commander once all squads have bid or passed |
| `accept_bid` / `assign(mission_id, squad_id, notes)` | Commander | `Assignment` + `Claim` | the assigned squad |
| `report(mission_id, status, files, tests_run, evidence_ref)` | squad | `OwnershipReport` | Commander |
| `request_review(mission_id, reviewer_squad)` | Commander | review request | reviewer squad (fresh `history_id`) |
| `verify(mission_id, verdict, disagreements, evidence_ref)` | squad | `PeerVerification` | Commander |
| `ask_commander(mission_id, text)` / reply via `assign` notes | squad / Commander | `Question` / `Answer` | the other side |
| `reassign(mission_id, from_squad, to_squad, notes)` | Commander | `Assignment` (costs 1 capital) | new squad |
| `close_mission(reason)` | Commander | `Close` | nobody; episode ends, eval runs |

**Assignment flow.** INTENT → BIDDING (each squad gets exactly one bid turn; it may bid or pass) → CLAIMED (Commander assigns) → WORKING (assigned squad works until it calls `report`) → VERIFYING (a different squad verifies with fresh context) → REPORTING (Commander reads the verdict) → DONE (`close_mission`) or back to WORKING (rework, same squad) or a reassign. If two squads claim the same intent, the Commander arbitrates (1 capital).

**Hand-back.** A squad never hands work directly to another squad. The PR #6 `handoff` tool is removed in this layout. All hand-backs go squad → board → Commander. This puts every coordination decision on the board, where it can be logged.

**Scheduling.** The orchestrator wakes exactly one slot at a time, chosen by the board state (deterministic: the Commander after any squad message, the named squad after any Commander message, bids in squad order). This is the architecture plan's sequential turn contract, unchanged.

## 4. Squad count 2 to 4, and how it is chosen

- The count is a run parameter, `--squads N` with N in {2, 3, 4}. It is fixed for the whole episode and recorded in `result.json` as `team.squads`. The Commander does not change the count during a run. It decides only which squads get work. An unassigned squad stays parked and costs nothing.
- The Commander is an extra agent on top of the squads. N=2 means 3 model agents.
- **How N is chosen for the experiment:** run a small sweep first. Use N ∈ {2, 3, 4}, K=3 episodes each, on flask-5014 plus the two hard xarray instances (4094, 6599). Then fix one N for GMA-394, chosen by highest resolve rate, with lower cost breaking ties. The sweep result is reported to Morgan for Tom's sign-off before GMA-394 (new Tom gate, section 10).
- Default before the sweep: N=2 (the smallest team the plan allows).

## 5. Guards

- Every agent, the Commander included, has its own guard: `--max-turns 200` model turns per agent and `--agent-timeout 14400` s. The time guard is measured from the episode start, so it is also the episode wall-clock guard. This changes PR #6, where the 200 turns were pooled across the team (`lesser_driver.py:844`). Per-agent guards keep the plan's rule that a guard catches a hung agent, not a busy team.
- A team-level backstop: the episode stops if **every** woken agent has hit its turn guard, or when the 14400 s wall clock is reached.
- A guard hit is logged as `guard_stop: {"agent": <name>, "guard": "max_turns"|"agent_timeout"}`. `result.json` also records `guards: {max_turns_per_agent: 200, agent_timeout_sec: 14400}`. An interrupt is not a guard stop (as already done in PR #6).
- When a squad hits its guard, the run continues: the Commander is woken with a `guard_stop` board note and may reassign. When the Commander hits its guard, the episode ends.
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
| **Editor retry loop** (xarray-4094: 140 non-unique `edit_file` retries in one run) | `edit_file` on non-unique text returns the match count, the line numbers and a hint. After 3 identical failures (same path and `old_text`), the squad's turn ends with `repeat_failure: true` in its report. The Commander must then reassign or narrow the intent (prompt contract item 6), instead of the episode ending. |

## 8. Which plan units change

- **Unit 4** (scripted orchestrator): add a scripted Commander (`ScriptedRuntime`) that broadcasts, accepts a bid, requests review and closes. It must also cover N=3 (bids in order, one squad stays parked), a reassign after `repeat_failure` (capital 10 → 9), and a Commander free-text turn (fail-closed error).
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
So every PR #6 result is the fixed 2-role layout with a pooled 200-turn guard, and none has a Commander or board key. Nothing will be confused for, or mixed into, arm 1 data as long as the summarizer keeps filtering on `layout == "team2"`. The future layout name must be different (proposed: `mca`). A `grep` over `harness/` and `docs/` for `commander` finds only the plan docs.

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
- A1-3: Is a team-level wall clock separate from the per-agent 14400 s needed? Default: no. The per-agent clock starts at episode start, so it is already the team clock.
