use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use canon_core::{canonical_bytes, canonical_bytes_from_json, decode_vector_input, digest, digest_from_json, parse_vector_corpus};
use serde_json::Value;
use std::env;
use std::fs;

/// `canon_core_emit [<corpus.json>]` emits `name\tbytes_b64\tdigest` per corpus vector (`ERROR\tERROR`
/// for a reject vector), sorted by name, in the format of `py/test_canon.py --emit`.
///
/// `canon_core_emit --boundary <engine_boundary_vectors.json>` emits `name\tOK|ERROR\tbytes_b64\tdigest`
/// (`-\t-` on rejection), sorted by name, in the format of `py/test_canon.py --emit-boundary`. Each
/// vector goes through the PRODUCTION JSON entry points (its `raw` text, or its serialized `input`).
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    let lines = match args.as_slice() {
        [flag, path] if flag == "--boundary" => emit_boundary(path)?,
        [path] => emit_corpus(path)?,
        [] => emit_corpus("../../vectors/canon-vectors.json")?,
        _ => return Err("usage: canon_core_emit [<corpus.json>] | --boundary <boundary.json>".into()),
    };
    println!("{}", lines.join("\n"));
    Ok(())
}

fn emit_corpus(vector_path: &str) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let raw = fs::read_to_string(vector_path)?;
    let vectors: Vec<Value> = parse_vector_corpus(&raw)?;
    let mut lines = Vec::with_capacity(vectors.len());
    for vector in vectors {
        let name = vector["name"].as_str().ok_or("vector missing name")?;
        if vector.get("expect_error").and_then(Value::as_bool).unwrap_or(false) {
            // decode is INSIDE the guard: a $int payload-grammar rejection is an Err at decode.
            match decode_vector_input(&vector["input"]).and_then(|value| canonical_bytes(&value)) {
                Ok(bytes) => {
                    return Err(format!("expected CanonError but encoding succeeded for {name}: {bytes:?}").into());
                }
                Err(_) => lines.push(format!("{name}\tERROR\tERROR")),
            }
            continue;
        }
        let value = decode_vector_input(&vector["input"])?;
        let profile = vector["profile"].as_str().ok_or("vector missing profile")?;
        let bytes = canonical_bytes(&value)?;
        let bytes_b64 = STANDARD.encode(bytes);
        let digest_hex = digest(&value, profile)?;
        lines.push(format!("{name}\t{bytes_b64}\t{digest_hex}"));
    }
    lines.sort();
    Ok(lines)
}

fn emit_boundary(path: &str) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let vectors: Vec<Value> = serde_json::from_str(&fs::read_to_string(path)?)?;
    let mut lines = Vec::with_capacity(vectors.len());
    for vector in vectors {
        let name = vector["name"].as_str().ok_or("boundary vector missing name")?;
        let tagged = match vector.get("raw") {
            Some(raw) => raw.as_str().ok_or("raw is not a string")?.to_string(),
            None => serde_json::to_string(&vector["input"])?,
        };
        match canonical_bytes_from_json(&tagged) {
            Ok(bytes) => {
                let digest_hex = digest_from_json(&tagged, "semantic-content")?;
                lines.push(format!("{name}\tOK\t{}\t{digest_hex}", STANDARD.encode(bytes)));
            }
            Err(_) => lines.push(format!("{name}\tERROR\t-\t-")),
        }
    }
    lines.sort();
    Ok(lines)
}
