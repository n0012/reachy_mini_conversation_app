#!/usr/bin/env bash
# Deploy the committed app code to the robot — forward only, gated, recorded.
#
#   scripts/robot_deploy.sh [--restart]              deploy HEAD (must pass gates + eval)
#   scripts/robot_deploy.sh --override "<reason>"    deploy HEAD without a passing eval verdict (recorded)
#   scripts/robot_deploy.sh --rollback <commit>      deploy an older commit on purpose
#   scripts/robot_deploy.sh --init                   first deploy: record HEAD as the baseline
#   scripts/robot_deploy.sh --status                 show what the robot is running
#   scripts/robot_deploy.sh --verify [commit]        compare the robot with a commit (default HEAD)
#
# Guarantees:
#   * Only committed code is shipped (git archive of the commit, never the working tree).
#   * Refuses to deploy a commit that is not a descendant of the deployed one, unless
#     --rollback is given — so an improvement can never silently downgrade the robot.
#   * Refuses a forward deploy unless ruff, the test suite and the replay eval passed
#     for that exact commit (eval verdict in ~/.reachy_eval/results/<sha>/verdict.json).
#   * Refuses a forward deploy of a commit that isn't pushed to GitHub.
#   * Never touches ~/.reachy_mini_gemini_live (profiles, .env, active user) or memories.
#   * Backs up the installed package before replacing it.
#
# Env: ROBOT (default pollen@reachy-mini.local), ROBOT_KEY (default ~/.ssh/reachy_mini_ed25519)
set -euo pipefail

ROBOT="${ROBOT:-pollen@reachy-mini.local}"
ROBOT_KEY="${ROBOT_KEY:-$HOME/.ssh/reachy_mini_ed25519}"
PKG_DIR="/venvs/apps_venv/lib/python3.12/site-packages/reachy_mini_gemini_live"
STATE_DIR="/home/pollen/.reachy_deploy"
APP_NAME="reachy_mini_gemini_live"
EVAL_RESULTS="${REACHY_EVAL_DIR:-$HOME/.reachy_eval}/results"

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(git -C "$APP_ROOT" rev-parse --show-toplevel)"
PKG_PATH_IN_REPO="$(cd "$APP_ROOT" && git rev-parse --show-prefix)src/reachy_mini_gemini_live"

robot() { ssh -i "$ROBOT_KEY" -o ConnectTimeout=10 "$ROBOT" "$@"; }
die() { echo "✗ $*" >&2; exit 1; }
say() { echo "▸ $*"; }

deployed_commit() { robot "cat $STATE_DIR/DEPLOYED 2>/dev/null || true"; }

# shellcheck source=scripts/lib_package.sh
. "$APP_ROOT/scripts/lib_package.sh"

# Compare the robot's installed package with commit $1 (behaviour-level).
verify_robot() {
  local commit="$1" tmp
  tmp="$(mktemp -d)"
  build_package "$commit" "$tmp/git"
  mkdir -p "$tmp/robot"
  robot "tar -c -C $PKG_DIR --exclude=__pycache__ ." | tar -x -C "$tmp/robot"
  (cd "$APP_ROOT" && uv run --no-sync python scripts/compare_trees.py "$tmp/robot" "$tmp/git")
  local rc=$?
  rm -rf "$tmp"
  return $rc
}

mode="forward"
target=""
restart=0
override=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rollback) mode="rollback"; target="${2:-}"; [ -n "$target" ] || die "--rollback needs a commit"; shift ;;
    --init) mode="init" ;;
    --status) mode="status" ;;
    --verify) mode="verify"; target="${2:-}"; [ -n "$target" ] && shift ;;
    --restart) restart=1 ;;
    --override) override="${2:-}"; [ -n "$override" ] || die "--override needs a reason"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

if [ "$mode" = "status" ]; then
  current="$(deployed_commit)"
  echo "deployed: ${current:-<none recorded>}"
  [ -n "$current" ] && git -C "$REPO_ROOT" log -1 --format='          %h %s (%cr)' "$current" 2>/dev/null || true
  robot "tail -5 $STATE_DIR/history.log 2>/dev/null || true"
  exit 0
fi

sha="$(git -C "$REPO_ROOT" rev-parse "${target:-HEAD}^{commit}")" || die "unknown commit: ${target:-HEAD}"
short="$(git -C "$REPO_ROOT" rev-parse --short "$sha")"

if [ "$mode" = "verify" ]; then
  say "comparing the robot's installed package with $short"
  verify_robot "$sha"
  exit $?
fi

# 1. Only committed code, and the checkout must match it when deploying HEAD.
if [ "$mode" != "rollback" ]; then
  dirty="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "working tree has uncommitted changes — commit first"
fi

# 2. Forward-only check.
current="$(deployed_commit)"
case "$mode" in
  init)
    [ -z "$current" ] || die "robot already records $current — use a normal deploy or --rollback"
    ;;
  forward)
    [ -n "$current" ] || die "robot has no recorded deploy — run --init once"
    if [ "$current" = "$sha" ]; then
      say "robot already runs $short — nothing to do"
      exit 0
    fi
    git -C "$REPO_ROOT" merge-base --is-ancestor "$current" "$sha" ||
      die "$short does not build on the deployed $(git -C "$REPO_ROOT" rev-parse --short "$current") — refusing to downgrade (use --rollback to do it on purpose)"
    ;;
  rollback)
    say "ROLLBACK to $short (currently ${current:-unknown})"
    ;;
esac

# 3. Gates: ruff + tests on the exact commit (skipped for an explicit rollback).
if [ "$mode" != "rollback" ]; then
  say "ruff"
  (cd "$APP_ROOT" && uv run --no-sync ruff check src tests && uv run --no-sync ruff format --check src tests) ||
    die "ruff failed"
  say "tests"
  "$APP_ROOT/scripts/run_tests.sh" || die "tests failed"
fi

# 3b. The robot only runs code that is on GitHub.
if [ "$mode" = "forward" ]; then
  git -C "$REPO_ROOT" fetch -q origin || die "could not fetch origin to check $short is pushed"
  [ -n "$(git -C "$REPO_ROOT" branch -r --contains "$sha")" ] ||
    die "$short is not on GitHub — push it first (git push)"
fi

# 4. Eval verdict required for forward deploys.
if [ "$mode" = "forward" ] && [ -n "$override" ]; then
  say "OVERRIDE: deploying $short without a passing eval verdict — $override"
elif [ "$mode" = "forward" ]; then
  verdict="$EVAL_RESULTS/$sha/verdict.json"
  [ -f "$verdict" ] || die "no eval verdict for $short — run scripts/eval/run_eval.sh first"
  grep -q '"pass": true' "$verdict" || die "eval did not pass for $short — see $verdict"
  # Chained (--against) verdicts can stack tolerances; the deployed commit must be the reference.
  grep -q "\"baseline\": \"$current\"" "$verdict" ||
    die "$short was not gated against the deployed $(git -C "$REPO_ROOT" rev-parse --short "$current") — run scripts/eval/gate.py --candidate $sha"
fi

# 5. Stage the committed package on the robot, back up the installed one, swap.
stage="/tmp/deploy-$short"
local_stage="$(mktemp -d)/pkg"
say "building $short (with Git LFS files)"
build_package "$sha" "$local_stage"
say "staging $short on the robot"
robot "rm -rf $stage && mkdir -p $stage"
tar --no-mac-metadata -c -C "$local_stage" . | robot "tar -x -C $stage"
rm -rf "$(dirname "$local_stage")"
robot "test -s $stage/gemini_live.py && test \$(stat -c %s $stage/models/hey-reachy-wake-word-detection-linux-aarch64.eim) -gt 1000000" ||
  die "staged package looks wrong (missing code or wake-word model)"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
say "backing up the installed package"
robot "mkdir -p $STATE_DIR/backups && tar -czf $STATE_DIR/backups/${ts}-${current:-unknown}.tgz -C $(dirname "$PKG_DIR") $(basename "$PKG_DIR")"

say "installing"
robot "set -e; rm -rf ${PKG_DIR}.new; cp -a $stage ${PKG_DIR}.new; rm -rf ${PKG_DIR}.old; mv $PKG_DIR ${PKG_DIR}.old; mv ${PKG_DIR}.new $PKG_DIR; rm -rf ${PKG_DIR}.old $stage"
robot "/venvs/apps_venv/bin/python -c 'import reachy_mini_gemini_live.gemini_live, reachy_mini_gemini_live.main' >/dev/null 2>&1" ||
  die "import check failed on the robot — restore with: --rollback ${current:-<previous>}"

note=""
[ -n "$override" ] && note=" override: ${override//\'/}"
robot "echo $sha > $STATE_DIR/DEPLOYED; echo '$ts $mode $sha$note' >> $STATE_DIR/history.log"
# What the robot runs is what the next candidate must beat (the eval ratchet).
[ "$mode" = "forward" ] && echo "$sha" >"${REACHY_EVAL_DIR:-$HOME/.reachy_eval}/baseline"
say "robot now runs $short ($(git -C "$REPO_ROOT" log -1 --format=%s "$sha"))"

# 6. Optional restart; otherwise the new code loads on the next app start.
if [ "$restart" = 1 ]; then
  say "restarting the app"
  robot "curl -s -X POST localhost:8000/api/apps/stop-current-app >/dev/null; for i in \$(seq 1 20); do [ \"\$(curl -s localhost:8000/api/apps/current-app-status)\" = null ] && break; sleep 2; done; curl -s -X POST localhost:8000/api/apps/start-app/$APP_NAME >/dev/null"
  say "started — watch with: ssh $ROBOT journalctl -u reachy-mini-daemon -f"
else
  say "not restarted — new code loads on the next app start"
fi
