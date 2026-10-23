# Mission-Claim RLCR Experiment Plan

You run this experiment on your local laptop. You compare three arms on the same SWE tasks. You use Docker containers for isolation. You measure whether a Mission-Claim team of lesser-model agents beats a single lesser-model agent and how close either comes to a frontier single agent.

## Goal

You test one claim. A shared policy trained with a collective reward produces useful multi-agent organisations. You map that claim onto the Mission-Claim Architecture. You measure organisation effects separately from model strength.

## Arms

You run three arms on every task.

1. You run an MCA team. One Strategic Commander plus two to four specialist squads. All agents use the same lesser model. They share one container, one workspace, and one message board. The Commander broadcasts MissionIntents. Specialists bid and claim. Peer verification happens before OwnershipReports go up. The only reward you log is the collective outcome.

2. You run a single agent. It uses the same lesser model. It gets the same tools and the same token or wall-clock budget. It runs in its own container.

3. You run a frontier control. It uses grok-4.7 or the latest Sonnet. It runs alone in its own container. You treat it as an upper bound.

A separate strong model judges all three outputs. You blind the judge to the arm. The primary score is automatic: did the tests pass? The judge adds qualitative notes on coordination, completeness, and efficiency.

## Tasks

You start with SWE-bench Verified mini or hard subset. Success is binary. The patch makes FAIL_TO_PASS tests pass and keeps PASS_TO_PASS tests green. You use the OpenReward port (GeneralReasoning/SWE-Bench-Verified) or the official Docker images.

Later you move to SWE-rebench-V2 or SWE-Gym for the training phase. Those give you larger pools of executable tasks with clear rewards.

## Harness

You put a thin Mission-Claim orchestrator on top of OpenReward or SWE-bench sessions. The environment supplies the sandbox, tools, and final reward. Your orchestrator supplies the Commander, squads, protocol messages, and peer verification. You reuse the MCA PoC scaffolding where it exists.

## Containers

You use Docker Desktop. You give each arm its own identical container. The MCA team shares one container so agents can coordinate on the same files. You mount a host directory only for logs and results. You destroy the container after each task.

## RLCR phase

After the three-arm evaluation is stable, you add a light collective update only on arms 1 and 2. You collect rollouts. You assign the environment reward as a single advantage to every token from every agent in the episode. You update the shared policy with a CISPO- or GRPO-style step. You re-run the evaluation. You measure whether the team pulls further ahead of the single lesser agent.

## Success criteria

You look for two signals. The MCA team scores higher than the single lesser agent on the same model. After collective training, the gap grows or the team closes distance to the frontier control. You also log role emergence, peer-review frequency, and Leadership Capital usage.

## Next steps

You stand up one OpenReward or SWE-bench session. You confirm a single lesser-model agent can finish a task. You port the MCA command layer so it drives the same session. You run the three arms on a small task set. You only then add the collective-advantage loop.
