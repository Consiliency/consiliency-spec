#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for path in test-vectors/outside-agent/*.json; do
  python3 -m json.tool "$path" >/dev/null
done

python3 tests/test_outside_agent_submission_schema.py
python3 tests/test_outside_agent_route_verdict_schema.py
python3 tests/test_outside_agent_vectors.py
