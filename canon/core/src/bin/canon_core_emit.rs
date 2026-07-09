use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use canon_core::{canonical_bytes, decode_input, digest, parse_vector_corpus};
use serde_json::Value;
use std::env;
use std::fs;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let vector_path = env::args()
        .nth(1)
        .unwrap_or_else(|| "../../vectors/canon-vectors.json".to_string());
    let raw = fs::read_to_string(vector_path)?;
    let vectors: Vec<Value> = parse_vector_corpus(&raw)?;
    let mut lines = Vec::with_capacity(vectors.len());
    for vector in vectors {
        let name = vector["name"].as_str().ok_or("vector missing name")?;
        let value = decode_input(&vector["input"])?;
        if vector.get("expect_error").and_then(Value::as_bool).unwrap_or(false) {
            match canonical_bytes(&value) {
                Ok(bytes) => {
                    return Err(format!("expected CanonError but encoding succeeded for {name}: {bytes:?}").into());
                }
                Err(_) => lines.push(format!("{name}\tERROR\tERROR")),
            }
            continue;
        }
        let profile = vector["profile"].as_str().ok_or("vector missing profile")?;
        let bytes = canonical_bytes(&value)?;
        let bytes_b64 = STANDARD.encode(bytes);
        let digest_hex = digest(&value, profile)?;
        lines.push(format!("{name}\t{bytes_b64}\t{digest_hex}"));
    }
    lines.sort();
    println!("{}", lines.join("\n"));
    Ok(())
}
