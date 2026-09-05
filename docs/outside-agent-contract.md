# Outside-Agent Contract

The outside-agent contract is a claims only intake surface. Outside agents may submit `work_request`, `implementation_submission`, or `ambiguity_report` records, but they never own acceptance truth. The acceptance authority (`governed_pipeline` in the schema) keeps acceptance truth, agent-harness may run the same contract as advisory conformance, and Portal consumes only downstream digest-bound projections.

## Submission Schema

`outside_agent_submission.v0.1` is the canonical JSON Schema 2020-12 envelope for outside-agent submissions. The discriminator is limited to `work_request`, `implementation_submission`, and `ambiguity_report`. Every submission carries `claim_posture=claims_only` and `acceptance_truth_owner=governed_pipeline` so the schema itself states the authority boundary.

## Evidence References

`outside_agent_evidence_ref.v0.1` is embedded inside the submission schema. Evidence refs are metadata-only and must include repo owner and repo name, an immutable commit or tree SHA, a repo-relative path, `digest_algorithm=sha256`, the SHA-256 digest, a source role, source bundle references, the bundle manifest digest, and a claimed-path membership proof. Absolute paths, traversal paths, raw bodies, provider payloads, secret fields, and local env value fields are rejected by the closed object shape and repo-relative path constraints.

## Route Verdicts

`outside_agent_route_verdict.v0.1` is an intake-only verdict schema. The route vocabulary is exactly `reject`, `needs_clarification`, `roadmap_intake`, and `review_candidate`. `accepted_for_merge` is intentionally absent because merge acceptance belongs to the acceptance authority's review, not to outside-agent intake.

The verdict schema allows a narrow, non-secret blocker object so intake can explain why a submission needs clarification or rejection. It does not allow merge-acceptance fields or Portal projection state claims.

## Downstream Ownership

- The acceptance authority (`governed_pipeline`) owns acceptance truth and any `accepted_for_merge` decision.
- Agent-harness may consume these schemas for advisory or mirrored conformance runs.
- Portal must stay downstream of digest-bound projections and must not consume raw outside-agent evidence payloads.

## Package Consumption

OAPACK makes the contract package-available for downstream pinning through `@consiliency/spec` and
`consiliency-spec`. Consumers should read `docs/outside-agent-contract.md`, the two
`schemas/outside-agent-*.schema.json` files, `test-vectors/outside-agent/manifest.json`, the vector
corpus, and `scripts/check_outside_agent_vectors.sh` from the package surface.

- Both consumers pin the **annotated release tag** recorded in `plans/oapack/RELEASE-ANCHOR.md`,
  never a branch commit. That file is the single source of truth for the version and the anchor.
- **Per-artifact digests are part of the published surface.**
  `consiliency-spec.public-manifest.json` carries a `sha256` for every public file, so a consumer
  can verify each schema and vector independently. Pinning only the manifest hash leaves a byte
  change that preserves that hash undetectable; verify per file.
- This phase proves contract availability only. It does not mean the package is published, does not
  claim downstream enforcement is live, and does not add Portal projection behavior.

## Reference Router

`consiliency_spec.outside_agent_router` is the normative executable oracle for
`outside_agent_route_verdict.v0.1`. `route(payload, schema_target)` returns the verdict a
conforming intake implementation MUST produce, and the conformance corpus is checked by deriving
every verdict and comparing it to `test-vectors/outside-agent/manifest.json`.

It exists because the corpus previously declared an `expected_verdict` per vector that nothing
computed: the manifest was only checked for allowed *vocabulary*, so a wrong verdict could not
fail the suite. A conformance corpus that cannot fail is not a fence. Install the `outside-agent`
extra (`jsonschema`) to use it.

Routing is total and fails closed:

| condition | route | blocker class |
| --- | --- | --- |
| valid `work_request` | `roadmap_intake` | none |
| valid `implementation_submission` | `review_candidate` | none |
| valid `ambiguity_report` | `needs_clarification` | none |
| absent or empty evidence | `reject` | `missing_information` |
| malformed or unsafe evidence content | `reject` | `unsafe_evidence_reference` |
| anything the contract has no rule for | `reject` | `policy_gap` |

## Fail-closed Rules

- `evidence_refs` requires `minItems: 1`. A submission with no evidence at all is not routable.
- Git object ids must be **exactly** 40 hex (SHA-1) or **exactly** 64 hex (SHA-256). The earlier
  `{40,64}` range accepted 41–63 character ids, which no git hash function can produce.
- Both rules are exercised by dedicated negative vectors
  (`negative-empty-evidence-refs`, `negative-git-object-id-length`) rather than asserted only in
  prose.

### Verdicts never echo submitted content

A route verdict reports **where** validation failed and **which** constraint failed — never the
submitted value. Validator libraries embed the offending value (and, for unexpected properties, the
property *name*) in their error messages; passing those through would launder submitted content into
a document that is serialized and passed downstream, defeating the metadata-only posture.

Structural validation and safety redaction are **separate mechanisms**. Conforming to a structural
contract never licenses relaxing a safety guarantee an implementation independently publishes, and
the reverse: a structural rejection must not become a content channel. `v0.2.0` violated this and is
superseded; see the changelog.

### Known gap — secret-shaped content

The schema constrains *shape*, not *content*: a permitted free-text field can still carry
secret-shaped text that the acceptance authority rejects at ingest. This is deliberately **not** fixed by
regex here. Pattern-matching for secrets inside a schema is unreliable in both directions, and
the acceptance authority already performs this check. Recorded as a
known divergence rather than papered over.
