use num_bigint::BigInt;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::fmt;

pub const DOMAIN_PREFIX: &str = "spec-canon:v2:";
pub const PROFILES: [&str; 4] = ["semantic-content", "run", "artifact-byte", "certificate"];

/// SPEC.md section 1 — maximum nesting depth (CAN-12). A container's depth is
/// 1 + the number of containers enclosing it (`[]` is depth 1). Any container deeper than this is a
/// `CanonError` at decode and at encode, identically in the Python and TypeScript ports. Before the
/// cap the native encoder overflowed the stack (SIGABRT) on deep values, and the JSON entry points
/// rejected anything past serde_json's own 127-level TEXT limit, which a `$arr`/`$obj`-tagged value
/// reaches at canonical depth 65 while the reference ports accepted it.
pub const MAX_DEPTH: usize = 128;

/// The deepest tagged-JSON text that can decode to a value within `MAX_DEPTH`. Every tag payload is a
/// scalar except `$obj`/`$arr`, which cost two JSON levels per container (wrapper + payload), and a
/// scalar tag wrapper adds one level at a leaf: so 2 * MAX_DEPTH + 1. Deeper text is rejected before
/// serde_json parses it (which bounds serde's recursion now that its own limit is lifted); anything
/// it rejects would be rejected by the reference ports too (depth or payload type).
const MAX_TAGGED_JSON_DEPTH: usize = 2 * MAX_DEPTH + 1;

const DEPTH_MESSAGE: &str = "nesting depth exceeds the maximum of 128";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CanonValue {
    Null,
    Bool(bool),
    Int(BigInt),
    String(String),
    Array(Vec<CanonValue>),
    Object(Vec<(String, CanonValue)>),
    FloatMarker,
    NanMarker,
    InfMarker,
    SurrogateMarker,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonError {
    message: String,
}

impl CanonError {
    fn new(message: impl Into<String>) -> Self {
        Self { message: message.into() }
    }
}

impl fmt::Display for CanonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CanonError {}

pub type CanonResult<T> = Result<T, CanonError>;

/// `depth` counts the containers enclosing the one being built; returns the new container's depth.
fn container_depth(depth: usize) -> CanonResult<usize> {
    if depth >= MAX_DEPTH {
        Err(CanonError::new(DEPTH_MESSAGE))
    } else {
        Ok(depth + 1)
    }
}

fn compare_code_points(a: &str, b: &str) -> Ordering {
    let mut ai = a.chars();
    let mut bi = b.chars();
    loop {
        match (ai.next(), bi.next()) {
            (None, None) => return Ordering::Equal,
            (None, Some(_)) => return Ordering::Less,
            (Some(_), None) => return Ordering::Greater,
            (Some(ac), Some(bc)) => match (ac as u32).cmp(&(bc as u32)) {
                Ordering::Equal => {}
                other => return other,
            },
        }
    }
}

fn encode_string(value: &str) -> CanonResult<String> {
    let mut out = String::from("\"");
    for ch in value.chars() {
        let cp = ch as u32;
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            _ if cp <= 0x1f => out.push_str(&format!("\\u{cp:04x}")),
            _ => out.push(ch),
        }
    }
    out.push('"');
    Ok(out)
}

/// `depth` is the number of containers enclosing `value` (0 at the top level).
fn encode_value(value: &CanonValue, depth: usize) -> CanonResult<String> {
    match value {
        CanonValue::FloatMarker => Err(CanonError::new(
            "floats are forbidden in canonical content; pre-represent as int or string",
        )),
        CanonValue::NanMarker => Err(CanonError::new("NaN is forbidden in canonical content")),
        CanonValue::InfMarker => Err(CanonError::new("Infinity is forbidden in canonical content")),
        CanonValue::SurrogateMarker => Err(CanonError::new(
            "unpaired surrogate U+D800 is not allowed in canonical content",
        )),
        CanonValue::Null => Ok("null".to_string()),
        CanonValue::Bool(v) => Ok(if *v { "true" } else { "false" }.to_string()),
        CanonValue::Int(v) => Ok(v.to_string()),
        CanonValue::String(v) => encode_string(v),
        CanonValue::Array(items) => {
            let inner = container_depth(depth)?;
            let mut parts = Vec::with_capacity(items.len());
            for item in items {
                parts.push(encode_value(item, inner)?);
            }
            Ok(format!("[{}]", parts.join(",")))
        }
        CanonValue::Object(entries) => encode_object(entries, container_depth(depth)?, false),
    }
}

/// `depth` is this object's own nesting depth (already checked). Keys are sorted through borrowed
/// references, so neither encoding nor the digest's top-level `digest`-key exclusion deep-clones
/// a subtree (a clone of a pathologically deep value would itself recurse past the stack).
fn encode_object(entries: &[(String, CanonValue)], depth: usize, exclude_digest: bool) -> CanonResult<String> {
    let mut sorted: Vec<&(String, CanonValue)> = entries
        .iter()
        .filter(|(key, _)| !(exclude_digest && key == "digest"))
        .collect();
    sorted.sort_by(|(ak, _), (bk, _)| compare_code_points(ak, bk));
    let mut parts = Vec::with_capacity(sorted.len());
    for (key, item) in sorted {
        parts.push(format!("{}:{}", encode_string(key)?, encode_value(item, depth)?));
    }
    Ok(format!("{{{}}}", parts.join(",")))
}

pub fn canonical_bytes(value: &CanonValue) -> CanonResult<Vec<u8>> {
    Ok(encode_value(value, 0)?.into_bytes())
}

/// The digest preimage body: `canonical_bytes` of the value with a TOP-LEVEL `digest` key removed
/// (SPEC.md section 8).
fn digest_body(value: &CanonValue) -> CanonResult<Vec<u8>> {
    match value {
        CanonValue::Object(entries) => Ok(encode_object(entries, container_depth(0)?, true)?.into_bytes()),
        _ => canonical_bytes(value),
    }
}

pub fn digest(value: &CanonValue, profile: &str) -> CanonResult<String> {
    if !PROFILES.contains(&profile) {
        return Err(CanonError::new(format!("unknown digest profile: {profile}")));
    }
    let mut hasher = Sha256::new();
    hasher.update(format!("{DOMAIN_PREFIX}{profile}\n").as_bytes());
    hasher.update(digest_body(value)?);
    Ok(hex::encode(hasher.finalize()))
}

/// The one payload grammar every port shares (SPEC.md section 2): an optional '-' then one or
/// more ASCII digits. `str::parse::<BigInt>` alone also accepts a leading '+', which the TS and
/// Python ports do not, so the grammar is checked first. Leading zeros and "-0" match and
/// normalise ("007" -> 7, "-0" -> 0). The message is FIXED — it never interpolates the payload,
/// because the C-ABI forwards error messages to callers verbatim.
fn parse_int_payload(payload: &Value) -> CanonResult<BigInt> {
    const MSG: &str = "invalid $int payload: expected optional '-' then ASCII digits";
    let raw = payload.as_str().ok_or_else(|| CanonError::new(MSG))?;
    let digits = raw.strip_prefix('-').unwrap_or(raw);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(CanonError::new(MSG));
    }
    raw.parse::<BigInt>().map_err(|_| CanonError::new(MSG))
}

/// Decode a type-tagged tree. Every tag's payload has exactly one accepted JSON type (SPEC.md
/// section 2); anything else is a fixed-message `CanonError` — `$bool` used to be JSON truthiness
/// here (`[]`, `{}` and `0.0` were all `true`) while Python and TypeScript disagreed with it and with
/// each other (CAN-9). Containers deeper than `MAX_DEPTH` are rejected.
pub fn decode_input(node: &Value) -> CanonResult<CanonValue> {
    decode(node, 0)
}

fn decode_entries(map: &serde_json::Map<String, Value>, depth: usize) -> CanonResult<CanonValue> {
    let inner = container_depth(depth)?;
    let mut entries = Vec::with_capacity(map.len());
    for (key, item) in map {
        entries.push((key.clone(), decode(item, inner)?));
    }
    Ok(CanonValue::Object(entries))
}

fn decode_items(items: &[Value], depth: usize) -> CanonResult<CanonValue> {
    let inner = container_depth(depth)?;
    items
        .iter()
        .map(|item| decode(item, inner))
        .collect::<CanonResult<Vec<_>>>()
        .map(CanonValue::Array)
}

fn decode(node: &Value, depth: usize) -> CanonResult<CanonValue> {
    match node {
        Value::Null => Ok(CanonValue::Null),
        Value::Bool(v) => Ok(CanonValue::Bool(*v)),
        Value::Number(n) => {
            if let Some(v) = n.as_i64() {
                Ok(CanonValue::Int(BigInt::from(v)))
            } else if let Some(v) = n.as_u64() {
                Ok(CanonValue::Int(BigInt::from(v)))
            } else {
                Ok(CanonValue::FloatMarker)
            }
        }
        Value::String(v) => Ok(CanonValue::String(v.clone())),
        Value::Array(items) => decode_items(items, depth),
        Value::Object(map) => {
            if map.len() == 1 {
                let (tag, payload) = map.iter().next().expect("single object entry");
                match tag.as_str() {
                    "$int" => return Ok(CanonValue::Int(parse_int_payload(payload)?)),
                    "$float" => return Ok(CanonValue::FloatMarker),
                    "$nan" => return Ok(CanonValue::NanMarker),
                    "$inf" => return Ok(CanonValue::InfMarker),
                    "$surrogate" => return Ok(CanonValue::SurrogateMarker),
                    "$str" => {
                        return payload
                            .as_str()
                            .map(|s| CanonValue::String(s.to_string()))
                            .ok_or_else(|| CanonError::new("invalid $str payload: expected a JSON string"));
                    }
                    "$bool" => {
                        return payload
                            .as_bool()
                            .map(CanonValue::Bool)
                            .ok_or_else(|| CanonError::new("invalid $bool payload: expected a JSON boolean"));
                    }
                    "$null" => {
                        return match payload {
                            Value::Bool(true) => Ok(CanonValue::Null),
                            _ => Err(CanonError::new("invalid $null payload: expected true")),
                        };
                    }
                    "$obj" => {
                        let obj = payload
                            .as_object()
                            .ok_or_else(|| CanonError::new("invalid $obj payload: expected a JSON object"))?;
                        return decode_entries(obj, depth);
                    }
                    "$arr" => {
                        let arr = payload
                            .as_array()
                            .ok_or_else(|| CanonError::new("invalid $arr payload: expected a JSON array"))?;
                        return decode_items(arr, depth);
                    }
                    _ => {}
                }
            }
            decode_entries(map, depth)
        }
    }
}

/// The maximum `[`/`{` nesting of JSON text, outside string literals. Iterative, so it is safe on
/// arbitrarily deep input. Exact for valid JSON; for invalid JSON it is an upper bound on the depth
/// serde_json reaches before reporting the syntax error (both tokenise strings the same way).
fn json_nesting_depth(text: &str) -> usize {
    let (mut depth, mut max, mut in_string, mut escaped) = (0usize, 0usize, false, false);
    for byte in text.bytes() {
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
            continue;
        }
        match byte {
            b'"' => in_string = true,
            b'[' | b'{' => {
                depth += 1;
                max = max.max(depth);
            }
            b']' | b'}' => depth = depth.saturating_sub(1),
            _ => {}
        }
    }
    max
}

/// Parse JSON text whose nesting is first bounded by `max_depth` (iteratively), with serde_json's
/// own 128-level recursion limit lifted: serde's limit counts TEXT levels, which is not the canonical
/// depth the cap is defined on (a tagged container costs two).
fn parse_json_bounded(text: &str, max_depth: usize) -> Result<Value, String> {
    if json_nesting_depth(text) > max_depth {
        return Err(DEPTH_MESSAGE.to_string());
    }
    let mut deserializer = serde_json::Deserializer::from_str(text);
    deserializer.disable_recursion_limit();
    let value = Value::deserialize(&mut deserializer).map_err(|error| format!("invalid JSON: {error}"))?;
    deserializer.end().map_err(|error| format!("invalid JSON: {error}"))?;
    Ok(value)
}

pub fn decode_input_json(tagged_json: &str) -> CanonResult<CanonValue> {
    let value = parse_json_bounded(tagged_json, MAX_TAGGED_JSON_DEPTH).map_err(CanonError::new)?;
    decode_input(&value)
}

pub fn parse_vector_corpus(raw: &str) -> CanonResult<Vec<Value>> {
    let rust_parseable = raw.replace("\"$str\": \"\\ud800\"", "\"$surrogate\": \"d800\"");
    // A corpus file wraps each tagged input in a few levels (the vector list, the vector object);
    // its deepest inputs are the over-the-cap rejection vectors, one container past MAX_DEPTH.
    let value = parse_json_bounded(&rust_parseable, MAX_TAGGED_JSON_DEPTH + 16)
        .map_err(|error| CanonError::new(format!("invalid vector corpus: {error}")))?;
    serde_json::from_value(value).map_err(|error| CanonError::new(format!("invalid vector corpus: {error}")))
}

pub fn canonical_bytes_from_json(tagged_json: &str) -> CanonResult<Vec<u8>> {
    canonical_bytes(&decode_input_json(tagged_json)?)
}

pub fn digest_from_json(tagged_json: &str, profile: &str) -> CanonResult<String> {
    digest(&decode_input_json(tagged_json)?, profile)
}

#[cfg(feature = "c-binding")]
pub mod c_abi;

#[cfg(feature = "wasm-binding")]
use wasm_bindgen::prelude::*;

#[cfg(feature = "wasm-binding")]
#[wasm_bindgen(js_name = canonicalBytesFromJson)]
pub fn wasm_canonical_bytes_from_json(tagged_json: &str) -> Result<Vec<u8>, JsValue> {
    canonical_bytes_from_json(tagged_json).map_err(|error| JsValue::from_str(&error.to_string()))
}

#[cfg(feature = "wasm-binding")]
#[wasm_bindgen(js_name = digestFromJson)]
pub fn wasm_digest_from_json(tagged_json: &str, profile: &str) -> Result<String, JsValue> {
    digest_from_json(tagged_json, profile).map_err(|error| JsValue::from_str(&error.to_string()))
}

#[cfg(feature = "pyo3-binding")]
use pyo3::prelude::*;

#[cfg(feature = "pyo3-binding")]
fn py_error(error: CanonError) -> PyErr {
    pyo3::exceptions::PyValueError::new_err(error.to_string())
}

#[cfg(feature = "pyo3-binding")]
#[pyfunction(name = "canonical_bytes_from_json")]
fn py_canonical_bytes_from_json(tagged_json: &str) -> PyResult<Vec<u8>> {
    canonical_bytes_from_json(tagged_json).map_err(py_error)
}

#[cfg(feature = "pyo3-binding")]
#[pyfunction(name = "digest_from_json")]
fn py_digest_from_json(tagged_json: &str, profile: &str) -> PyResult<String> {
    digest_from_json(tagged_json, profile).map_err(py_error)
}

#[cfg(feature = "pyo3-binding")]
#[pymodule]
fn canon_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(py_canonical_bytes_from_json, module)?)?;
    module.add_function(wrap_pyfunction!(py_digest_from_json, module)?)?;
    Ok(())
}
