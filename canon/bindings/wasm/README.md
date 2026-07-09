# canon-core WASM binding

The canonical algorithm lives in `canon/core`. Build the WASM surface with:

```bash
cargo build --manifest-path canon/core/Cargo.toml --features wasm-binding --target wasm32-unknown-unknown
```

The exported JS names are:

- `canonicalBytesFromJson(taggedJson: string): Uint8Array`
- `digestFromJson(taggedJson: string, profile: string): string`

Inputs are the existing type-tagged vector JSON shape used by `canon/vectors/canon-vectors.json`.
