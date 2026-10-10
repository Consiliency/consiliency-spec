# consiliency-spec

**A deterministic, machine-checkable way to certify "does the code match the intent?" — and keep that answer trustworthy as both the code and the intent change over time.**

Every software project has two things: a **blueprint** (what it's *supposed* to be — the intent, the rules, the must/must-never) and the **actual building** (the real code). Normally nobody can *prove* the building matches the blueprint; people eyeball it, or trust an AI's opinion, which can be confidently wrong.

`consiliency-spec` is the open **engine** for that proof: feed it the blueprint and the real code and it issues a **certificate** — these match, or here is exactly where they diverge — with **mathematical certainty, not an AI's guess**. AIs may *propose* changes; only the deterministic engine *certifies*. And it stays honest as things change: rename or move code and it sees *"same thing, relocated"*, not *"deleted + re-added"*.

> This repository is the neutral engine and its contracts. It is source-disclosure of the canon engine surface, digest-pinned in [`consiliency-spec.public-manifest.json`](consiliency-spec.public-manifest.json).

---

## Core principles

- **Deterministic, never LLM-graded.** The projection `P` and the checker `N` are pure code. The certificate is reproducible byte-for-byte; an LLM is never in the grading path.
- **Two authoritative sources.** The **desired state** (a semantic graph `S` = intent) and the **realized state** (`E(C)` = facts extracted from the source). Everything else — renderings, reports, payloads — is a **disposable projection**, regenerable and never authoritative.
- **Honest about uncertainty.** A check the extractor *cannot observe* returns `unknown`; a check the engine *cannot ask* returns `unsupported`. Neither is ever silently turned into `pass`. An empty run is `not_applicable`, never green.
- **Identity survives change.** Refactor-tolerance comes from a **correspondence map**: logical identity is tracked across rename/move/split/merge, with a lifecycle enum.
- **Content-addressed everything.** One canonical serialization, one hash domain scheme, byte-identical across languages (Python ↔ TypeScript).

---

## What's in this repository

| Component | What it is | Key guarantee |
|---|---|---|
| [`canon/`](canon/) | Canonical serialization + content-addressing (SHA-256; **canon v2** — NFC at the ingestion boundary, not in the hash). Includes the Rust core (`canon/core/`) and the dependency-free TypeScript and Python ports. | Python and TypeScript produce **byte-identical** bytes + digests. |
| [`idmodel/`](idmodel/correspondence/schema.json) | Two-tier identity (logical key + occurrence) + the correspondence map schema. | Logical identity tracked across rename/move/split/merge. |
| [`spec-graph/`](spec-graph/schema/) | The desired-state semantic metamodel schema (the blueprint format). | Open versioned `kind` system; per-node content-addressed. |
| [`spec-parity/`](spec-parity/) | The formal parity contract: `SEMANTICS.md` + schemas (kind-alignment, result-state, waiver, run-descriptor, `E(C)`, reproducibility attestation, **certificate** (schema `"2"`), **portal-payload**, permitted-freedom). | A reviewer can implement `P`/`N` from it directly; result states + closed-world prohibitions + waiver lifecycle are pinned; projection algorithm `spec-engine-projection:v4`. |
| [`spec-engine/authority/`](spec-engine/authority/authority-event.schema.json) | The authority-event schema for the deterministic projection/checker boundary. | Certificate byte-reproducible. |

The five parity dimensions: **completeness**, **soundness**, **closure**, **prohibition**, **revision-alignment**.

### Reference certificate verifier (0.5.1) — additive

`0.5.1` adds `consiliency_spec.verify_certificate` (see "Verify a certificate" below), its `verify`
extra and a public certificate test vector. Nothing else changes: every certificate, payload and
`ec_digest` from `0.5.0` keeps its bytes and verifies unchanged.

### Projection v4 (0.5.0) — breaking

Since `0.5.0` the certificate is still `schema_version` `"2"`, but the projection algorithm is
`spec-engine-projection:v4` (`SEMANTICS.md` §9.2), so every certificate and payload digest differs from
`0.4.0`. The reference engine validates the desired graph `S` with the full spec-graph rule set at every
entry (§0): every `operation` declares a signature, and a `decomposes_to` edge descends to a strictly
lower level. Under the default closure, a prohibition no in-scope node is governed by is reported
`unknown` (`prohibition_outside_default_closure`) instead of dropping out. The canon ports track
canon-core `0.4.0`: a bare JSON integer must lie within ±(2^53 − 1), anything larger is a `$int` tag.
See [`CHANGELOG.md`](CHANGELOG.md) for the full BREAKING CHANGES list and the migration steps.

### Certificate contract 2 (0.4.0) — breaking

Since `0.4.0` every certificate is `schema_version` `"2"`, so every certificate digest differs from
`0.3.0`. A certificate pins `spec_authority` (`grounded` or `draft`) and optionally `authority_ref`;
`desired_graph_digest` is the normalized spec-graph digest; `ec_reproducible` is required and measured.
A verifier binds a candidate graph `S` with the four steps in `SEMANTICS.md` §9 (entry check, graph
digest, revision digest, authority), and a delivery refuses a `draft` certificate unless the consumer
opts in explicitly (§12.6). Coming from `0.3.0`, apply the `0.4.0` migration in
[`CHANGELOG.md`](CHANGELOG.md) before the `0.5.0` one.

---

## Packages

This repository is the source for two packages:

- **npm:** `@consiliency/spec`
- **PyPI:** `consiliency-spec`

The packages are an **extraction of the existing canon bytes, not a reimplementation**: canon-core v2's Rust core, the dependency-free TypeScript and Python ports, vectors, conformance checks, and public schemas are digest-pinned in [`consiliency-spec.public-manifest.json`](consiliency-spec.public-manifest.json). CI hard-fails if the package artifacts drift from those source bytes.

The enforcing JavaScript surface is the pure TypeScript v2 port in [`canon/ts/canon.ts`](canon/ts/canon.ts). The WASM binding is a cross-language parity artifact only; see [`canon/conformance/wasm_surrogate_finding.mjs`](canon/conformance/wasm_surrogate_finding.mjs) for the documented lone-surrogate boundary finding.

> Packages are published to npm and PyPI by the maintainer via Trusted Publishing when a GitHub Release is created in this repository. A version is available only once its release has been published; check the registry for the versions that exist.

### Use it

Python (`consiliency-spec` has no runtime dependencies; it needs Python 3.10 or later):

```bash
pip install consiliency-spec
```

The `consiliency_spec` module is a thin reader over the digest-pinned public files:

```python
import hashlib
import consiliency_spec

consiliency_spec.__version__                  # the package version, e.g. "0.5.1"

# The manifest: {"schema_version", "package", "source", "public_files": [{"path", "sha256"}, ...]}
manifest = consiliency_spec.load_manifest()

# Every public file path the manifest pins (repo-relative), e.g. "spec-parity/SEMANTICS.md"
paths = consiliency_spec.list_public_files()

# The exact bytes of one public file. A path the manifest does not list raises ValueError.
data = consiliency_spec.read_public_bytes("spec-parity/schemas/certificate.schema.json")
pinned = {f["path"]: f["sha256"] for f in manifest["public_files"]}
assert hashlib.sha256(data).hexdigest() == pinned["spec-parity/schemas/certificate.schema.json"]

# A JSON schema by name, with or without the ".schema.json" suffix. An unknown name raises ValueError.
certificate_schema = consiliency_spec.load_schema("certificate")   # spec-parity/schemas/certificate.schema.json
spec_graph_schema = consiliency_spec.load_schema("spec-graph")     # spec-graph/schema/spec-graph.schema.json
```

`read_public_text(path)` and `load_json(path)` are the text and parsed-JSON forms of
`read_public_bytes(path)`. To validate a certificate, feed `load_schema("certificate")` to any JSON
Schema 2020-12 validator (for example `jsonschema`); to bind it to a desired-state graph, follow the
verifier procedure in `SEMANTICS.md` §9, or call `verify_certificate` (below), which implements it.

npm: `npm install @consiliency/spec` ships the same public files. Import them through the package's
`exports` map, for example `@consiliency/spec/manifest.json` (the manifest),
`@consiliency/spec/schemas/certificate.schema.json` (any `spec-parity/schemas/*` file) and
`@consiliency/spec/canon/ts/canon.ts` (the TypeScript canon port).

### Verify a certificate

`consiliency_spec.verify_certificate` (new in 0.5.1) is the reference verifier for a parity certificate
(`SEMANTICS.md` §9). It re-derives everything the certificate pins that you can recompute, using only
what this package ships: the bundled canon v2 port, the spec-graph and idmodel reference modules
(`consiliency_spec/_bundle/`) and the public schemas. It never re-grades.

```bash
pip install "consiliency-spec[verify]"   # jsonschema (always required) and unicodedata2==16.0.0 (to bind S)
```

```python
from pathlib import Path
from consiliency_spec import verify_certificate

result = verify_certificate(
    Path("spec-certificate.json").read_bytes(),               # JSON text or bytes: parsed strictly
    desired_graph=Path("spec.graph.json").read_bytes(),      # S': the binding procedure of §9
    ec=Path("ec.json").read_bytes(),                         # E(C): ec_digest
    payload=Path("spec-portal-payload.json").read_bytes(),   # optional: the portal payload
    # finding_set=...                                        # optional: the finding set at findings_ref
    # require_authoritative=True                             # also fail unless the certificate may gate
)
result.valid            # no check failed
result.bound            # S' is valid and binds: desired_graph_digest, spec_revision_digest, spec_authority
                        #   (reported on its own, whatever the other checks say)
result.authoritative    # valid, bound, E(C) supplied and matching ec_digest, spec_authority ==
                        #   "grounded" and ec_reproducible is True
result.advisory         # ec_reproducible is not true: the certificate must not gate
for check in result.checks:
    print(check.name, check.status, check.reason)   # status: "pass" | "fail"
```

What it checks:

- **The record.** The certificate decodes under canon's strict number rules, its field set matches
  `certificate.schema.json` (`schema_version` `"2"`, `spec_authority` and `ec_reproducible` present and
  well-typed), `canon_version` is `v2`, and the canon `certificate`-profile digest recomputes (the
  non-hashed `locator` envelope is excluded). Its `overall_result_state` must be the §6.4 aggregation
  of its own `dimension_results`, and a dimension is `not_applicable` exactly when it evaluated no
  check (§6.3), so a re-hashed `pass` over failing dimensions is refused.
- **The binding pair.** Given `desired_graph=S'`, it runs the four steps of §9: `S'` passes the full
  spec-graph rule set; `spec_graph.graph_digest(S')` equals `desired_graph_digest`;
  `spec_revision_digest` recomputes from `S'`'s `spec_revision_id` (`""` when absent) and that digest;
  and `is_authoritative(S')` agrees with `spec_authority`. A draft graph never binds to its ratified
  twin's certificate, and changing only `spec_revision_id` is caught.
- **Everything else you supply.** `ec_digest` from `E(C)`; from the finding set, its JSON Schema
  (`result-state.schema.json`), `finding_set_id`,
  `findings_ref`, the copied `overall_result_state` / `dimension_results`, every `finding_id`, and each
  dimension's state against the rollup of its findings; the payload's digest and every field it copies
  from the certificate, after validating it against `portal-payload.schema.json`. With the finding
  set, the payload must be exactly the §12 projection of (certificate, finding set); without it, its
  `finding_summaries` must be one per finding the certificate lists, titled by code, and roll up to
  each dimension's state.
  `require_authoritative=True` needs both `desired_graph` and `ec`.

What `valid` does not prove is origin. Anyone can edit a certificate and re-hash it, which yields a
different certificate that is consistent with itself. The digest is the certificate's identity (§9):
compare `result.digest` with a digest you obtained from a channel you trust, or resolve `authority_ref`
against the authority ledger (§13), which this verifier does not do. A re-hash cannot defeat the
bindings to inputs you hold yourself: `S'`, `E(C)`, the finding set and the payload.

Numbers are never trusted. A fraction or exponent spelling (`2.0`, `2e0`), a bare `-0`, `NaN`/`Infinity`
and an integer beyond ±(2^53 − 1) are rejected exactly as canon-core 0.4.0 rejects them, and so is a
duplicated key. Pass inputs as text or bytes: a parsed `dict` can no longer show that `2` was spelled
`2.0`. Bad input never raises; it is a failed check with a reason. Only misuse raises: `TypeError` for an
argument of the wrong type, and `VerifierUnavailable` when the environment cannot run a check
(`jsonschema` missing; `unicodedata2` missing or not Unicode 16.0 when `desired_graph` is given; or a
bundled module, schema or vocabulary file whose bytes do not match the package manifest). The verdict never depends on what is installed: every
check you asked for runs, or the call raises. The verifier is Python only; the npm package does not
ship one.

A worked example ships in [`test-vectors/certificate/`](test-vectors/certificate/): a certificate with
the desired graph, `E(C)`, finding set and portal payload it binds to, plus tampered copies (a flipped verdict, a forged finding
summary) that must fail. `scripts/check_certificate_vectors.sh` (part of the release gate) runs the verifier over it.

### canon-core relationship

Runtime consumers that need a compiled core use the separately published `@consiliency/canon-core` (npm) and `consiliency-canon-core` (PyPI) packages. This repository discloses the canon-core Rust **source** (`canon/core/**`) for independent verification; it does not change or republish those runtime packages.

---

## Conformance

Every push and PR to `main` runs one gate in CI, `scripts/consiliency-spec/check_release.sh`, and the publish workflow runs the same gate before any upload. Run it locally with:

```bash
bash scripts/consiliency-spec/check_release.sh
```

It runs four self-contained gates:

- `canon/conformance/check.sh`: Python and TypeScript canon v2 conformance against the pinned vectors, ingest-boundary NFC under the pinned Unicode 16.0 DB, the vector corpus regenerated by `gen_vectors.py --check`, and Python <-> TypeScript byte-identity.
- `canon/conformance/check_xg4_canon_core.sh` (XG4): the Rust core tests, then the BUILT PyO3 and WASM bindings checked byte-identical to the reference ports, including the engine boundary vectors and the vendored cross-repo consumer corpus.
- `scripts/check_outside_agent_vectors.sh`: the outside-agent schemas, conformance vectors and reference router.
- `scripts/check_certificate_vectors.sh` (since `0.5.1`): the reference certificate verifier over `test-vectors/certificate/`. It needs the `verify` extra's dependencies; `check.sh` installs `unicodedata2==16.0.0` first.

The authority-event contract vectors are not run here. They are checked in the private source repository, which vendors the authority contract; this repository ships only `spec-engine/authority/authority-event.schema.json`.

---

## License

Licensed under the **Apache License, Version 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Apache-2.0 is a permissive license with an explicit patent grant. It grants **no trademark rights**: "Consiliency" is retained as a name/brand even though the source is public.

## Contributing

**This project is not accepting external contributions yet.** Issues may be opened for discussion, but pull requests from outside the maintainer are not being merged at this time. A contribution policy (DCO or CLA) will be published if and when contributions are opened.

## Security

Please report security issues privately to the maintainer rather than opening a public issue.
