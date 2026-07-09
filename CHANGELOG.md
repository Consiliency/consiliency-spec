# Changelog

All notable changes to `@consiliency/spec` / `consiliency-spec` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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
