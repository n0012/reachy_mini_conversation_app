#!/usr/bin/env bash
# Replay-eval a committed version on the robot, then gate it against the baseline.
#
#   scripts/eval/run_eval.sh                       eval HEAD vs the baseline -> verdict.json
#   scripts/eval/run_eval.sh --commit <sha>        eval another commit
#   scripts/eval/run_eval.sh --baseline            eval and record this commit as the baseline
#   scripts/eval/run_eval.sh --target <metric>     also require <metric> to improve
#   scripts/eval/run_eval.sh --profile-overlay D   lay profile files from D over the robot's profile
#   scripts/eval/run_eval.sh --smoke               one run, no judge, no verdict (harness check)
#
# The candidate package runs from /tmp on the robot (the installed app is untouched and
# keeps running); memory, Home Assistant and calendar are read-only during the eval
# (see harness.py). Results — which contain personal conversation content — stay in
# ~/.reachy_eval/results/<sha>/ and are never committed.
#
# Setup once: ~/.reachy_eval/expectations.json, then uv run --no-sync python scripts/eval/make_audio.py
set -euo pipefail

ROBOT="${ROBOT:-pollen@reachy-mini.local}"
ROBOT_KEY="${ROBOT_KEY:-$HOME/.ssh/reachy_mini_ed25519}"
EVAL_DIR="${REACHY_EVAL_DIR:-$HOME/.reachy_eval}"
ROBOT_PY="/venvs/apps_venv/bin/python"

APP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REPO_ROOT="$(git -C "$APP_ROOT" rev-parse --show-toplevel)"
PKG_PATH_IN_REPO="$(cd "$APP_ROOT" && git rev-parse --show-prefix)src/reachy_mini_gemini_live"

robot() { ssh -i "$ROBOT_KEY" -o ConnectTimeout=10 "$ROBOT" "$@"; }
die() { echo "✗ $*" >&2; exit 1; }
say() { echo "▸ $*"; }
# shellcheck source=scripts/lib_package.sh
. "$APP_ROOT/scripts/lib_package.sh"

commit="HEAD"
runs=3
gate_args=()
overlay=""
smoke=0
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) commit="${2:?}"; shift ;;
    --runs) runs="${2:?}"; shift ;;
    --baseline) gate_args+=(--baseline) ;;
    --target) gate_args+=(--target "${2:?}"); shift ;;
    --profile-overlay) overlay="$(cd "${2:?}" && pwd)"; shift ;;
    --smoke) smoke=1; runs=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[ -f "$EVAL_DIR/expectations.json" ] || die "missing $EVAL_DIR/expectations.json"
[ -f "$EVAL_DIR/audio/manifest.json" ] || die "no scenario audio — run scripts/eval/make_audio.py"

sha="$(git -C "$REPO_ROOT" rev-parse "$commit^{commit}")"
short="$(git -C "$REPO_ROOT" rev-parse --short "$sha")"
# The harness and scenarios are part of the measurement: results are only comparable
# when they were produced by the same harness version.
harness_id="$(cat "$APP_ROOT/scripts/eval/harness.py" "$APP_ROOT/scripts/eval/scenarios.json" \
  "$EVAL_DIR/audio/manifest.json" ${overlay:+$(find "$overlay" -type f | sort)} | shasum | cut -c1-12)"
results="$EVAL_DIR/results/$sha"
[ "$smoke" = 1 ] && results="$EVAL_DIR/smoke/$sha"

remote="/tmp/reachy-eval-$short"
say "building $short"
local_tmp="$(mktemp -d)"
build_package "$sha" "$local_tmp/pkg/reachy_mini_gemini_live"
mkdir -p "$local_tmp/harness" "$local_tmp/audio"
cp "$APP_ROOT/scripts/eval/harness.py" "$APP_ROOT/scripts/eval/scenarios.json" "$local_tmp/harness/"
cp "$EVAL_DIR/expectations.json" "$local_tmp/harness/"
cp "$EVAL_DIR/audio/manifest.json" "$EVAL_DIR"/audio/step-*.wav "$local_tmp/audio/"
[ -n "$overlay" ] && cp -R "$overlay" "$local_tmp/overlay"

say "staging on the robot ($remote)"
robot "rm -rf $remote && mkdir -p $remote"
tar --no-mac-metadata -c -C "$local_tmp" . | robot "tar -x -C $remote"
rm -rf "$local_tmp"

say "running $runs conversation(s) in parallel (~7 min)"
overlay_arg=""
[ -n "$overlay" ] && overlay_arg="--profile-overlay $remote/overlay"
robot "cd $remote && for i in \$(seq 1 $runs); do
  mkdir -p out/run\$i
  PYTHONPATH=$remote/pkg $ROBOT_PY -u harness/harness.py --scenarios harness/scenarios.json --audio audio \
    --expectations harness/expectations.json --out out/run\$i $overlay_arg >out/run\$i/stdout.txt 2>&1 &
  sleep 2
done; wait" || true

say "collecting results"
rm -rf "$results" && mkdir -p "$results"
robot "tar -c -C $remote/out ." | tar -x -C "$results"
robot "rm -rf $remote"
echo "$harness_id" >"$results/harness_id"
for i in $(seq 1 "$runs"); do
  grep -q "$remote/pkg/reachy_mini_gemini_live" "$results/run$i/events.jsonl" 2>/dev/null ||
    die "run$i did not run the staged package — see $results/run$i/stdout.txt"
  tail -1 "$results/run$i/stdout.txt"
done

if [ "$smoke" = 1 ]; then
  say "smoke run done: $results/run1/transcript.txt"
  exit 0
fi
(cd "$APP_ROOT" && uv run --no-sync python scripts/eval/gate.py --candidate "$sha" ${gate_args[@]+"${gate_args[@]}"})
