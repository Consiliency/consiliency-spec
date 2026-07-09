# Cross-repo canon vectors — vendored provenance

`gov-cross-repo-canon-vectors.json` is a pinned, byte-for-byte copy of a downstream
consumer's certificate/payload canon vector corpus. The XG-4 parity gate
(`canon/conformance/check_xg4_canon_core.sh`, step [7]) holds every canon v2 engine
(Python + TypeScript references, the Rust core, and the BUILT PyO3 + WASM bindings)
byte+digest-identical over these vectors' INPUT domain — the cross-repo
serialization-parity invariant — with **this committed copy** as the REQUIRED target
so the gate is always runnable (CI has no sibling checkout).

- **Source:** an internal downstream-consumer certificate corpus (vendored)
- **SHA-256:** `a644c6e1c99c202fa5c5bbb0db3278ccd139414de84755c7bd8a21eb88c96e6e`

## Note on canon versions (why this is a v2 CROSS-ENGINE check, not a v1 diff)

These vectors are canon **v1** (in-hash NFC); canon-core is **v2** (NFC relocated to
the ingest boundary). So the committed **digests are v1-domain** and the **NFC vectors'
bytes differ by design** from a v2 engine. The gate therefore does NOT compare v2
output against the corpus's committed v1 bytes; it holds the five v2 engines identical
to **each other** over the corpus INPUTS (Python v2 reference as the baseline). That is
the serializer-parity claim SPEC §5.0 protects; the v1→v2 boundary itself is a
downstream consumer's dual-run responsibility, not this serializer gate.

## Re-vendor procedure

Re-copy from the upstream source, update the SHA-256 above, and re-run the XG-4 gate.
When a live corpus is provided via the `GOV_CORPUS` env var (opt-in), the gate's
OPTIONAL cross-check asserts the live file is byte-identical to this committed copy —
so drift is caught at the source.
