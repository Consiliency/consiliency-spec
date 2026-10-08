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

consiliency_spec.__version__                  # the package version, e.g. "0.5.0"

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
verifier procedure in `SEMANTICS.md` §9.

npm: `npm install @consiliency/spec` ships the same public files. Import them through the package's
`exports` map, for example `@consiliency/spec/manifest.json` (the manifest),
`@consiliency/spec/schemas/certificate.schema.json` (any `spec-parity/schemas/*` file) and
`@consiliency/spec/canon/ts/canon.ts` (the TypeScript canon port).

### canon-core relationship

Runtime consumers that need a compiled core use the separately published `@consiliency/canon-core` (npm) and `consiliency-canon-core` (PyPI) packages. This repository discloses the canon-core Rust **source** (`canon/core/**`) for independent verification; it does not change or republish those runtime packages.

---

## Conformance

Every push and PR runs the canon byte-identity, XG4 canon-core parity, and authority-vector gates in CI. Run the local release gate with:

```bash
bash scripts/consiliency-spec/check_release.sh
```

This runs the self-contained conformance gates: Python <-> TypeScript canon v2 byte-identity, and the Rust core + BUILT PyO3/WASM bindings byte-identity (XG4), including the vendored cross-repo consumer corpus.

---

## License

Licensed under the **Apache License, Version 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Apache-2.0 is a permissive license with an explicit patent grant. It grants **no trademark rights**: "Consiliency" is retained as a name/brand even though the source is public.

## Contributing

**This project is not accepting external contributions yet.** Issues may be opened for discussion, but pull requests from outside the maintainer are not being merged at this time. A contribution policy (DCO or CLA) will be published if and when contributions are opened.

## Security

Please report security issues privately to the maintainer rather than opening a public issue.
