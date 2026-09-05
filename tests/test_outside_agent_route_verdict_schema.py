from __future__ import annotations

import copy
import json
import os

from jsonschema.validators import Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schemas", "outside-agent-route-verdict.schema.json")


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _base_payload(route: str) -> dict:
    return {
        "verdict_schema_version": "outside_agent_route_verdict.v0.1",
        "route": route,
        "claim_posture": "claims_only",
        "acceptance_truth_owner": "governed_pipeline",
        "blocker": {
            "class": "ambiguous_request",
            "summary": "Missing implementation detail",
            "human_required": True,
        },
        "notes": "intake-only routing decision",
    }


def _errors(payload: dict) -> list:
    return sorted(_validator().iter_errors(payload), key=lambda error: list(error.path))


def test_schema_and_allowed_routes() -> None:
    for route in ("reject", "needs_clarification", "roadmap_intake", "review_candidate"):
        assert not _errors(_base_payload(route)), route


def test_rejects_accepted_for_merge_and_unknown_routes() -> None:
    assert _errors(_base_payload("accepted_for_merge"))
    assert _errors(_base_payload("ship_it"))


def test_rejects_merge_acceptance_and_portal_authority_fields() -> None:
    merge_claim = _base_payload("review_candidate")
    merge_claim["accepted_for_merge"] = True
    assert _errors(merge_claim)

    portal_claim = _base_payload("review_candidate")
    portal_claim["portal_projection_state"] = "projected"
    assert _errors(portal_claim)


def test_blocker_values_stay_intake_scoped() -> None:
    payload = _base_payload("reject")
    assert not _errors(payload)

    invalid_blocker = copy.deepcopy(payload)
    invalid_blocker["blocker"]["class"] = "accepted_for_merge"
    assert _errors(invalid_blocker)


if __name__ == "__main__":
    test_schema_and_allowed_routes()
    test_rejects_accepted_for_merge_and_unknown_routes()
    test_rejects_merge_acceptance_and_portal_authority_fields()
    test_blocker_values_stay_intake_scoped()
    print("outside-agent route verdict schema tests passed")
