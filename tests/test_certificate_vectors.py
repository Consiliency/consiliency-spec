"""Public certificate vectors: consiliency_spec.verify_certificate over test-vectors/certificate/.

Run by scripts/check_certificate_vectors.sh (part of the release gate). Every case in the vector
manifest must produce exactly the outcome it declares: the untouched certificate verifies and binds to
the desired graph, E(C), finding set and payload shipped with it; the tampered copy fails, on exactly
the declared checks. Needs the `verify` extra (jsonschema, unicodedata2==16.0.0).
"""

from __future__ import annotations

import json
from pathlib import Path

from consiliency_spec import verify_certificate

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "test-vectors" / "certificate"
INPUTS = ("desired_graph", "ec", "finding_set", "payload")


def _bytes(rel: str) -> bytes:
    return (VECTORS / rel).read_bytes()


def check_case(case: dict) -> None:
    kwargs = {key: _bytes(case[key]) for key in INPUTS if key in case}
    result = verify_certificate(_bytes(case["certificate"]),
                                require_authoritative=case["require_authoritative"], **kwargs)
    expect = case["expect"]
    got = {
        "valid": result.valid,
        "bound": result.bound,
        "authoritative": result.authoritative,
        "advisory": result.advisory,
        "spec_authority": result.spec_authority,
        "overall_result_state": result.overall_result_state,
        "failed_checks": sorted(check.name for check in result.failures),
    }
    if got != {**expect, "failed_checks": sorted(expect["failed_checks"])}:
        raise AssertionError("%s: expected %s, got %s; checks: %s"
                             % (case["id"], expect, got, [c.to_dict() for c in result.checks]))
    if not all(check.status in ("pass", "fail") for check in result.checks):
        raise AssertionError("%s: a check was neither pass nor fail" % case["id"])


def test_certificate_vectors() -> None:
    manifest = json.loads((VECTORS / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    if not cases or not any(c["expect"]["valid"] for c in cases) or all(c["expect"]["valid"]
                                                                          for c in cases):
        raise AssertionError("the vector set must hold at least one valid and one invalid case")
    for case in cases:
        check_case(case)


if __name__ == "__main__":
    test_certificate_vectors()
    print("certificate vector tests passed")
