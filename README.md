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
| [`spec-parity/`](spec-parity/) | The formal parity contract: `SEMANTICS.md` + schemas (kind-alignment, result-state, waiver, certificate, portal-payload, permitted-freedom). | A reviewer can implement `P`/`N` from it directly; result states + closed-world prohibitions + waiver lifecycle are pinned. |
| [`spec-engine/authority/`](spec-engine/authority/authority-event.schema.json) | The authority-event schema for the deterministic projection/checker boundary. | Certificate byte-reproducible. |

The five parity dimensions: **completeness**, **soundness**, **closure**, **prohibition**, **revision-alignment**.

---

## Packages

This repository is the source for two packages:

- **npm:** `@consiliency/spec`
- **PyPI:** `consiliency-spec`

The packages are an **extraction of the existing canon bytes, not a reimplementation**: canon-core v2's Rust core, the dependency-free TypeScript and Python ports, vectors, conformance checks, and public schemas are digest-pinned in [`consiliency-spec.public-manifest.json`](consiliency-spec.public-manifest.json). CI hard-fails if the package artifacts drift from those source bytes.

The enforcing JavaScript surface is the pure TypeScript v2 port in [`canon/ts/canon.ts`](canon/ts/canon.ts). The WASM binding is a cross-language parity artifact only; see [`canon/conformance/wasm_surrogate_finding.mjs`](canon/conformance/wasm_surrogate_finding.mjs) for the documented lone-surrogate boundary finding.

> Package publication to npm and PyPI is performed by the maintainer via Trusted Publishing after the first release is cut. If a registry lookup shows no published version yet, the package has not been released.

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
