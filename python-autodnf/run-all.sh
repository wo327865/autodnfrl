#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"

.venv/bin/python -m autodnf_py run --execute --battle --debug
echo "Battle workflow complete; starting mail and dismantle maintenance."
exec .venv/bin/python -m autodnf_py maintenance --execute --debug
