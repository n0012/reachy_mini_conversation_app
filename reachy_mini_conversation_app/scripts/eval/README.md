# Rolling changes forward without regressions

One change per commit, each proven against what the robot runs now:

1. **Commit** a single change (`ruff` and `scripts/run_tests.sh` must pass).
2. **Eval**: `scripts/eval/run_eval.sh [--target <metric>]` — three replayed conversations
   on the robot with the candidate code, then `gate.py` compares them with the baseline.
3. **Deploy**: `scripts/robot_deploy.sh [--restart]` — refuses unless the eval passed for
   that exact commit, and refuses anything that isn't a descendant of the deployed commit.
   A successful deploy makes that commit the new baseline (the ratchet).

`scripts/robot_deploy.sh --rollback <commit>` is the deliberate way back; `--status` and
`--verify` show what the robot runs.

## What the eval does

`harness.py` runs on the robot from `/tmp` (the installed app keeps running) and drives the
real `GeminiLiveHandler` with synthesized speech streamed in real time: greeting, identity,
two memory questions, weather, web lookup, 90 s of quiet, a TV clip, side conversation,
"go to sleep" (`scenarios.json`).

It is read-only: Memory Bank writes are blocked (only GET and `memories:retrieve` pass),
Home Assistant / calendar calls other than reads are stubbed, the active-user file and the
profile are temp copies, and the robot does not move.

## The gate

- **Code metrics**, summed over runs — heard, replied, names recalled, slept when told,
  unprompted speech, placeholders shown, tool echoes shown, restarts, errors, blocked
  Home Assistant writes, median reply latency. Each must be no worse than the baseline
  (small slack where runs are noisy).
- **Judge** (`gemini-4-argon-high` via `agy`): rubric scores per run (`rubric.md`) must not
  drop more than 0.5, and a blind A/B with randomized order must not prefer the baseline.
- `--target <metric>` additionally requires that metric to improve.

## Local files (never committed — they hold personal conversation content)

- `~/.reachy_eval/expectations.json` — `start_user`, `identity_reply`, `recall_names`
- `~/.reachy_eval/audio/` — synthesized clips (`uv run --no-sync python scripts/eval/make_audio.py`)
- `~/.reachy_eval/results/<sha>/` — runs, `verdict.json`; `~/.reachy_eval/baseline`

Changing the harness, scenarios or audio changes the harness id, so the baseline must be
re-run before candidates can be compared with it.
