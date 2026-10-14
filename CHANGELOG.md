# Changelog

All notable changes to `@consiliency/spec` / `consiliency-spec` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.7.1 — the verifier's acceptance-scope check, a stricter `authority_id` pattern

A patch on `0.7.0`. Projection `spec-engine-projection:v5`, certificate `schema_version` `"2"`,
canon-core `0.4.0` and the outside-agent schemas and test vectors are unchanged, and no certificate,
finding-set or payload digest moves. Public manifest rows move for the files listed below.

### Added

- **Verifier check `acceptance_scope`.** When `desired_graph=` is supplied and the certificate has an
  `acceptance` dimension, every `acceptance_results` row must name an `acceptance_criterion` of `S'`
  (its idmodel engine join id over `S'`'s hashed content). A row with no `E(C)` record carries nothing
  the `acceptance_evidence` check can compare, so without this check a re-hashed certificate with an
  added no-record row for a criterion `S` does not have verified. Without `S'` that gap remains
  (`SEMANTICS.md` §7.6); which in-scope criteria have rows is still not checked.

### Changed

- **`authority_id` pattern** (`ec.schema.json`, `result-state.schema.json`): it now ends in the
  lookahead `(?![\s\S])` instead of `$`, which anchors the true end of the string in both Python `re`
  and ECMA-262 (`u` flag), so a trailing newline is refused. Validating with these schemas needs a
  regex engine that supports lookahead; an RE2-based validator cannot compile the pattern.
- **Removed `spec-parity/conformance/check.sh`** from this repository and the packages. It needs engine
  files that are not public, so it could not run here. `spec-parity/conformance/check_xg0_envelope.sh`
  stays. The manifest now pins **98 files** (was 99).
- **Documentation** (wording only): `canon/SPEC.md` states that idmodel normalizes under the pinned
  Unicode 16.0 database (`unicodedata2`, or the standard library only when it already reports 16.0;
  otherwise non-ASCII input is refused with `IdModelError`); `SEMANTICS.md` §4.2 keeps the global
  coverage rule as the v5 rule (a finer rule could only arrive in v6 or later) and §12 names the public
  delivery projection mirror; internal planning references are removed from `canon/README.md`,
  `canon/SPEC.md`, `SEMANTICS.md`, the spec-parity and spec-graph schema descriptions,
  `kind-alignment.json` notes (`kind_alignment_version` stays `"1"`) and canon-core source comments.

### Fixed

- The reference engine refuses a lone surrogate in an acceptance evidence record's `argv` at `E(C)`
  validation, as an input error (`SEMANTICS.md` §7.6).
- `canon/conformance/test_unicode_skew.py` no longer depends on the working directory.

### Migration

- Upgrade the verifier to `0.7.1` to get `acceptance_scope`, and pass `desired_graph=` where you hold
  `S'`.
- Use a JSON Schema validator whose regex engine supports lookahead.
- Re-record any per-file digests you pin; drop `spec-parity/conformance/check.sh` from any file list.

## 0.7.0 — acceptance evidence (projection v5), Python 3.12 or later

Certificate `schema_version` stays `"2"`, canon-core stays `0.4.0`, and the outside-agent schemas and
test vectors are unchanged. New certificates pin `spec-engine-projection:v5` and carry a sixth parity
dimension, so every certificate, finding-set and payload digest differs from `0.6.x`. Stored v1-v4
certificates keep their bytes and verify under the `0.7.0` verifier as five-dimension records.

### Breaking

- **Projection v5: a sixth dimension, `acceptance`** (`SEMANTICS.md` §7.6). It follows
  `revision_alignment` in `dimension_results` and carries `acceptance_results`: one row per in-scope
  acceptance criterion, sorted by `criterion_logical_id`, each with its result state, authority and
  verdict role.
- **Verifiers `0.6.0` and earlier refuse v5 certificates**, and so does any consumer that requires
  exactly five dimensions. Upgrade verifiers and consumers before producers. A consumer that needs
  acceptance results must require `spec-engine-projection:v5`.
- **Python 3.12 or later** (was 3.10). pip will not install `0.7.0` on an older interpreter.
- **Extracted-`E(C)` markers (input)** (`SEMANTICS.md` §0, §4.2). Without `extraction_coverage`, a
  tree-sitter-chunker or baml capture stripped of its top-level markers (its nodes keep
  `provenance.tsc_node_id`), a protobuf or baml extraction that keeps a top-level marker, and any `E(C)`
  with a top-level `collapsed_boundary_ids` key are now refused. A hand-authored `E(C)` whose only
  marker is `provenance.tsc_version` is no longer refused and grades on the `declared` basis.
- **Bundled reference modules** moved to `consiliency_spec/_bundle/spec_core/` (`canon`, `graph`,
  `idmodel`); `_bundle/idmodel/py/` and `_bundle/spec-graph/py/` are gone. `src/spec_core/canon/` is a
  new public directory. Code that only calls `consiliency_spec.verify_certificate` is unaffected.

### Added

- **`E(C).acceptance_evidence`** (`ec.schema.json`): closed records with the criterion's engine join
  id, argv, exit code (within canon's bare-integer domain), log SHA-256, producing mode, actual
  pipeline engagement and a generic `authority` role: `producer` (the run that executed the command
  owns its result) or `pipeline` (an external acceptance authority owns the truth and the result is
  input to it). An optional `authority_id` names the concrete owner; it is copied and bound in
  `record_digest`, never interpreted.
- The certificate, portal-payload, result-state and waiver schemas enumerate the `acceptance`
  dimension; new finding codes `acceptance_command_pass`, `acceptance_command_fail`,
  `acceptance_unobserved` and `acceptance_pipeline_unengaged`. An unengaged `pipeline_required` record
  grades `unknown` whatever the command exited.
- **Verifier:** given the `E(C)`, every `acceptance_results` row must copy the record it names and the
  rows must be strictly increasing by `criterion_logical_id` (check `acceptance_evidence`). An unknown
  `projection_algo_version` is refused by name. The verifier does not re-derive acceptance scope: a
  consumer that needs the full scope re-runs the engine.
- **Release tooling:** `scripts/consiliency-spec/check_release.sh` runs a publish dry-run (wheel and
  sdist built and version-checked, `npm publish --dry-run`), and `publish.yml` refuses a release tag
  that does not match `package.json` and `pyproject.toml`.

### Changed

- Completeness no longer scores acceptance criteria; the acceptance dimension reports them. A graph
  whose only completeness concern was a criterion can change completeness state.
- `kind-alignment.json`: the criterion row points at the acceptance dimension (wording only;
  `kind_alignment_version` stays `"1"`).
- `docs/outside-agent-contract.md` scopes its acceptance-owner constant to pipeline-mode intake,
  names no consumer in its normative text (the frozen v0.1 `governed_pipeline` token is unchanged),
  states that the packages are published, and drops a reference to a file outside this repository
  (wording only; the schemas and vectors are unchanged).
- `spec-parity/schemas/portal-payload.schema.json`: its `description` names the producing module
  `spec_core.engine.deliver` (wording only; no validation rule changes).
- `test-vectors/certificate/pass-demo/` is regenerated as a v5 certificate.

### Migration

- Install the `0.7.0` verifier and v5-aware consumers first, then upgrade producers. Store the new
  certificate and payload digests; do not compare them with `0.6.x` values.
- Run on Python 3.12 or later.
- Re-extract any extracted `E(C)` that lacks `extraction_coverage`, or attach a coverage record.
- Import bundled reference modules from `consiliency_spec._bundle.spec_core` if you loaded them
  directly.

## 0.6.0 — realized extraction with treesitter-chunker 5.3.0, the coverage-record requirement

Certificate `schema_version` `"2"`, projection `spec-engine-projection:v4`, canon-core `0.4.0`, the
outside-agent files and the `verify_certificate` API are unchanged. The realized side changes, so every
realized `ec_digest`, and every certificate and payload over a realized `E(C)`, differs from `0.5.x`.
Certificates over hand-authored `E(C)`s keep their bytes: the `test-vectors/certificate/` vectors are
byte-identical, and a `0.5.1` verifier verifies `0.6.0` certificates unchanged.

### Changed

- **Realized extraction: treesitter-chunker `5.3.0`** (was `5.2.0`; `tree-sitter` `0.26.0` and
  `tree-sitter-language-pack` `1.20.0` unchanged). `.tsx` files are parsed with the `tsx` grammar and
  labelled `tsx`, so a JSX file without a real syntax error is fully extracted (`SEMANTICS.md` §4.2).
  Go, C++, Java, C#, Kotlin, Swift, PHP and Ruby methods carry signatures, and Go and Rust return types
  are surfaced, so an operation that graded `unknown signature_unobservable` can now grade `pass` or
  `fail`.
- **`kind-alignment.json`**: `jsx_element` and `jsx_fragment` align as `wrapper` (coalesce
  `inner_by_semantic_path`); one with no coincident mapped node stays `unmapped_realized_kind`.
  `kind_alignment_version` stays `"1"`.
- **`ec.schema.json`**: an edge may carry `to_token`, the raw call-site token as written in the source
  (a string or null). It does not depend on cross-file resolution. Documented, not shape-checked.
- Parents are same-file only: a realized node no longer hangs under a same-named declaration in
  another file.

### Breaking (input)

- **An extracted `E(C)` must carry `extraction_coverage`** (`SEMANTICS.md` §0, §4.2). An `E(C)` is
  extracted if it has any one marker: a string `provenance.tsc_version`, `source:
  "treesitter-chunker"`, or any `provenance.tsc_*` key. Without the record it is refused at entry
  instead of being graded on the hand-authored `declared` basis; re-extract it. An `E(C)` with no marker
  keeps `declared`, and an explicit `null`, malformed or `unmeasured` record keeps the `unmeasured`
  grading. A hand-built `E(C)` that sets `source: "treesitter-chunker"` only to select those
  kind-alignment rows needs a record too: a measured `full` one grades exactly like `declared`.

### Migration

- Re-extract with treesitter-chunker `5.3.0` and store the new `ec_digest` and certificate values; do
  not compare them with `0.5.x` values. Certificates over hand-authored `E(C)`s need nothing.
- Select `tsx` as well as `typescript` where you filter nodes by language, and re-read any
  correspondence entry that names a `.tsx` handler or a Kotlin method from the re-extracted `E(C)`:
  their semantic paths change.
- Declare real source signatures (including return types) for operations in the newly signed
  languages.

## 0.5.1 — reference certificate verifier

Additive only: no certificate, payload, `ec_digest`, schema, canon vector or outside-agent file changes,
and no existing API changes. A `0.5.0` certificate verifies unchanged under `0.5.1`.

### Added

- **`consiliency_spec.verify_certificate()`**, a reference certificate verifier (`SEMANTICS.md` §9),
  exported with `CertificateVerification`, `VerificationCheck` and `VerifierUnavailable`. It never
  re-grades: it recomputes the certificate digest (the non-hashed `locator` envelope excluded),
  `finding_set_id`, `ec_digest`, the portal-payload digest and the normalized `desired_graph_digest`,
  and checks the field set against `certificate.schema.json` (`schema_version` `"2"`, `canon_version`
  `v2`). It also requires `overall_result_state` to be the §6.4 aggregation of the certificate's own
  `dimension_results`, so a re-hashed `pass` over failing dimensions is refused; and, given the finding
  set, its schema to validate, every `finding_id` to recompute and each dimension's state to be the
  §6.3 rollup of its findings. Given a candidate desired graph `S'`, it runs the four binding steps of
  §9: full spec-graph validation, the `desired_graph_digest` / `spec_revision_digest` pair, and
  `spec_authority` against the graph's authority markers. A supplied portal payload must be exactly the
  §12 projection of the certificate and finding set (one summary per finding); without the finding set,
  its summaries must match the findings the certificate lists.
- **What the result means.** Each check reports `pass` or `fail` with a reason.
  - `valid`: no check failed. It proves consistency with the digest the certificate carries, not its
    origin: compare `digest` with one from a trusted channel. A `draft` or `ec_reproducible: false`
    certificate can be valid; it is then a true record of a draft or advisory run.
  - `bound`: a supplied `S'` passed validation and bound (both digests and `spec_authority`). It is
    reported on its own, whatever the other checks say, and is false when no `S'` was supplied.
  - `authoritative`: `valid` and `bound`, a supplied `E(C)` matched `ec_digest`, `spec_authority` is
    `"grounded"` and `ec_reproducible` is `true`. Only an authoritative certificate may gate.
    `require_authoritative=True` adds a failing check when it is not.
  - `advisory`: `ec_reproducible` is not `true`.
- **Strict input.** Inputs are decoded with canon's number rules (canon-core `0.4.0`): `2.0`, `2e0`,
  `-0`, `NaN`, integers beyond ±(2^53 − 1) and duplicate keys are rejected, never coerced. Untrusted
  input is a failed check, never an exception; only misuse raises (`TypeError` for an argument of the
  wrong type, `VerifierUnavailable` for an environment that cannot run a requested check). Pass JSON
  text or bytes where you can: a parsed `dict` cannot show how a number was spelled.
- **The `verify` extra**: `pip install "consiliency-spec[verify]"` adds `jsonschema>=4.18` and
  `unicodedata2==16.0.0`. `jsonschema` is always required (full schema validation). `unicodedata2` is
  required only to bind a desired graph, which applies NFC under the pinned Unicode 16.0 database.
  `VerifierUnavailable` is raised for a missing `jsonschema`, for a bundled module, schema or data file
  whose bytes do not match the package manifest, and, only when a desired graph is supplied, for a
  missing `unicodedata2` or a Unicode database other than 16.0. The verifier never returns a verdict
  with a check skipped. The npm tarball carries the verifier's Python source
  (`consiliency_spec/certificate_verifier.py`) as a public file, but there is no JavaScript verifier
  (there is no TypeScript spec-graph port).
- A dimension that records `checks_evaluated` must be `not_applicable` exactly when it evaluated no
  check (§6.3); the verifier does not apply that rule to a dimension without the field.
- The spec-graph and idmodel reference modules ship under `consiliency_spec/_bundle/`, digest-pinned in
  the manifest. The verifier loads them, with the bundled canon port, only after their sha256 matches
  the manifest, and computes the normalized desired-graph digest with them.
- `test-vectors/certificate/pass-demo/`: a certificate with the desired graph, `E(C)`, finding set and
  portal payload it binds to, plus a copy with a flipped verdict and a payload with a rewritten summary;
  `test-vectors/certificate/manifest.json` states the expected outcome of each case.
  `tests/test_certificate_vectors.py`, run by `scripts/check_certificate_vectors.sh`, checks them, and
  `scripts/consiliency-spec/check_release.sh` (the release gate) now runs it.
- The public manifest lists 89 files (`0.5.0`: 76): the verifier, the two bundled modules, the eight
  certificate vector files, their runner and their test. See "Verify a certificate" in the README.

## 0.5.0 — projection v4, full spec-graph validation at every entry, canon-core 0.4.0 ports

The certificate stays `schema_version` `"2"`, but `projection_algo_version` is
`spec-engine-projection:v4`. The algorithm version is in every certificate's hashed preimage, so every
certificate and portal-payload digest changes once. `E(C)` produced by the reference extractor now records
treesitter-chunker `5.2.0` in its provenance, so a re-extracted realized `ec_digest` changes too. On the
reference engine's own test inputs, verdicts, findings and `finding_set_id` are unchanged, except where an
input had to be corrected to pass the full validation below (operations gaining declared signatures).

### BREAKING CHANGES

**Grading — `spec-engine-projection:v4`** (`SEMANTICS.md` §0, §1.2, §7.2, §9.2):

- **Full validation at every entry.** The desired graph `S` must pass the full spec-graph rule set
  before any verdict. `0.4.0` validated an engine subset that skipped level ordering and "every
  `operation` declares a signature"; that subset is gone. An invalid `S` that `0.4.0` graded is now
  refused as an input error, and `P` / `N` run the same full validation at their shared grading
  boundary, so a driver that builds its inputs directly cannot grade it either. In particular an
  `operation` without a `signature` is refused, and a graph without its `spec_graph_version` envelope is
  refused.
- **`decomposes_to` is strictly descending.** The target must sit at a strictly lower level. Skipping a
  level going down (`contract` → `detailed`) is now valid; same-level and upward edges stay invalid and
  are refused at entry. A graph that only skipped a level keeps its digest.
- **`signature_undeclared` is retired.** An unsigned `operation` never reaches grading, so the code is
  never emitted (`result-state.schema.json` keeps it readable in older records).
- **Default-closure prohibitions are reported, never dropped.** Under the default closure, a prohibition
  in `S` that no in-scope node is `governed_by` (its governed nodes filtered out by kind, unreachable
  from a capability, or none) is a `prohibition` check `unknown` with the new code
  `prohibition_outside_default_closure` (it was silently out of scope). The projection lists them in
  `default_closure_dropped_prohibition_names`.
- **Verifier binding.** Step (1) of the four-step binding in §9 (the entry check on a candidate `S`)
  is now the full spec-graph rule set.

**Portal payload** (`portal-payload.schema.json`, §12.2): gains an optional `extraction_coverage` (the
certificate's coverage summary, copied verbatim iff present). `payload_schema_version` stays `"0"`, but a
consumer that copies the schema with `additionalProperties: false` must take the `0.5.0` copy.

**Authority delivery** (`SEMANTICS.md` §13.2, §13.3; normative text only, `authority-event.schema.json`
is byte-unchanged):

- **Re-verification at delivery (MUST).** A ledger row is no longer trusted because it is in the hash
  chain. Under the ledger lock, delivery re-verifies key status, scheme, approver, certificate binding
  and the Ed25519 signature of every admitted row for the delivered event's resolution key, each at its
  signing instant, then verifies the delivered `ratify` in full, validity windows included, at the
  delivery clock. `0.4.0` said delivery did not re-check signatures. An authority whose validity window
  has lapsed, or whose key has since been revoked or has expired in the registry, is no longer
  deliverable; the refusal carries the verifier's own reason (`bad_signature`, `unknown_key_id`,
  `key_revoked`, `key_expired`, `core_validity_expired`, `algorithm_confusion`,
  `signer_approver_mismatch`, `missing_signature`, …).
- **Delivery head anchor (MUST).** Delivery keeps a monotonic head anchor outside the ledger file,
  `.spec-authority-ledger-anchor.json`, in the decision-log directory when one is used, otherwise in the
  target directory. It refuses a ledger shorter than the anchored head (`ledger_rollback`), a ledger whose
  anchored row no longer carries the anchored digests (`ledger_forked`) and an unreadable anchor
  (`ledger_anchor_unreadable`). An anchor problem after publishing never refuses: it is reported in a
  `ledger_anchor_warning` result field (`anchor_warning` on a refusal) and a log line. This replaces the
  `0.4.0` limit that the chain does not defend against a writer; the remaining limits are listed in §13.3.

**Canon ports** (track canon-core `0.4.0`, `canon/SPEC.md`):

- a bare JSON number decodes to an integer only when it is integer-spelled and within ±(2^53 − 1), in
  every port. A larger bare integer is a `CanonError` (send it as a `$int` tag); a fraction or exponent
  spelling, or `-0`, is a float and is rejected. Previously Rust accepted bare integers up to u64,
  Python any size, and TypeScript rounded silently;
- the JSON-text entry points check every number token, including one a later duplicate key overwrites;
  Python gains `decode_input_json(text)` and TypeScript `decodeInputJson(text)` and the exact-number
  loader `parseTaggedJson(text)`, matching Rust's text entry point;
- `{"$surrogate": …}` is an ordinary key in Rust (it was rejected as a tag);
- TypeScript: an array must be plain data (no extra named properties, no index getters), a `Proxy` is
  rejected, a revoked `Proxy` is a `CanonError`, and `__proto__` is an ordinary key;
- Rust: a native `CanonValue::Object` with a repeated key is rejected.

`$int` tags, native values and the 69 earlier corpus vectors keep their bytes and digests. The corpus
grows to 81 vectors and the engine boundary vectors (`canon/conformance/engine_boundary_vectors.json`)
from 9 to 26; `canon/conformance/check_published_canon_core.sh` pins the published canon-core at
`0.4.0` and runs all 26 against the published npm and PyPI engines.

### Migration

1. **Re-pin stored digests.** Every certificate and payload digest changes once. Do not compare
   verdicts or digests across a `v3` and a `v4` certificate.
2. **Upgrade schemas.** Replace any vendored `certificate.schema.json`, `portal-payload.schema.json`
   and `result-state.schema.json` with the `0.5.0` copies, and verify each file's `sha256` against
   `consiliency-spec.public-manifest.json`.
3. **Validate your `S` with the full spec-graph rule set before upgrading:** give every `operation` a
   `signature`, relevel any same-level or upward `decomposes_to`, and always send a versioned graph
   (`{"spec_graph_version": "1", "nodes": [], "edges": []}` for an empty one).
4. **Finding codes:** handle `prohibition_outside_default_closure`; stop expecting
   `signature_undeclared` from a `v4` certificate.
5. **Canon callers:** send any integer beyond ±(2^53 − 1) as a `$int` tag, never as a bare number or a
   fraction/exponent spelling; in TypeScript, pass plain arrays and no `Proxy`.
6. **Authority deliveries:** expect a delivery to be refused when its authority has lapsed, its key has
   been revoked or has expired, or a row for its key fails signature verification. Keep
   `.spec-authority-ledger-anchor.json` and point every delivery at one stable decision-log directory so
   that they share one anchor; a deleted anchor starts again at first use and trusts the ledger as it is.
   Handle `ledger_rollback`, `ledger_forked` and `ledger_anchor_unreadable`, and watch for
   `ledger_anchor_warning`.
7. **Coming from `0.3.0`:** apply the `0.4.0` migration below first.

The normative text is `SEMANTICS.md` §0 (entry validation), §1.2 (default closure), §7.2 (signatures),
§9.2 (the projection version history) and §13.2–§13.3 (re-verification at delivery and the delivery head
anchor).

### Added

- `SEMANTICS.md` §9.3: an opt-in digest preimage sidecar. A producer can hand back the exact canonical
  bytes behind `desired_graph_digest`, `finding_set_id` and the certificate `digest`, so a verifier
  never re-encodes them. It is outside every digest and never part of a portal payload.
- `canon/conformance/engine_boundary_vectors_next.json` (manifest 75 → 76 files): boundary vectors for
  behaviour no published canon-core has yet, run only by the in-tree engines. It is empty in this
  release.

The outside-agent contract (schemas, vectors, contract document) is byte-unchanged; the reference
router's behaviour is unchanged.

`consiliency-spec.public-manifest.json` records `source.authority_contract_version` `"0.6.5"` (was
`"0.6.0"`). The vendored authority schema, `spec-engine/authority/authority-event.schema.json`, is
byte-unchanged.

`spec-parity/kind-alignment.json` changes one `note` only (no row change; `version` stays `"1"`): the
generic `treesitter-chunker` `class` → `component` row now documents how BAML types are graded
(`SEMANTICS.md` §5).

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
