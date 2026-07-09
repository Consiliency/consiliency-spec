#!/usr/bin/env bash
# spec-parity DELIVERY conformance gate — the Phase-0C EXIT GATE.
#
# Asserts the metadata-only certificate -> portal-payload contract (SEMANTICS.md sec 12):
#   - the certificate schema validates REAL parity.py output and is frozen (schema_version=="1"),
#   - the payload validates against schemas/portal-payload.schema.json,
#   - the projection deliver(certificate, finding_set) -> payload is BYTE-REPRODUCIBLE across two
#     INDEPENDENT process invocations (canon determinism),
#   - the METADATA-ONLY guarantee holds: a certificate+finding carrying synthetic evidence/secret/
#     source/message yields a payload with NONE of it, byte-identical to the clean payload (proven by
#     test_deliver.py: test_metadata_only_guarantee_redaction_holds),
#   - the payload digest is stable + a real canon semantic-content digest of the payload content.
#
# NO LLM anywhere. Pure deterministic Python (python3 + jsonschema). Exit 0 = green gate.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARITY="$(dirname "$HERE")"
ROOT="$(dirname "$PARITY")"
ENGINE="$ROOT/spec-engine"

PY="${PYTHON:-python3}"

echo "== spec-parity delivery (portal-payload) conformance gate =="
echo "root: $ROOT"
echo

# 1) Full delivery test suite (schema validation, byte-reproducibility, redaction proof, badge rule).
echo "-- [1/3] test_deliver.py (payload schema, reproducibility, metadata-only redaction proof) --"
cd "$ENGINE"
"$PY" test_deliver.py
echo

# 2) Byte-reproducibility of the PAYLOAD across two INDEPENDENT process invocations (CLI path).
echo "-- [2/3] payload byte-reproducibility across two runs --"
RUN1="$(mktemp)"; RUN2="$(mktemp)"
trap 'rm -f "$RUN1" "$RUN2"' EXIT
"$PY" run_engine.py fixtures/pmcp-run.json --emit payload > "$RUN1"
"$PY" run_engine.py fixtures/pmcp-run.json --emit payload > "$RUN2"
if ! cmp -s "$RUN1" "$RUN2"; then
  echo "FAIL: payload is NOT byte-identical across runs"
  diff <(cat "$RUN1") <(cat "$RUN2") || true
  exit 1
fi
PDIGEST="$("$PY" -c "import json; print(json.load(open('$RUN1'))['digest'])")"
PBADGE="$("$PY" -c "import json; print(json.load(open('$RUN1'))['badge'])")"
echo "PASS: payload byte-identical across two runs"
echo "      payload digest : $PDIGEST"
echo "      badge          : $PBADGE"
echo

# 3) Empty-frontier delivery guard: the not_applicable payload (the one shape test_deliver.py does
#    not cover) VALIDATES against the schema AND renders badge neutral (NEVER silent green).
echo "-- [3/3] empty-frontier delivery guard (schema-valid + badge == neutral, never green) --"
EMPTY_BADGE="$("$PY" run_engine.py fixtures/empty-run.json --emit payload \
  | "$PY" -c "
import json, sys, os
sys.path.insert(0, os.path.join('$ROOT', 'spec-engine'))
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
sd = os.path.join('$ROOT', 'spec-parity', 'schemas')
res = []
for f in ('result-state.schema.json','portal-payload.schema.json'):
    s = json.load(open(os.path.join(sd, f)))
    r = Resource.from_contents(s, default_specification=DRAFT202012)
    res += [(s['\$id'], r), (f, r)]
reg = Registry().with_resources(res)
p = json.load(sys.stdin)
Draft202012Validator(json.load(open(os.path.join(sd,'portal-payload.schema.json'))), registry=reg).validate(p)
assert p['overall_result_state']=='not_applicable', p['overall_result_state']
print(p['badge'])
")"
if [ "$EMPTY_BADGE" != "neutral" ]; then
  echo "FAIL: empty-frontier badge is '$EMPTY_BADGE', expected neutral (not_applicable is non-green)"
  exit 1
fi
echo "PASS: empty-frontier payload schema-valid + badge == neutral (not_applicable rendered non-green)"
echo

echo "== GATE GREEN =="
