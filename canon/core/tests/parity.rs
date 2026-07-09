use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use canon_core::{canonical_bytes, decode_input, digest, parse_vector_corpus};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn vectors_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../vectors/canon-vectors.json")
}

#[test]
fn rust_core_matches_pinned_vectors() {
    let raw = fs::read_to_string(vectors_path()).expect("read canon vectors");
    let vectors: Vec<Value> = parse_vector_corpus(&raw).expect("parse canon vectors");
    assert!(!vectors.is_empty(), "canon vector corpus must be non-empty");
    for vector in vectors {
        let name = vector["name"].as_str().expect("name");
        let value = decode_input(&vector["input"]).unwrap_or_else(|error| panic!("{name}: decode failed: {error}"));
        if vector.get("expect_error").and_then(Value::as_bool).unwrap_or(false) {
            assert!(canonical_bytes(&value).is_err(), "{name}: expected canonical_bytes to reject");
            continue;
        }
        let profile = vector["profile"].as_str().expect("profile");
        let bytes = canonical_bytes(&value).unwrap_or_else(|error| panic!("{name}: encode failed: {error}"));
        let bytes_b64 = STANDARD.encode(bytes);
        assert_eq!(
            bytes_b64,
            vector["expected_canonical_bytes_b64"].as_str().expect("expected bytes"),
            "{name}: bytes mismatch",
        );
        let digest_hex = digest(&value, profile).unwrap_or_else(|error| panic!("{name}: digest failed: {error}"));
        assert_eq!(
            digest_hex,
            vector["expected_digest_hex"].as_str().expect("expected digest"),
            "{name}: digest mismatch",
        );
    }
}
