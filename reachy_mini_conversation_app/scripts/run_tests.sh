#!/usr/bin/env bash
# Run the test suite, failing on anything not listed in tests/known_failures.txt.
#
# known_failures.txt lines:  ignore:<path>    (can't be collected in this environment)
#                            deselect:<nodeid> (already failing before 2026-10-29; fix, then remove)
set -euo pipefail
cd "$(dirname "$0")/.."

args=""
while IFS= read -r line; do
  case "$line" in
    ignore:*) args="$args --ignore=${line#ignore:}" ;;
    deselect:*) args="$args --deselect=${line#deselect:}" ;;
  esac
done < tests/known_failures.txt

# shellcheck disable=SC2086
uv run --no-sync pytest tests -q -p no:cacheprovider $args
