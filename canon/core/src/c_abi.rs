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
//!   `canon_string_free`). `*err_out` is NON-NULL on every `CANON_ERR`, and messages never echo
//!   caller payloads. No Rust panic is ever allowed to unwind across the FFI boundary: every
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

/// Copy a Rust `String` into a freshly heap-allocated NUL-terminated C string. Never returns
/// null: an interior NUL (impossible for hex digests, and error messages no longer echo caller
/// payloads — but stay total) is escaped as the six characters `\u0000` and the copy retried,
/// which cannot fail.
fn into_c_string(value: String) -> *mut c_char {
    match CString::new(value) {
        Ok(cstr) => cstr.into_raw(),
        Err(err) => {
            let escaped = err.into_vec();
            let escaped = String::from_utf8_lossy(&escaped).replace('\0', "\\u0000");
            CString::new(escaped)
                .expect("interior NULs were just escaped")
                .into_raw()
        }
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
        // The error is an owned String, copied once into the caller's C string by set_error and then
        // dropped here. (It used to be Box::leak'ed to get a &'static str, leaking one message per
        // failed call on top of the copy the caller frees.)
        let input = borrow_utf8(tagged_json).map_err(str::to_string)?;
        canonical_bytes_from_json(input).map_err(|e| e.to_string())
    }));

    match result {
        Ok(Ok(bytes)) => {
            if bytes_out.is_null() || len_out.is_null() {
                set_error(err_out, "null output pointer");
                return CANON_ERR;
            }
            // A boxed slice's allocation is exactly `len` bytes. `Vec::shrink_to_fit` does NOT
            // guarantee `capacity == len`, and `canon_bytes_free` used to rebuild the Vec with
            // capacity `len` — deallocating with a different layout than was allocated is undefined
            // behaviour (CAN-3). The (ptr, len) pair now describes the
            // allocation exactly, and `canon_bytes_free` rebuilds the same boxed slice.
            let boxed: Box<[u8]> = bytes.into_boxed_slice();
            let len = boxed.len();
            let ptr = Box::into_raw(boxed) as *mut u8; // ownership transfers to the caller
            *bytes_out = ptr;
            *len_out = len;
            CANON_OK
        }
        Ok(Err(message)) => {
            set_error(err_out, &message);
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
        let input = borrow_utf8(tagged_json).map_err(str::to_string)?;
        let prof = borrow_utf8(profile).map_err(str::to_string)?;
        digest_from_json(input, prof).map_err(|e| e.to_string())
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
            set_error(err_out, &message);
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
    // Rebuild the exact boxed slice `canon_canonical_bytes_from_json` leaked (allocation == len bytes).
    drop(Box::from_raw(ptr::slice_from_raw_parts_mut(ptr, len)));
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

#[cfg(test)]
mod tests {
    use super::*;

    /// CAN-10: an interior NUL in an error message must never turn into a null `*err_out`.
    #[test]
    fn into_c_string_never_returns_null_on_interior_nul() {
        let ptr = into_c_string("bad\0payload".to_string());
        assert!(!ptr.is_null(), "interior NUL must be escaped, not dropped to a null pointer");
        // SAFETY: `ptr` was just produced by `CString::into_raw`; we take ownership back here.
        let owned = unsafe { CString::from_raw(ptr) };
        assert_eq!(owned.to_str().unwrap(), "bad\\u0000payload");
    }

    /// CAN-3: the returned (ptr, len) pair must round-trip through `canon_bytes_free` for every
    /// output size (the layout rebuilt at free must be the one allocated). Not a fail-before test —
    /// the old shrink_to_fit path was UB only when an allocator kept spare capacity, which the
    /// system allocator does not; Miri would flag it. It pins the boxed-slice contract.
    #[test]
    fn canonical_bytes_buffer_round_trips_through_canon_bytes_free() {
        for input in [r#"null"#, r#"{"$int":"7"}"#, r#"{"$str":"0123456789abcdef0123456789abcdef"}"#, r#"[[],{},[[]]]"#] {
            let json = CString::new(input).unwrap();
            let mut bytes: *mut u8 = ptr::null_mut();
            let mut len: usize = 0;
            let mut err: *mut c_char = ptr::null_mut();
            // SAFETY: all out-pointers are valid for the duration of the call.
            let rc = unsafe { canon_canonical_bytes_from_json(json.as_ptr(), &mut bytes, &mut len, &mut err) };
            assert_eq!(rc, CANON_OK, "{input}");
            assert!(err.is_null());
            let expected = crate::canonical_bytes_from_json(input).unwrap();
            // SAFETY: (bytes, len) is the pair the call just wrote.
            assert_eq!(unsafe { std::slice::from_raw_parts(bytes, len) }, expected.as_slice());
            // SAFETY: freed exactly once with the pair the call wrote.
            unsafe { canon_bytes_free(bytes, len) };
        }
    }

    #[test]
    fn digest_error_pointer_is_non_null_for_interior_nul_payload() {
        let json = CString::new(r#"{"$int":"12\u000034"}"#).unwrap();
        let profile = CString::new("semantic-content").unwrap();
        let mut digest: *mut c_char = ptr::null_mut();
        let mut err: *mut c_char = ptr::null_mut();
        // SAFETY: all four pointers are valid for the duration of the call.
        let rc = unsafe { canon_digest_from_json(json.as_ptr(), profile.as_ptr(), &mut digest, &mut err) };
        assert_eq!(rc, CANON_ERR);
        assert!(digest.is_null());
        assert!(!err.is_null(), "*err_out must be non-null on CANON_ERR");
        // SAFETY: `err` was produced by `into_c_string`; reclaiming it frees it on our allocator.
        let msg = unsafe { CString::from_raw(err) };
        let msg = msg.to_str().unwrap();
        assert!(msg.contains("invalid $int payload"), "{msg}");
        assert!(!msg.contains("12"), "message must not echo the payload: {msg}");
    }
}
