from __future__ import annotations

import json
import os

from jsonschema.validators import Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schemas", "outside-agent-submission.schema.json")


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _evidence_ref() -> dict:
    return {
        "evidence_ref_schema_version": "outside_agent_evidence_ref.v0.1",
        "repo_owner": "Consiliency",
        "repo_name": "spec",
        "immutable_git_ref": "a" * 40,
        "repo_relative_path": "docs/outside-agent-contract.md",
        "digest_algorithm": "sha256",
        "sha256": "b" * 64,
        "source_role": "documentation",
        "source_bundle_refs": [
            {
                "bundle_id": "bundle-1",
                "bundle_manifest_sha256": "c" * 64,
            }
        ],
        "bundle_manifest_sha256": "c" * 64,
        "claimed_path_membership": {
            "proof_type": "bundle_manifest_path",
            "included": True,
        },
        "redaction_posture": "metadata_only",
    }


def _base_payload(kind: str) -> dict:
    payload = {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_id": f"{kind}-1",
        "submission_kind": kind,
        "claim_posture": "claims_only",
        "acceptance_truth_owner": "governed_pipeline",
        "summary": f"summary for {kind}",
        "producer": {
            "agent_name": "outside-agent"
        },
        "evidence_refs": [_evidence_ref()],
    }
    if kind == "work_request":
        payload["work_request"] = {
            "goal": "Implement the requested change",
            "constraints": ["preserve schema boundary"],
        }
    elif kind == "implementation_submission":
        payload["implementation_submission"] = {
            "head_commit_sha": "d" * 40,
            "change_summary": "Implemented the requested change",
        }
    elif kind == "ambiguity_report":
        payload["ambiguity_report"] = {
            "ambiguity_summary": "Two equally plausible interpretations remain",
            "questions": ["Should route verdict stay advisory?"],
        }
    else:
        raise AssertionError(f"unexpected kind: {kind}")
    return payload


def _errors(payload: dict) -> list:
    return sorted(_validator().iter_errors(payload), key=lambda error: list(error.path))


def test_schema_and_minimal_examples() -> None:
    for kind in ("work_request", "implementation_submission", "ambiguity_report"):
        assert not _errors(_base_payload(kind)), kind


def test_rejects_absolute_paths_and_traversal_paths() -> None:
    absolute = _base_payload("implementation_submission")
    absolute["evidence_refs"][0]["repo_relative_path"] = "/tmp/private.txt"
    assert _errors(absolute)

    traversal = _base_payload("implementation_submission")
    traversal["evidence_refs"][0]["repo_relative_path"] = "../private.txt"
    assert _errors(traversal)


def test_rejects_missing_repo_identity_and_hashes() -> None:
    missing_owner = _base_payload("work_request")
    del missing_owner["evidence_refs"][0]["repo_owner"]
    assert _errors(missing_owner)

    missing_git_ref = _base_payload("work_request")
    del missing_git_ref["evidence_refs"][0]["immutable_git_ref"]
    assert _errors(missing_git_ref)

    missing_sha = _base_payload("work_request")
    del missing_sha["evidence_refs"][0]["sha256"]
    assert _errors(missing_sha)

    missing_bundle_manifest = _base_payload("work_request")
    del missing_bundle_manifest["evidence_refs"][0]["bundle_manifest_sha256"]
    assert _errors(missing_bundle_manifest)


def test_rejects_raw_body_provider_payload_secret_and_local_env_fields() -> None:
    for forbidden_field in ("raw_body", "provider_payload", "api_key", "local_env_value"):
        payload = _base_payload("ambiguity_report")
        payload["evidence_refs"][0][forbidden_field] = "forbidden"
        assert _errors(payload), forbidden_field


def test_rejects_wrong_discriminator_payload_pairing() -> None:
    payload = _base_payload("work_request")
    payload["implementation_submission"] = {
        "head_commit_sha": "e" * 40,
        "change_summary": "wrong section",
    }
    assert _errors(payload)


if __name__ == "__main__":
    test_schema_and_minimal_examples()
    test_rejects_absolute_paths_and_traversal_paths()
    test_rejects_missing_repo_identity_and_hashes()
    test_rejects_raw_body_provider_payload_secret_and_local_env_fields()
    test_rejects_wrong_discriminator_payload_pairing()
    print("outside-agent submission schema tests passed")
