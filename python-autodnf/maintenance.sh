#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"
exec .venv/bin/python -m autodnf_py maintenance --execute --debug
