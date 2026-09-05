from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema.validators import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consiliency_spec.outside_agent_router import (  # noqa: E402
    SUBMISSION_SCHEMA_TARGET,
    VERDICT_SCHEMA_TARGET,
    blocker_class_of,
    route,
)
VECTOR_ROOT = ROOT / "test-vectors" / "outside-agent"
MANIFEST_PATH = VECTOR_ROOT / "manifest.json"
SUBMISSION_SCHEMA_PATH = ROOT / "schemas" / "outside-agent-submission.schema.json"
VERDICT_SCHEMA_PATH = ROOT / "schemas" / "outside-agent-route-verdict.schema.json"
ALLOWED_TARGETS = {
    "outside_agent_submission.v0.1",
    "outside_agent_route_verdict.v0.1",
}
ALLOWED_VERDICTS = {
    "reject",
    "needs_clarification",
    "roadmap_intake",
    "review_candidate",
}
ALLOWED_BLOCKER_CLASSES = {
    "none",
    "missing_information",
    "unsafe_evidence_reference",
    "unsupported_submission_kind",
    "ambiguous_request",
    "policy_gap",
}


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _validator(path: Path) -> Draft202012Validator:
    schema = _load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


SUBMISSION_VALIDATOR = _validator(SUBMISSION_SCHEMA_PATH)
VERDICT_VALIDATOR = _validator(VERDICT_SCHEMA_PATH)


def _manifest() -> dict:
    return _load_json(MANIFEST_PATH)


def _manifest_entries() -> list[dict]:
    manifest = _manifest()
    assert manifest["manifest_schema_version"] == "outside_agent_vector_manifest.v0.1"
    entries = manifest["vectors"]
    assert entries, "manifest must not be empty"
    return entries


def _vector_path(entry: dict) -> Path:
    path = ROOT / entry["path"]
    assert path.exists(), f"missing vector file: {entry['path']}"
    assert path.is_file(), f"vector path must be a file: {entry['path']}"
    assert path.parent == VECTOR_ROOT, f"vector must stay under {VECTOR_ROOT}: {entry['path']}"
    return path


def _schema_errors(entry: dict, payload: dict) -> list:
    validator = (
        SUBMISSION_VALIDATOR
        if entry["schema_target"] == "outside_agent_submission.v0.1"
        else VERDICT_VALIDATOR
    )
    return sorted(validator.iter_errors(payload), key=lambda error: list(error.path))


def _semantic_errors(entry: dict, payload: dict) -> list[str]:
    if entry.get("negative_reason") != "source_bundle_mismatch":
        return []
    mismatches: list[str] = []
    for evidence_ref in payload.get("evidence_refs", []):
        top_level_digest = evidence_ref.get("bundle_manifest_sha256")
        for source_bundle in evidence_ref.get("source_bundle_refs", []):
            if source_bundle.get("bundle_manifest_sha256") != top_level_digest:
                mismatches.append("bundle manifest digest mismatch")
    return mismatches


def test_manifest_has_unique_case_ids_and_expected_fields() -> None:
    entries = _manifest_entries()
    case_ids = [entry["case_id"] for entry in entries]
    assert len(case_ids) == len(set(case_ids)), "case_id values must be unique"

    for entry in entries:
        assert entry["schema_target"] in ALLOWED_TARGETS
        assert entry["expected_verdict"] in ALLOWED_VERDICTS
        assert entry["expected_blocker_class"] in ALLOWED_BLOCKER_CLASSES
        assert isinstance(entry["expected_valid"], bool)
        if entry["expected_valid"]:
            assert "negative_reason" not in entry
            assert entry["expected_blocker_class"] == "none"
        else:
            assert entry["expected_verdict"] == "reject"
            assert entry["negative_reason"]


def test_manifest_paths_exist_and_every_vector_is_manifested() -> None:
    manifest_paths = set()
    for entry in _manifest_entries():
        vector_path = _vector_path(entry)
        manifest_paths.add(vector_path.relative_to(ROOT).as_posix())

    actual_paths = {
        path.relative_to(ROOT).as_posix()
        for path in VECTOR_ROOT.glob("*.json")
        if path.name != "manifest.json"
    }
    assert actual_paths == manifest_paths, "manifest must list every JSON vector file exactly once"


def test_vectors_match_expected_schema_and_semantic_outcomes() -> None:
    for entry in _manifest_entries():
        payload = _load_json(_vector_path(entry))
        schema_errors = _schema_errors(entry, payload)
        semantic_errors = _semantic_errors(entry, payload)

        if entry["expected_valid"]:
            assert not schema_errors, entry["case_id"]
            assert not semantic_errors, entry["case_id"]
        else:
            assert schema_errors or semantic_errors, entry["case_id"]
            if entry["negative_reason"] == "source_bundle_mismatch":
                assert semantic_errors == ["bundle manifest digest mismatch"]


def test_positive_vectors_cover_every_submission_kind() -> None:
    positive_kinds = {
        entry["submission_kind"]
        for entry in _manifest_entries()
        if entry["expected_valid"]
    }
    assert positive_kinds == {
        "work_request",
        "implementation_submission",
        "ambiguity_report",
    }


def test_negative_vectors_cover_required_reasons() -> None:
    negative_reasons = {
        entry["negative_reason"]
        for entry in _manifest_entries()
        if not entry["expected_valid"]
    }
    assert negative_reasons == {
        "raw_payload",
        "missing_digest",
        "source_bundle_mismatch",
        "unsupported_verdict",
        "unknown_producer_identity",
        "path_traversal",
        "empty_evidence_refs",
        "malformed_git_object_id",
    }


def test_router_derives_the_manifest_verdict_for_every_vector() -> None:
    """The corpus is an oracle only if a verdict is actually computed.

    Every vector is routed through the reference router and the DERIVED route
    and blocker class are compared to the manifest. Before this, the manifest's
    `expected_verdict` was only checked against a vocabulary, so a wrong verdict
    could not fail the suite.
    """
    for entry in _manifest_entries():
        payload = _load_json(_vector_path(entry))
        verdict = route(payload, entry["schema_target"])

        assert verdict["route"] == entry["expected_verdict"], (
            f"{entry['case_id']}: derived route {verdict['route']!r} "
            f"!= manifest {entry['expected_verdict']!r}"
        )
        assert blocker_class_of(verdict) == entry["expected_blocker_class"], (
            f"{entry['case_id']}: derived blocker {blocker_class_of(verdict)!r} "
            f"!= manifest {entry['expected_blocker_class']!r}"
        )
        assert not list(
            VERDICT_VALIDATOR.iter_errors(verdict)
        ), f"{entry['case_id']}: derived verdict is not schema-valid"


def test_router_is_falsifiable() -> None:
    """Guard the guard: a corrupted vector must move the derived verdict.

    If mutating a positive vector into an invalid one left the route unchanged,
    the comparison above would be vacuous.
    """
    positive = next(e for e in _manifest_entries() if e["expected_valid"])
    payload = _load_json(_vector_path(positive))
    assert route(payload, positive["schema_target"])["route"] == positive["expected_verdict"]

    corrupted = json.loads(json.dumps(payload))
    corrupted["evidence_refs"] = []
    corrupted_verdict = route(corrupted, positive["schema_target"])
    assert corrupted_verdict["route"] == "reject"
    assert blocker_class_of(corrupted_verdict) == "missing_information"


def test_derived_verdicts_never_grant_acceptance() -> None:
    """Intake routing must not be able to express merge acceptance."""
    for entry in _manifest_entries():
        verdict = route(_load_json(_vector_path(entry)), entry["schema_target"])
        assert verdict["acceptance_truth_owner"] == "governed_pipeline"
        assert verdict["claim_posture"] == "claims_only"
        assert verdict["route"] in ALLOWED_VERDICTS
        assert verdict["route"] != "accepted_for_merge"


def test_verdicts_never_echo_submitted_content() -> None:
    """A rejected value must not be laundered into the verdict document.

    `jsonschema` messages embed the offending value, and for
    `additionalProperties` the offending property NAME. Serializing that into a
    verdict would carry submitted content downstream through a contract that is
    metadata-only by design. Structural validation and safety redaction are
    separate mechanisms: a verdict reports WHERE and WHICH constraint failed,
    never WHAT was submitted.
    """
    marker = "sk-live-51H8xQ2ZzAbCdEfGhIjKlMnOpQrStUvWx"
    base = _load_json(VECTOR_ROOT / "valid-work-request.json")

    def _mutate(fn):
        payload = json.loads(json.dumps(base))
        fn(payload)
        return payload

    cases = {
        "path": _mutate(
            lambda p: p["evidence_refs"][0].__setitem__(
                "repo_relative_path", f"../{marker}/leak.txt"
            )
        ),
        "property_name": _mutate(
            lambda p: p["evidence_refs"][0].__setitem__(marker, "x")
        ),
        "git_ref": _mutate(
            lambda p: p["evidence_refs"][0].__setitem__("immutable_git_ref", marker)
        ),
        "summary_field": _mutate(
            lambda p: (p.__setitem__("summary", marker), p.__setitem__("submission_kind", "nope"))
        ),
        "producer": _mutate(lambda p: p["producer"].__setitem__("agent_name", marker)),
    }

    for name, payload in cases.items():
        verdict = route(payload, SUBMISSION_SCHEMA_TARGET)
        assert marker not in json.dumps(verdict), (
            f"{name}: submitted content leaked into the verdict document"
        )

    verdict_case = {
        "verdict_schema_version": "outside_agent_route_verdict.v0.1",
        "route": marker,
        "claim_posture": "claims_only",
        "acceptance_truth_owner": "governed_pipeline",
    }
    derived = route(verdict_case, VERDICT_SCHEMA_TARGET)
    assert marker not in json.dumps(derived), "verdict path leaked submitted content"


def test_router_never_raises_on_malformed_input() -> None:
    """Malformed types must reject, not crash.

    A router that raises on `evidence_refs: 1` fails open in any caller that
    treats an exception as anything other than a rejection.
    """
    base = _load_json(VECTOR_ROOT / "valid-work-request.json")
    malformed = [
        None,
        7,
        "string",
        [1, 2, 3],
        {},
        {**base, "evidence_refs": 1},
        {**base, "evidence_refs": "x"},
        {**base, "evidence_refs": [1]},
        {**base, "evidence_refs": None},
        {**base, "producer": 1},
        {**base, "submission_kind": None},
    ]
    for payload in malformed:
        verdict = route(payload, SUBMISSION_SCHEMA_TARGET)
        assert verdict["route"] == "reject"
        assert not list(VERDICT_VALIDATOR.iter_errors(verdict))


if __name__ == "__main__":
    test_manifest_has_unique_case_ids_and_expected_fields()
    test_manifest_paths_exist_and_every_vector_is_manifested()
    test_vectors_match_expected_schema_and_semantic_outcomes()
    test_positive_vectors_cover_every_submission_kind()
    test_negative_vectors_cover_required_reasons()
    test_router_derives_the_manifest_verdict_for_every_vector()
    test_router_is_falsifiable()
    test_derived_verdicts_never_grant_acceptance()
    test_verdicts_never_echo_submitted_content()
    test_router_never_raises_on_malformed_input()
    print("outside-agent vector tests passed")
