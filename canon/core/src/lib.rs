use num_bigint::BigInt;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::fmt;

pub const DOMAIN_PREFIX: &str = "spec-canon:v2:";
pub const PROFILES: [&str; 4] = ["semantic-content", "run", "artifact-byte", "certificate"];

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

fn encode_value(value: &CanonValue) -> CanonResult<String> {
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
            let mut parts = Vec::with_capacity(items.len());
            for item in items {
                parts.push(encode_value(item)?);
            }
            Ok(format!("[{}]", parts.join(",")))
        }
        CanonValue::Object(entries) => {
            let mut sorted = entries.clone();
            sorted.sort_by(|(ak, _), (bk, _)| compare_code_points(ak, bk));
            let mut parts = Vec::with_capacity(sorted.len());
            for (key, item) in sorted {
                parts.push(format!("{}:{}", encode_string(&key)?, encode_value(&item)?));
            }
            Ok(format!("{{{}}}", parts.join(",")))
        }
    }
}

pub fn canonical_bytes(value: &CanonValue) -> CanonResult<Vec<u8>> {
    Ok(encode_value(value)?.into_bytes())
}

fn strip_top_level_digest(value: &CanonValue) -> CanonValue {
    match value {
        CanonValue::Object(entries) => CanonValue::Object(
            entries
                .iter()
                .filter(|(key, _)| key != "digest")
                .cloned()
                .collect(),
        ),
        _ => value.clone(),
    }
}

pub fn digest(value: &CanonValue, profile: &str) -> CanonResult<String> {
    if !PROFILES.contains(&profile) {
        return Err(CanonError::new(format!("unknown digest profile: {profile}")));
    }
    let mut hasher = Sha256::new();
    hasher.update(format!("{DOMAIN_PREFIX}{profile}\n").as_bytes());
    hasher.update(canonical_bytes(&strip_top_level_digest(value))?);
    Ok(hex::encode(hasher.finalize()))
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(n) => n.as_i64().map_or(true, |v| v != 0),
        Value::String(s) => !s.is_empty(),
        Value::Array(_) | Value::Object(_) => true,
    }
}

fn parse_int_payload(payload: &Value) -> CanonResult<BigInt> {
    let raw = payload
        .as_str()
        .ok_or_else(|| CanonError::new("$int payload must be a decimal string"))?;
    raw.parse::<BigInt>()
        .map_err(|_| CanonError::new(format!("invalid integer payload: {raw}")))
}

pub fn decode_input(node: &Value) -> CanonResult<CanonValue> {
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
        Value::Array(items) => items.iter().map(decode_input).collect::<CanonResult<Vec<_>>>().map(CanonValue::Array),
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
                            .ok_or_else(|| CanonError::new("$str payload must be a string"));
                    }
                    "$bool" => return Ok(CanonValue::Bool(json_truthy(payload))),
                    "$null" => return Ok(CanonValue::Null),
                    "$obj" => {
                        let obj = payload
                            .as_object()
                            .ok_or_else(|| CanonError::new("$obj payload must be an object"))?;
                        let mut entries = Vec::with_capacity(obj.len());
                        for (key, item) in obj {
                            entries.push((key.clone(), decode_input(item)?));
                        }
                        return Ok(CanonValue::Object(entries));
                    }
                    "$arr" => {
                        let arr = payload
                            .as_array()
                            .ok_or_else(|| CanonError::new("$arr payload must be an array"))?;
                        return arr.iter().map(decode_input).collect::<CanonResult<Vec<_>>>().map(CanonValue::Array);
                    }
                    _ => {}
                }
            }
            let mut entries = Vec::with_capacity(map.len());
            for (key, item) in map {
                entries.push((key.clone(), decode_input(item)?));
            }
            Ok(CanonValue::Object(entries))
        }
    }
}

pub fn decode_input_json(tagged_json: &str) -> CanonResult<CanonValue> {
    let value: Value = serde_json::from_str(tagged_json)
        .map_err(|error| CanonError::new(format!("invalid JSON: {error}")))?;
    decode_input(&value)
}

pub fn parse_vector_corpus(raw: &str) -> CanonResult<Vec<Value>> {
    let rust_parseable = raw.replace("\"$str\": \"\\ud800\"", "\"$surrogate\": \"d800\"");
    serde_json::from_str(&rust_parseable)
        .map_err(|error| CanonError::new(format!("invalid vector corpus: {error}")))
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
