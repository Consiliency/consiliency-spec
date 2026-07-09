#!/usr/bin/env bash
# XG0 envelope conformance gate.
#
# Pins the spec-side certificate/payload digest-description contract to the live
# canon domain prefix without banning accurate historical v1 references
# elsewhere in the repo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARITY="$(dirname "$HERE")"
ROOT="$(dirname "$PARITY")"
PY="${PYTHON:-python3}"

echo "== XG0 envelope conformance gate =="
echo "root: $ROOT"

"$PY" - "$ROOT" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
canon_py = root / "canon/py/canon.py"
certificate_schema_path = root / "spec-parity/schemas/certificate.schema.json"
payload_schema_path = root / "spec-parity/schemas/portal-payload.schema.json"
semantics_path = root / "spec-parity/SEMANTICS.md"

canon_text = canon_py.read_text(encoding="utf-8")
match = re.search(r"^_DOMAIN_PREFIX\s*=\s*['\"](spec-canon:[^'\"]+)['\"]\s*$", canon_text, re.MULTILINE)
if not match:
    print("FAIL: could not read canon/py/canon.py::_DOMAIN_PREFIX")
    sys.exit(1)

expected_prefix = match.group(1)
print(f"expected_prefix_source: {canon_py.relative_to(root)}::_DOMAIN_PREFIX")
print(f"expected_prefix: {expected_prefix}")
if expected_prefix != "spec-canon:v2:":
    print("FAIL: XG0 expects canon/py/canon.py::_DOMAIN_PREFIX to stay spec-canon:v2:")
    sys.exit(1)

certificate_schema = json.loads(certificate_schema_path.read_text(encoding="utf-8"))
payload_schema = json.loads(payload_schema_path.read_text(encoding="utf-8"))
semantics_text = semantics_path.read_text(encoding="utf-8")

semantics_match = re.search(
    r"^## 9\. Certificate field set\s*\n(?P<body>.*?)(?=^## \d+\. |\Z)",
    semantics_text,
    re.MULTILINE | re.DOTALL,
)
if not semantics_match:
    print("FAIL: could not isolate section 9 in spec-parity/SEMANTICS.md")
    sys.exit(1)

certificate_preimage = f"{expected_prefix}certificate\\n"
payload_preimage = f"{expected_prefix}semantic-content\\n"

checks = [
    (
        "certificate schema root description",
        certificate_schema_path.relative_to(root).as_posix(),
        certificate_schema["description"],
        certificate_preimage,
        "spec-canon:v1:certificate\\n",
    ),
    (
        "certificate schema canon_version description",
        certificate_schema_path.relative_to(root).as_posix(),
        certificate_schema["properties"]["canon_version"]["description"],
        expected_prefix,
        None,
    ),
    (
        "certificate schema digest description",
        certificate_schema_path.relative_to(root).as_posix(),
        certificate_schema["properties"]["digest"]["description"],
        certificate_preimage,
        "spec-canon:v1:certificate\\n",
    ),
    (
        "portal payload digest description",
        payload_schema_path.relative_to(root).as_posix(),
        payload_schema["properties"]["digest"]["description"],
        payload_preimage,
        "spec-canon:v1:semantic-content\\n",
    ),
    (
        "SEMANTICS certificate field section",
        semantics_path.relative_to(root).as_posix(),
        semantics_match.group("body"),
        certificate_preimage,
        "spec-canon:v1:certificate\\n",
    ),
]

failures = []
for label, relpath, text, required, forbidden in checks:
    if required not in text:
        failures.append(f"{label}: missing `{required}` in {relpath}")
    if forbidden and forbidden in text:
        failures.append(f"{label}: stale `{forbidden}` still present in {relpath}")

for label, relpath, _, required, _ in checks:
    print(f"checked: {label} | file={relpath} | required_prefix={required}")

if failures:
    print("FAIL: XG0 envelope contract drift detected")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)

print("PASS: targeted certificate/payload digest descriptions match canon/py/canon.py::_DOMAIN_PREFIX")
PY
