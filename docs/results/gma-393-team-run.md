# GMA-393 two-agent (MCA) team run — paid poolside/laguna-s-2.1 via OpenRouter

Generated 2026-10-24 15:49 MDT by harness/summarize_team_runs.py from run dirs under /Users/Shared/gman-sandbox/mission-claim-rlcr/runs/ (team2 layout, started 2026-10-24 19:25Z or later). Guards: --max-turns 200 (pooled), --agent-timeout 14400 s.

| instance | resolved | mean wall (max) | tool-call success | cost | 429 hits | guard_stop | stop reasons |
|---|---|---|---|---|---|---|---|
| pallets__flask-5014 | 5/5 | 23s (max 29) | 51/51 = 1.000 | $0.0042 | 0 | [] | ['finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted'] |
| pydata__xarray-4094 | 1/5 | 539s (max 682) | 847/848 = 0.999 | $0.5136 | 0 | ['max_turns', 'max_turns', 'max_turns', 'max_turns'] | ['turn_limit', 'finish_rejected_final', 'turn_limit', 'turn_limit', 'turn_limit'] |
| pydata__xarray-4629 | 5/5 | 32s (max 36) | 45/45 = 1.000 | $0.0043 | 0 | [] | ['finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted'] |
| pydata__xarray-6599 | 1/5 | 781s (max 1059) | 837/848 = 0.987 | $0.7483 | 0 | ['max_turns', 'max_turns', 'max_turns', 'max_turns'] | ['llm_error', 'turn_limit', 'turn_limit', 'turn_limit', 'turn_limit'] |
| pytest-dev__pytest-5262 | 5/5 | 152s (max 264) | 198/202 = 0.980 | $0.0344 | 0 | [] | ['finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted'] |
| pytest-dev__pytest-7982 | 5/5 | 46s (max 55) | 85/86 = 0.988 | $0.0083 | 0 | [] | ['finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted', 'finish_accepted'] |
TOTAL [22, 30, 7866.174, 2063, 17, 1.3131800000000002, 0]

Single-agent laguna baseline (PR #5, 1 episode each): 5/8 — flask-5014, xarray-4629, pytest-5262, pytest-7432, pytest-7982 resolved; xarray-4094, xarray-6599, pytest-7490 not. flask-5014 earlier smoke 5/5 (PR #4). pytest-7432/7490 are excluded from the team set (ambiguous dataset id).

Excluded: ABORTED-p100bug-pytest-dev__pytest-5262-agent-20261024T192947Z (run before the [100%] visible-test fix).
