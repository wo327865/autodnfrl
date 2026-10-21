#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"

mkdir -p logs
run_log="$(mktemp "$script_dir/logs/run-all.XXXXXX")"
phase="battle"
started=$SECONDS
notify_exit() {
    result=$?
    trap - EXIT
    .venv/bin/python -m autodnf_py.notify --exit-code "$result" \
        --phase "$phase" --elapsed "$((SECONDS - started))" --log "$run_log" || true
    exit "$result"
}
trap notify_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
echo "Run log: $run_log"
.venv/bin/python -u -m autodnf_py run --execute --battle --debug 2>&1 | tee -a "$run_log"
echo "Battle workflow complete; starting mail and dismantle maintenance."
phase="maintenance"
.venv/bin/python -u -m autodnf_py maintenance --execute --debug 2>&1 | tee -a "$run_log"
phase="complete"
