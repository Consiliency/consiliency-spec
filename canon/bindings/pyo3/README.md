# canon-core PyO3 binding

The canonical algorithm lives in `canon/core`. Build the PyO3 surface with:

```bash
cargo build --manifest-path canon/core/Cargo.toml --features pyo3-binding
```

The Python module is named `canon_core` and exposes:

- `canonical_bytes_from_json(tagged_json: str) -> bytes`
- `digest_from_json(tagged_json: str, profile: str) -> str`

Inputs are the existing type-tagged vector JSON shape used by `canon/vectors/canon-vectors.json`.
