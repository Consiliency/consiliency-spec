# Changelog

All notable changes to `@consiliency/spec` / `consiliency-spec` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.4.0 — parity certificate contract 2, projection v3, canon-core 0.3.0 ports

Every parity certificate digest changes in this release. No verdict, result state or finding
changes on any input the reference engine was tested against; the digests, the new fields and the
versions are what move.

### BREAKING CHANGES

**Certificate** (`spec-parity/schemas/certificate.schema.json`, `SEMANTICS.md` §9):

- `schema_version` is `"2"` (a schema `const`). A validator pinned to the `0.3.0` certificate schema
  rejects every new certificate. A schema-`"1"` certificate stays a valid record of its own version;
  never re-hash it as schema 2.
- New hashed, required `spec_authority` (`grounded` | `draft`): `grounded` iff the anti-vacuity
  predicate `is_authoritative(S)` holds (at least one contract/architecture node that is
  human-authored and not marked unratified), else `draft`.
- New hashed, optional `authority_ref`: the 64-hex authority-ledger `entry_digest` of the ratify event
  the producer cites. Refused for a draft `S`; recorded verbatim, not verified by the engine.
- `desired_graph_digest` is the **normalized** spec-graph digest (`graph_digest(S)`: canon
  `semantic-content` over the hashed content only), no longer the canon digest of the raw graph. Prose,
  notes, source positions, provenance markers, `graph_id`, `doc` and node order no longer move it.
  `spec_revision_digest` = canon `semantic-content` of `{spec_revision_id, desired_graph_digest}`
  (`spec_revision_id` is `""` when absent) moves with it.
- `ec_reproducible` is required and means **measured**: `true` only when the producer extracted
  `E(C)` twice and compared the canonical bytes, or cites a reproducibility attestation whose digest
  equals the certified `E(C)` (`ec-reproducibility-attestation.schema.json`). Otherwise `false`, and
  the certificate is advisory.
- Optional hashed `extraction_coverage` summary (also on the finding set and the vacuity record) when
  the `E(C)` extraction was partial or unmeasured.

**Portal payload** (`portal-payload.schema.json`, §12): gains a required `spec_authority` and an
optional `authority_ref`. `payload_schema_version` stays `"0"`. A conforming delivery refuses a
`draft` certificate, or a certificate with no `spec_authority`, unless the consumer explicitly opts in
with a literal boolean `true` (§12.6).

**Grading** — `projection_algo_version` is `spec-engine-projection:v3` (§9.2). `0.3.0` documented
`v1`; `v2` and `v3` both land in this release:

- correspondence lifecycle: an ambiguous entry, two entries for one desired node, or a dangling entry
  is `unknown`; `deleted` fails; `superseded` counts as present and soundness grades its signature
  (`correspondence_superseded` is retired);
- measured coverage: over a partial or unmeasured extraction no prohibition passes, a completeness
  absence is `unknown` (`desired_element_unobservable`) and closure adds `closure_extraction_partial`;
- absent signatures follow the type-member rule: no declared signature passes, a declared signature
  over a realized fact without one is `unknown signature_unobservable`, an operation with no declared
  signature matched to a realized one is `unknown signature_undeclared` (all were
  `fail structural_mismatch`);
- a realized kind that aligns to a different desired kind fails `kind_mismatch` (except desired
  `type`). On new inputs this can turn a former pass into a fail;
- an unknown frontier-selector name refuses the run; selectors hold node names;
- `S` and `E(C)` are validated at entry (new `ec.schema.json`; JSON floats rejected anywhere;
  `frontier`/`unratified` must be booleans and `provenance` an object), and an invalid input refuses
  the run;
- `P`/`N` grade the graded view (the preimage of `desired_graph_digest`) instead of the raw `S`:
  strings compare after Unicode NFC, a desired signature is graded only on operation/interface nodes
  and `members` only on `type`.

**Waivers and run descriptor**: new `run-descriptor.schema.json` with a deterministic `as_of` date. A
waiver whose expiry is before `as_of` is expired; supplying waivers without `as_of` refuses the run; an
empty `scope` is rejected; the `prohibition_id` and `realized_source` selectors are now applied.

**Spec graph** (`spec-graph.schema.json`): new provenance origin `llm_proposed`; graph-level `doc` and
`spec_revision_id` (envelope, unhashed); optional derived `logical_id` / `content_digest` on nodes and
edges, which must recompute when present.

**Authority** (§13): §13 now documents only the live `authority_event_protocol.v1` (the exact audience
plus `cert_digest` key, latest ratify wins, supersede retires, revoke is terminal, `decision_id`
admitted at most once, the ledger hash chain verified on load). `authority-event.schema.json` still
ships, labelled superseded and non-normative.

**Canon ports** (track canon-core `0.3.0`, `canon/SPEC.md`):

- every tag payload has exactly one JSON type (`$bool` a boolean, `$str` a string, `$null` exactly
  `true`, `$obj` an object, `$arr` an array); anything else is a `CanonError`;
- containers nested deeper than `MAX_DEPTH = 128` are a `CanonError` in every port, at decode and
  encode;
- the TypeScript port encodes plain data objects only: `Date`, `Map`, `Set`, class instances, boxed
  primitives, accessors, symbol-keyed or non-enumerable properties and sparse-array holes are
  rejected;
- the C-ABI hands out exact-length boxed buffers (header and ABI unchanged).

Inputs that all three `0.2.0` ports accepted keep their bytes and digests; the 47 earlier vectors
are byte-identical, and the corpus grows to 69.

### Migration

1. **Upgrade schemas.** Replace any vendored `certificate.schema.json` and
   `portal-payload.schema.json` with the `0.4.0` copies, and verify each file's `sha256` against
   `consiliency-spec.public-manifest.json`.
2. **Bind `S` with all four steps** (§9) if you verify a certificate against a candidate graph:
   (1) structurally validate `S`; (2) require `graph_digest(S) == desired_graph_digest`;
   (3) require the recomputed `spec_revision_digest` to match; (4) require
   `is_authoritative(S) == (spec_authority == "grounded")`. Comparing a digest of the raw graph no
   longer works, and the graph digest alone does not bind `spec_revision_id` or the draft markers.
3. **Gate on authority.** Treat `spec_authority: "draft"` as non-authoritative and refuse to
   deliver or ratify it unless the caller opted in explicitly. Treat `ec_reproducible: false` as
   advisory.
4. **Do not compare across versions.** A schema-1 / `v1` certificate and a schema-2 / `v3` one over
   the same inputs have different digests by design.
5. **Canon callers**: stop relying on truthy tag payloads, nesting deeper than 128, or non-plain
   TypeScript objects; convert to plain data first.
6. **Waivers**: supply `as_of` with every waiver set and give every waiver a non-empty `scope`.

New public schemas since `0.3.0`: `ec.schema.json`, `run-descriptor.schema.json`,
`ec-reproducibility-attestation.schema.json`. The outside-agent contract (schemas, vectors, reference
router) is unchanged.

## 0.3.0 — one `$int` grammar for every canon port

Tracks canon-core `0.2.0` (`@consiliency/canon-core@0.2.0`,
`consiliency-canon-core==0.2.0`). The bundled Python and TypeScript canon ports
and the conformance corpus carry the same change.

- **`$int` payload grammar is `^-?[0-9]+$` in every port**, checked before the
  native integer parser. Previously each port's parser decided (`int("1_000")`
  in Python, `BigInt(" 12 ")` in TypeScript, `"+5"` in Rust), so a tagged
  integer could digest in one port and be rejected in another. Leading zeros
  and `-0` are accepted and normalise; empty, whitespace, `+`, `_`, hex,
  non-ASCII digits and interior NUL are rejected with one fixed message that
  never echoes the payload.
- **TypeScript `CanonValue` takes `bigint` only.** A plain JS `number` is
  rejected instead of `100.0 === 100` silently canonicalising a float. Callers
  convert with `BigInt(...)`. This type-surface change is why the bump is a
  minor, not a patch.
- **C-ABI error pointer is never null**: an interior NUL in the fixed message is
  escaped rather than turning `*err_out` into a null pointer.
- **Corpus**: 11 new vectors (36 → 47). The 36 pre-existing vectors are
  byte-identical — the digest domain is unchanged, so no existing digest moves.
  The committed corpus is checked against its generator in the canon gate, and
  the generator (`canon/vectors/gen_vectors.py`) now ships in this package so
  that check runs here too.
- Test harnesses decode inside the `expect_error` guard, so an over-permissive
  decoder can no longer be recorded as a rejection.

Nothing in the outside-agent contract changes.

## 0.2.4 — outside-agent contract

Adds the **outside-agent contract**: a claims-only intake surface for work
proposed by agents outside a governed project. Outside agents may submit
`work_request`, `implementation_submission`, or `ambiguity_report` records; they
never own acceptance truth.

- `schemas/outside-agent-submission.schema.json` — the submission contract
  (JSON Schema 2020-12). Fail-closed: `evidence_refs` must be non-empty, git
  object ids must be exactly 40 or 64 hex characters, unknown properties are
  rejected.
- `schemas/outside-agent-route-verdict.schema.json` — the intake-only verdict.
  The route vocabulary is exactly `reject`, `needs_clarification`,
  `roadmap_intake`, `review_candidate`; `accepted_for_merge` is deliberately
  absent because merge acceptance belongs to the acceptance authority.
- `consiliency_spec/outside_agent_router.py` — the reference router, the
  executable oracle that derives the verdict for every conformance vector.
  Verdicts never echo submitted content: a rejection names the JSON pointer and
  the failed constraint only, so a structural rejection cannot become a content
  channel for whatever was in the rejected field.
- `test-vectors/outside-agent/` — 11 conformance vectors (3 valid, 8 invalid)
  with a manifest; the shipped tests replay every vector through the router and
  assert the derived verdict matches.
- `docs/outside-agent-contract.md` — the contract, its fail-closed rules, and
  one recorded known gap (secret-shaped content in permitted text fields is
  deliberately not regex-enforced by the schema).
- Every module under `consiliency_spec/` is now digest-pinned in the public
  manifest, so a change to the router is a visible change to the manifest.
- Public release gate now runs the outside-agent vector gate alongside the canon
  gates; CI pins Node `24.13.0` and asserts its Unicode database is `16.0`.

Versions `0.2.0`–`0.2.3` were cut and superseded before reaching either registry;
`0.2.4` is the first `0.2.x` published. Python: the router's `jsonschema`
dependency is the optional extra `consiliency-spec[outside-agent]`.

## 0.1.1 — first CI release (Trusted Publishing)

No source changes from `0.1.0`. First release published through GitHub Actions
Trusted Publishing (OIDC, tokenless) to both npm (`@consiliency/spec`, with
provenance) and PyPI (`consiliency-spec`). The `0.1.0` npm publish was a manual
bootstrap to create the package so the trusted publisher could attach; this is
the first fully-governed release across both registries.

## 0.1.0 — initial public release

First public open-core release of the deterministic spec-vs-code parity engine
(Apache-2.0). This is an extraction of the canon engine surface, digest-pinned in
[`consiliency-spec.public-manifest.json`](consiliency-spec.public-manifest.json):

- **canon** — canonical serialization + content-addressing (SHA-256; canon v2, NFC
  at the ingestion boundary). Rust core, plus dependency-free TypeScript and Python
  ports that are byte-identical to each other and to the Rust core.
- **idmodel** — two-tier identity + correspondence-map schema.
- **spec-graph** — desired-state semantic metamodel schema.
- **spec-parity** — the formal parity contract (`SEMANTICS.md` + schemas:
  kind-alignment, result-state, waiver, certificate, portal-payload,
  permitted-freedom).
- **spec-engine/authority** — the authority-event schema.
- **conformance vectors + gates** — Python/TypeScript/Rust byte-identity and the
  XG4 core/binding parity gate.

The five parity dimensions: completeness, soundness, closure, prohibition,
revision-alignment. AIs may propose changes; only the deterministic engine
certifies — an LLM is never in the grading path.
