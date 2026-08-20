#!/usr/bin/env bash
# Bedrock Guardrails capability demo runner.
set -euo pipefail
cd "$(dirname "$0")"
export DEMO_REGION="${DEMO_REGION:-us-east-1}"
export PYTHONPATH="$PWD/src"
exec python3 src/run_demo.py "$@"
