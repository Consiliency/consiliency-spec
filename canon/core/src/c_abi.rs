//! C ABI binding (`c-binding` feature) — a stable `extern "C"` surface over the published canon
//! engine for `dart:ffi` on mobile (and any other C-ABI consumer).
//!
//! WHY THIS EXISTS
//! ---------------
//! canon-core already ships pyo3 (Python) and wasm-bindgen (Node/web) bindings. Dart on mobile has
//! neither: it calls native libraries through `dart:ffi`, which speaks the C ABI. This module adds
//! that fourth surface — the SAME two engine functions, `canonical_bytes_from_json` and
//! `digest_from_json`, exposed as `extern "C"` / `#[no_mangle]` entry points — WITHOUT touching the
//! canon algorithm. It is additive and feature-gated (`c-binding`, off by default); the pyo3 and
//! wasm bindings are untouched.
//!
//! CALLING / OWNERSHIP CONTRACT (this is IF-0-DABI-1; the Dart `dart:ffi` layer pins it)
//! ------------------------------------------------------------------------------------
//! - INPUT strings are NUL-terminated UTF-8 C strings (`const char *`), owned by the caller. The
//!   engine reads them; it never frees or retains them.
//! - `canon_canonical_bytes_from_json` returns the canonical BYTES via an out-pointer + out-length,
//!   because canonical bytes are arbitrary binary (they may contain interior NUL and non-UTF-8 —
//!   actually canon bytes are always valid UTF-8 text, but the contract is length-delimited, never
//!   NUL-terminated, so it stays correct if that ever changes).
//! - `canon_digest_from_json` returns the digest as a NUL-terminated UTF-8 C string (hex, ASCII).
//! - On SUCCESS a function returns `CANON_OK` (0) and writes its output param(s).
//! - On FAILURE a function returns `CANON_ERR` (1), writes null/zero to the output param(s), and
//!   writes a NUL-terminated UTF-8 error message to `*err_out` (the caller frees it with
//!   `canon_string_free`). No Rust panic is ever allowed to unwind across the FFI boundary: every
//!   entry point is wrapped in `catch_unwind` and a caught panic is reported as `CANON_ERR`.
//! - OWNERSHIP OF RETURNED BUFFERS: every buffer the engine hands back (`*bytes_out`, the digest
//!   string, `*err_out`) is heap-allocated by THIS crate and MUST be freed by the matching free
//!   function — `canon_bytes_free(ptr, len)` for byte buffers, `canon_string_free(ptr)` for C
//!   strings — so allocation and deallocation stay on the same allocator. Freeing them any other way
//!   (libc `free`, Dart `malloc.free`) is undefined behavior.
//! - THREADING: the entry points are pure functions with no shared mutable state; they are safe to
//!   call concurrently from multiple threads / isolates.

use crate::{canonical_bytes_from_json, digest_from_json};
use std::ffi::{c_char, c_int, CStr, CString};
use std::os::raw::c_void;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

/// Status: the call succeeded and the output params are populated.
pub const CANON_OK: c_int = 0;
/// Status: the call failed; `*err_out` holds a NUL-terminated UTF-8 message the caller must free.
pub const CANON_ERR: c_int = 1;

/// Copy a Rust `String` into a freshly heap-allocated NUL-terminated C string.
/// Returns null if the string contains an interior NUL (never happens for our hex digests /
/// error messages, but we stay total rather than panic).
fn into_c_string(value: String) -> *mut c_char {
    match CString::new(value) {
        Ok(cstr) => cstr.into_raw(),
        Err(_) => ptr::null_mut(),
    }
}

/// Write a UTF-8 error message to `*err_out` (if non-null) as an owned C string.
fn set_error(err_out: *mut *mut c_char, message: &str) {
    if !err_out.is_null() {
        // SAFETY: caller guarantees err_out points at a writable `*mut c_char`.
        unsafe {
            *err_out = into_c_string(message.to_string());
        }
    }
}

/// Borrow a `&str` from a caller-owned NUL-terminated UTF-8 C string.
/// Returns `Err(message)` on a null pointer or invalid UTF-8.
///
/// SAFETY: `ptr` must be null or a valid pointer to a NUL-terminated C string.
unsafe fn borrow_utf8<'a>(ptr: *const c_char) -> Result<&'a str, &'static str> {
    if ptr.is_null() {
        return Err("null input pointer");
    }
    CStr::from_ptr(ptr)
        .to_str()
        .map_err(|_| "input is not valid UTF-8")
}

/// Canonical bytes over a tagged-JSON-text input.
///
/// `tagged_json`  : caller-owned NUL-terminated UTF-8 C string (the DENC-produced tagged text).
/// `bytes_out`    : receives a heap pointer to the canonical bytes (free with `canon_bytes_free`).
/// `len_out`      : receives the length in bytes.
/// `err_out`      : on failure receives a heap error string (free with `canon_string_free`).
///
/// Returns `CANON_OK` or `CANON_ERR`. Byte-identical to `canon_core::canonical_bytes_from_json`.
///
/// # Safety
/// All pointers must be valid per the module ownership contract.
#[no_mangle]
pub unsafe extern "C" fn canon_canonical_bytes_from_json(
    tagged_json: *const c_char,
    bytes_out: *mut *mut u8,
    len_out: *mut usize,
    err_out: *mut *mut c_char,
) -> c_int {
    // Null the outputs up front so a caller who forgets to check the status never reads garbage.
    if !bytes_out.is_null() {
        *bytes_out = ptr::null_mut();
    }
    if !len_out.is_null() {
        *len_out = 0;
    }
    if !err_out.is_null() {
        *err_out = ptr::null_mut();
    }

    let result = catch_unwind(AssertUnwindSafe(|| {
        let input = borrow_utf8(tagged_json)?;
        canonical_bytes_from_json(input).map_err(|e| {
            // Leak into a 'static-ish owned message via set_error at the call site; here return owned.
            Box::leak(e.to_string().into_boxed_str()) as &str
        })
    }));

    match result {
        Ok(Ok(mut bytes)) => {
            if bytes_out.is_null() || len_out.is_null() {
                set_error(err_out, "null output pointer");
                return CANON_ERR;
            }
            bytes.shrink_to_fit();
            let len = bytes.len();
            let ptr = bytes.as_mut_ptr();
            std::mem::forget(bytes); // ownership transfers to the caller (freed via canon_bytes_free)
            *bytes_out = ptr;
            *len_out = len;
            CANON_OK
        }
        Ok(Err(message)) => {
            set_error(err_out, message);
            CANON_ERR
        }
        Err(_) => {
            set_error(err_out, "canon-core panicked while computing canonical bytes");
            CANON_ERR
        }
    }
}

/// Digest (hex) over a tagged-JSON-text input under a profile.
///
/// `tagged_json` : caller-owned NUL-terminated UTF-8 C string.
/// `profile`     : caller-owned NUL-terminated UTF-8 C string (one of the four canon profiles).
/// `digest_out`  : receives a heap NUL-terminated hex C string (free with `canon_string_free`).
/// `err_out`     : on failure receives a heap error string (free with `canon_string_free`).
///
/// Returns `CANON_OK` or `CANON_ERR`. Byte-identical to `canon_core::digest_from_json`.
///
/// # Safety
/// All pointers must be valid per the module ownership contract.
#[no_mangle]
pub unsafe extern "C" fn canon_digest_from_json(
    tagged_json: *const c_char,
    profile: *const c_char,
    digest_out: *mut *mut c_char,
    err_out: *mut *mut c_char,
) -> c_int {
    if !digest_out.is_null() {
        *digest_out = ptr::null_mut();
    }
    if !err_out.is_null() {
        *err_out = ptr::null_mut();
    }

    let result = catch_unwind(AssertUnwindSafe(|| {
        let input = borrow_utf8(tagged_json)?;
        let prof = borrow_utf8(profile)?;
        digest_from_json(input, prof).map_err(|e| Box::leak(e.to_string().into_boxed_str()) as &str)
    }));

    match result {
        Ok(Ok(digest)) => {
            if digest_out.is_null() {
                set_error(err_out, "null output pointer");
                return CANON_ERR;
            }
            let c = into_c_string(digest);
            if c.is_null() {
                set_error(err_out, "digest contained an interior NUL (should be impossible)");
                return CANON_ERR;
            }
            *digest_out = c;
            CANON_OK
        }
        Ok(Err(message)) => {
            set_error(err_out, message);
            CANON_ERR
        }
        Err(_) => {
            set_error(err_out, "canon-core panicked while computing the digest");
            CANON_ERR
        }
    }
}

/// Free a byte buffer returned by `canon_canonical_bytes_from_json`.
///
/// # Safety
/// `ptr`/`len` must be exactly the pair a `canon_canonical_bytes_from_json` call wrote, and must be
/// freed at most once. A null `ptr` is a no-op.
#[no_mangle]
pub unsafe extern "C" fn canon_bytes_free(ptr: *mut u8, len: usize) {
    if ptr.is_null() {
        return;
    }
    // Reconstitute the Vec with capacity == len (we shrank_to_fit before forgetting it) and drop it.
    drop(Vec::from_raw_parts(ptr, len, len));
}

/// Free a C string returned by `canon_digest_from_json` or via an `err_out` param.
///
/// # Safety
/// `ptr` must be a string this crate returned, freed at most once. A null `ptr` is a no-op.
#[no_mangle]
pub unsafe extern "C" fn canon_string_free(ptr: *mut c_char) {
    if ptr.is_null() {
        return;
    }
    drop(CString::from_raw(ptr));
}

// Keep `c_void` referenced so cbindgen always emits `#include <stdint.h>`/stddef via the header
// config; also documents that no opaque handle type crosses this ABI (the surface is stateless).
#[allow(dead_code)]
type CanonNoOpaqueHandle = *mut c_void;
