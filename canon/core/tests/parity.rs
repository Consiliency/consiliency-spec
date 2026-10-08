use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use canon_core::{
    canonical_bytes, canonical_bytes_from_json, decode_input, decode_vector_input, digest, digest_from_json,
    parse_vector_corpus, CanonValue, MAX_DEPTH,
};
use num_bigint::BigInt;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn vectors_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../vectors/canon-vectors.json")
}

fn boundary_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../conformance/engine_boundary_vectors.json")
}

#[test]
fn rust_core_matches_pinned_vectors() {
    let raw = fs::read_to_string(vectors_path()).expect("read canon vectors");
    let vectors: Vec<Value> = parse_vector_corpus(&raw).expect("parse canon vectors");
    assert!(!vectors.is_empty(), "canon vector corpus must be non-empty");
    for vector in vectors {
        let name = vector["name"].as_str().expect("name");
        if vector.get("expect_error").and_then(Value::as_bool).unwrap_or(false) {
            // decode is INSIDE the guard: a $int payload-grammar rejection is an Err at decode.
            let rejected = decode_vector_input(&vector["input"]).and_then(|value| canonical_bytes(&value)).is_err();
            assert!(rejected, "{name}: expected decode_input/canonical_bytes to reject");
            continue;
        }
        let value =
            decode_vector_input(&vector["input"]).unwrap_or_else(|error| panic!("{name}: decode failed: {error}"));
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

/// The production JSON entry points must agree with the corpus too: every vector goes through
/// `canonical_bytes_from_json` / `digest_from_json` exactly as a PyO3, WASM or C-ABI caller sends it.
/// (The test above decodes an already-parsed tree, which bypasses the JSON text path where serde's
/// own depth limit used to reject `$arr`/`$obj`-tagged values at canonical depth 65.)
#[test]
fn rust_json_api_matches_pinned_vectors() {
    let raw = fs::read_to_string(vectors_path()).expect("read canon vectors");
    for vector in parse_vector_corpus(&raw).expect("parse canon vectors") {
        let name = vector["name"].as_str().expect("name");
        if name == "reject-lone-surrogate" {
            continue; // rewritten to a `$surrogate` marker for Rust; serde rejects the real escape at parse
        }
        let tagged = serde_json::to_string(&vector["input"]).expect("serialize input");
        if vector.get("expect_error").and_then(Value::as_bool).unwrap_or(false) {
            assert!(canonical_bytes_from_json(&tagged).is_err(), "{name}: expected rejection from the JSON API");
            continue;
        }
        let profile = vector["profile"].as_str().expect("profile");
        let bytes = canonical_bytes_from_json(&tagged).unwrap_or_else(|error| panic!("{name}: {error}"));
        assert_eq!(STANDARD.encode(bytes), vector["expected_canonical_bytes_b64"].as_str().unwrap(), "{name}");
        let digest_hex = digest_from_json(&tagged, profile).unwrap_or_else(|error| panic!("{name}: {error}"));
        assert_eq!(digest_hex, vector["expected_digest_hex"].as_str().unwrap(), "{name}");
    }
}

/// CAN-13: the engine boundary vectors used to run only against the already-published packages
/// (check_published_canon_core.sh), so a regression in this tree was invisible to the source gates.
#[test]
fn rust_core_matches_engine_boundary_vectors() {
    check_boundary_file(boundary_path(), 26);
}

/// Boundary vectors for behaviour no published canon-core has yet (run against in-tree engines only).
/// Empty since the canon-core 0.4.0 repin folded duplicate-key-f1..f6 into the main file.
#[test]
fn rust_core_matches_next_engine_boundary_vectors() {
    check_boundary_file(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../conformance/engine_boundary_vectors_next.json"),
        0,
    );
}

fn check_boundary_file(path: PathBuf, minimum: usize) {
    let raw = fs::read_to_string(&path).expect("read boundary vectors");
    let vectors: Vec<Value> = serde_json::from_str(&raw).expect("parse boundary vectors");
    assert!(vectors.len() >= minimum, "{path:?} looks truncated");
    for vector in vectors {
        let name = vector["name"].as_str().expect("name");
        let tagged = match vector.get("raw") {
            Some(raw) => raw.as_str().expect("raw is a string").to_string(),
            None => serde_json::to_string(&vector["input"]).expect("serialize input"),
        };
        let result = canonical_bytes_from_json(&tagged);
        match vector["expect"].as_str().expect("expect") {
            "reject" => assert!(result.is_err(), "{name}: expected rejection"),
            "accept" => {
                let bytes = result.unwrap_or_else(|error| panic!("{name}: expected acceptance: {error}"));
                if let Some(expected) = vector.get("bytes").and_then(Value::as_str) {
                    assert_eq!(String::from_utf8(bytes).unwrap(), expected, "{name}");
                }
            }
            other => panic!("{name}: unknown expect {other}"),
        }
    }
}

fn nested_array(depth: usize) -> CanonValue {
    let mut value = CanonValue::Array(Vec::new());
    for _ in 1..depth {
        value = CanonValue::Array(vec![value]);
    }
    value
}

/// Tear a deep value down iteratively: the derived recursive `Drop` would itself overflow the test
/// thread's stack on the 100,000-deep value (a caller-side hazard the encoder cannot remove).
fn dismantle(mut value: CanonValue) {
    while let CanonValue::Array(mut items) = value {
        match items.pop() {
            Some(inner) => value = inner,
            None => break,
        }
    }
}

/// CAN-12: the native encoder rejects past MAX_DEPTH with a CanonError. It used to recurse until the
/// stack overflowed (SIGABRT at ~100,000), which no error handling can catch.
#[test]
fn native_encoder_enforces_max_depth() {
    assert_eq!(MAX_DEPTH, 128);
    let ok = nested_array(MAX_DEPTH);
    assert!(canonical_bytes(&ok).is_ok(), "depth {MAX_DEPTH} must be accepted");
    assert!(digest(&ok, "run").is_ok());
    for depth in [MAX_DEPTH + 1, 100_000] {
        let deep = nested_array(depth);
        let error = canonical_bytes(&deep).expect_err("over the cap must be rejected");
        assert!(error.to_string().contains("nesting depth"), "{error}");
        assert!(digest(&deep, "run").is_err());
        dismantle(deep);
    }
    // A JSON text far deeper than any valid tagged value is rejected by the iterative pre-scan before
    // serde parses it, so its recursion is bounded even though serde's own limit is lifted.
    let text = format!("{}{}", "[".repeat(1_000_000), "]".repeat(1_000_000));
    assert!(canonical_bytes_from_json(&text).is_err());
}

/// CAN-11: `$surrogate` is an ordinary key on the production API (it used to be a tag that only this
/// port honoured, rejecting what Python and TypeScript encode). The corpus loader's marker key is
/// ordinary on the production API too; only `decode_vector_input` reads it.
#[test]
fn surrogate_keys_are_ordinary_on_the_production_api() {
    for (input, expected) in [
        (r#"{"$surrogate":"d800"}"#, r#"{"$surrogate":"d800"}"#),
        (r#"{"$canon-vector:lone-surrogate":"d800"}"#, r#"{"$canon-vector:lone-surrogate":"d800"}"#),
    ] {
        let bytes = canonical_bytes_from_json(input).unwrap_or_else(|error| panic!("{input}: {error}"));
        assert_eq!(String::from_utf8(bytes).unwrap(), expected);
    }
    let marker: Value = serde_json::from_str(r#"{"$canon-vector:lone-surrogate":"d800"}"#).unwrap();
    assert_eq!(decode_vector_input(&marker).unwrap(), CanonValue::SurrogateMarker);
    assert!(matches!(decode_input(&marker).unwrap(), CanonValue::Object(_)));
    // A real lone-surrogate escape never reaches the decoder: serde_json rejects it at tokenising.
    assert!(canonical_bytes_from_json(r#"{"$str":"\ud800"}"#).is_err());
}

/// D6: a bare JSON number is an integer only inside +/-(2^53 - 1); every other bare number (past the
/// range, a fraction or exponent spelling, `-0`) is rejected, as in Python and TypeScript.
#[test]
fn bare_numbers_follow_the_safe_integer_rule() {
    for ok in ["0", "9007199254740991", "-9007199254740991", "[1,-2]"] {
        assert!(canonical_bytes_from_json(ok).is_ok(), "{ok}: expected acceptance");
    }
    for reject in [
        "9007199254740992",
        "-9007199254740992",
        "9007199254740993",
        "18446744073709551615",
        "18446744073709551616",
        "-0",
        "1.0",
        "1e3",
        "10e-1",
        "1E400",
        r#"{"n":9007199254740992}"#,
    ] {
        assert!(canonical_bytes_from_json(reject).is_err(), "{reject}: expected rejection");
    }
    let message = canonical_bytes_from_json("18446744073709551616").unwrap_err().to_string();
    assert!(message.contains("$int"), "{message}");
}

/// CAN-17: a native caller can build an Object with a repeated key; it is rejected, never emitted twice.
#[test]
fn native_duplicate_object_keys_are_rejected() {
    let one = CanonValue::Int(BigInt::from(1));
    let dup = CanonValue::Object(vec![("a".to_string(), one.clone()), ("a".to_string(), one.clone())]);
    assert!(canonical_bytes(&dup).is_err());
    assert!(digest(&dup, "run").is_err());
    let ok = CanonValue::Object(vec![("b".to_string(), one.clone()), ("a".to_string(), one)]);
    assert_eq!(canonical_bytes(&ok).unwrap(), br#"{"a":1,"b":1}"#.to_vec());
}
