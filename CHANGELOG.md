# Changelog

All notable changes to `@consiliency/spec` / `consiliency-spec` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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
