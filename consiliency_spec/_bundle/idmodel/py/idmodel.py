"""idmodel v1 — Python reference implementation (two-tier identity + correspondence).

Normative contract: ../SPEC.md. Builds on canon v1
(``../../canon/py/canon.py``) — reuses ONLY canon's NFC discipline, not its JSON encoder.

Tier-1 logical-entity id = a CHIE-discipline TYPED BINARY PACK of
``{file_role, qualified_symbol, kind, normalized_signature}`` -> SHA-256 with the domain prefix
``idmodel:v1:logical\n`` -> lowercase hex. The pack is byte-identical across Python and TypeScript.

CHIE discipline (clean-room — type-tagged fixed-width fields, length-prefixed UTF-8 strings AFTER
NFC, explicit null sentinels, NO string concatenation, versioned layout):
  - all multi-byte integers are big-endian (network order);
  - strings: NFC -> UTF-8 -> 4-byte big-endian BYTE length prefix (never UTF-16 ``.length``);
  - a present field is tagged 0x01, a null field 0x00 (so present-but-empty != null);
  - the pack is assembled into a growable byte buffer, never via string concatenation.

CORRECTED identity story: Tier-1 ids are deterministic logical
keys *for a revision*. In file-as-module languages a move/rename CHANGES ``qualified_symbol`` (and a
move may change ``file_role``), so the id is NOT promised stable across refactors. Refactor tolerance
is the correspondence map, never the hash.

Public API:
    logical_pack(entity)            -> bytes   (the CHIE typed binary pack; SPEC.md section 3)
    logical_id(entity)              -> str     (lowercase hex SHA-256 over the pack)
    normalize_signature(sig)        -> dict    (the pinned signature-normalization rule; SPEC.md 4)
    occurrence_record(...)          -> dict    (Tier-2 provenance wrapper; SPEC.md section 5)
    spec_revision_logical_id(node)  -> str     (a spec node's per-revision id; file_role = level)
    engine_join_id(node)            -> str     (a spec node's engine/overlay join key; file_role null)
    LIFECYCLE                       -> the correspondence lifecycle enum (SPEC.md section 6)
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import sys
import unicodedata as _stdlib_unicodedata
from typing import Any, Dict, List, Optional

# Reuse canon's NFC discipline (and ONLY that) so the two spine contracts agree on normalization.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "canon", "py"))
import canon  # noqa: E402

# NFC is defined against a Unicode version, and the stdlib `unicodedata` is bound to the interpreter's
# (Python 3.12 ships 15.0, 3.14 ships 16.0). Hashing a non-ASCII identity under the host DB would give
# the SAME name different logical_ids on different interpreters. The id is therefore defined under the
# PINNED Unicode version canon's ingest boundary uses (canon/py/canon_ingest.py EXPECTED_UNICODE, the
# Node ICU version of the TS port). idmodel resolves that DB itself instead of importing canon_ingest:
# the minimal ingest package vendors idmodel without canon_ingest and loads every module by file
# location, so a by-name import here would both fail there and let a same-named host module stand in.
#   - the `unicodedata2` backport when it reports the pinned version, else
#   - the stdlib DB when it already reports the pinned version, else
#   - none: a pure-ASCII string still packs (NFC is the identity on ASCII under every Unicode
#     version), and a non-ASCII one raises IdModelError instead of hashing a host-dependent id.
PINNED_UNICODE = "16.0"


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _pinned_unicodedata() -> Any:
    try:
        import unicodedata2  # type: ignore[import-not-found]
    except ImportError:
        unicodedata2 = None
    for db in (unicodedata2, _stdlib_unicodedata):
        if db is not None and _major_minor(db.unidata_version) == PINNED_UNICODE:
            return db
    return None


_UNICODE_DB = _pinned_unicodedata()

# --------------------------------------------------------------------------- #
# Constants (SPEC.md sections 3, 6)
# --------------------------------------------------------------------------- #

PACK_VERSION = 1
# Domain prefix for the Tier-1 logical id. NOTE: this is the idmodel domain, NOT canon's
# "spec-canon:" prefix — the two domains are deliberately separate (SPEC.md section 3).
_LOGICAL_DOMAIN_PREFIX = b"idmodel:v1:logical\n"

# Field presence tags (the explicit null sentinel — CHIE discipline).
_TAG_NULL = 0x00
_TAG_PRESENT = 0x01

# The lifecycle enum for correspondence entries (SPEC.md section 6 — verbatim contract set).
LIFECYCLE = (
    "active",
    "moved",
    "renamed",
    "split",
    "merged",
    "superseded",
    "ambiguous",
    "deleted",
)

# Mapping-origin enum for correspondence entries (SPEC.md section 6).
MAPPING_ORIGIN = ("extracted", "inferred", "manual")


class IdModelError(ValueError):
    """Raised for any malformed entity descriptor or signature (SPEC.md sections 3/4)."""


def _nfc(s: str) -> str:
    """NFC under the pinned Unicode version (see PINNED_UNICODE). Fails closed for non-ASCII input
    when no DB of that version is available."""
    if s.isascii():
        return s
    if _UNICODE_DB is None:
        raise IdModelError(
            "a non-ASCII identity string needs the pinned Unicode %s database for NFC (install "
            "unicodedata2==16.0.0); this interpreter's stdlib has Unicode %s, which could give the "
            "string a different logical_id than other hosts"
            % (PINNED_UNICODE, _stdlib_unicodedata.unidata_version))
    return _UNICODE_DB.normalize("NFC", s)


# --------------------------------------------------------------------------- #
# CHIE-discipline byte buffer primitives (SPEC.md section 3) — NO string concatenation.
# --------------------------------------------------------------------------- #

def _nfc_utf8(s: str) -> bytes:
    """NFC-normalize (pinned Unicode DB) then encode UTF-8, rejecting lone surrogates.

    Rejecting unpaired surrogates here mirrors canon SPEC.md section 5: Python's UTF-8 encoder
    raises on a lone surrogate while JS emits U+FFFD — a silent cross-language byte divergence.
    """
    s = _nfc(s)
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise IdModelError("unpaired surrogate U+%04X is not allowed in an identity field" % cp)
    return s.encode("utf-8")


def _u8(buf: bytearray, n: int) -> None:
    # One unsigned byte (presence tags, the version byte). 0..255.
    if not (0 <= n <= 0xFF):
        raise IdModelError("u8 out of range: %d" % n)
    buf.append(n)


def _u32be(buf: bytearray, n: int) -> None:
    # Fixed-width 4-byte big-endian unsigned int (lengths, counts). Endianness pinned big-endian.
    if not (0 <= n <= 0xFFFFFFFF):
        raise IdModelError("u32 out of range: %d" % n)
    buf.extend(struct.pack(">I", n))


def _pack_string(buf: bytearray, s: Optional[str]) -> None:
    """Pack an optional string with an explicit null sentinel and a UTF-8 BYTE-length prefix.

    Present:  0x01 || u32be(len(utf8_bytes)) || utf8_bytes   (length is BYTE length, NOT .length)
    Null:     0x00
    So a present-but-empty string (0x01 || 0x00000000) packs DIFFERENTLY from a null field (0x00).
    """
    if s is None:
        _u8(buf, _TAG_NULL)
        return
    if not isinstance(s, str):
        raise IdModelError("expected a string or None, got %s" % type(s).__name__)
    body = _nfc_utf8(s)
    _u8(buf, _TAG_PRESENT)
    _u32be(buf, len(body))  # BYTE length after NFC+UTF-8 — never the UTF-16/code-point count.
    buf.extend(body)


# --------------------------------------------------------------------------- #
# Signature normalization (SPEC.md section 4) — the pinned open detail.
# --------------------------------------------------------------------------- #
#
# Operate on a STRUCTURED signature, never a parsed signature STRING (string-parsing in two
# languages is a fresh divergence surface). Input shape:
#     {"params": [{"name"?: str, "type": str|null}, ...], "return_type": str|null}
# or None (no signature, e.g. a field/variable/anonymous symbol).
#
# The pinned rule:
#   - parameter NAMES are DROPPED (a rename of a param must not change the id).
#   - parameter TYPES are canonicalized (NFC + outer-whitespace trimmed + internal runs collapsed);
#     a null/absent type is kept as a distinct null (untyped != typed-empty).
#   - ARITY is preserved (the number of params is part of the key).
#   - parameter ORDER is preserved (never sorted).
#   - the RETURN type is included (canonicalized the same way; null kept distinct).
# Document mirror: SPEC.md section 4.

# Whitespace class for type canonicalization, PINNED EXPLICITLY (SPEC.md section 4). We must NOT use
# each language's built-in whitespace notion: Python str.split() / \s and JS \s are DIFFERENT sets
# (they disagree on NEL U+0085, U+001C-001F, U+FEFF, etc.) — using them would reopen the exact
# cross-language divergence canon exists to prevent. Only this fixed ASCII set is collapsed; every
# other codepoint (including U+0085/U+FEFF and all non-ASCII whitespace) is treated as ordinary
# content and left intact. This regex is byte-for-byte mirrored in ts/idmodel.ts.
_TYPE_WS = re.compile(r"[ \t\n\r\f\v]+")


def _canon_type(t: Optional[str]) -> Optional[str]:
    """Canonicalize a type string: NFC, then collapse runs of the PINNED ASCII whitespace set to one
    space and strip leading/trailing pinned whitespace.

    NFC is applied here too (so types compare after normalization); the actual UTF-8/NFC happens
    again at pack time, which is idempotent. Returns None unchanged (untyped is distinct).
    """
    if t is None:
        return None
    if not isinstance(t, str):
        raise IdModelError("type must be a string or null, got %s" % type(t).__name__)
    t = _nfc(t)
    # Collapse runs of the pinned ASCII whitespace set to one space, then strip the SAME set off ends.
    return _TYPE_WS.sub(" ", t).strip(" \t\n\r\f\v")


def normalize_signature(sig: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Apply the pinned normalization rule to a structured signature (or None)."""
    if sig is None:
        return None
    if not isinstance(sig, dict):
        raise IdModelError("signature must be an object or null, got %s" % type(sig).__name__)
    raw_params = sig.get("params")
    if raw_params is None:  # absent OR explicit null -> empty (mirrors TS `sig.params ?? []`).
        raw_params = []
    if not isinstance(raw_params, list):
        raise IdModelError("signature.params must be a list or null")
    params: List[Dict[str, Any]] = []
    for p in raw_params:
        if not isinstance(p, dict):
            raise IdModelError("each param must be an object")
        # name DROPPED; only the canonicalized type is kept (null preserved as distinct).
        params.append({"type": _canon_type(p.get("type"))})
    return {"params": params, "return_type": _canon_type(sig.get("return_type"))}


def _pack_signature(buf: bytearray, sig: Optional[Dict[str, Any]]) -> None:
    """Pack a normalized signature with the CHIE discipline (null sentinel + counts + ordering).

    Null signature: 0x00.
    Present:        0x01 || u32be(param_count) || (per param: _pack_string(type)) ||
                   _pack_string(return_type)
    Parameter NAMES are never packed (dropped by normalization); arity is the explicit u32 count;
    order is the buffer append order (never sorted).
    """
    norm = normalize_signature(sig)
    if norm is None:
        _u8(buf, _TAG_NULL)
        return
    _u8(buf, _TAG_PRESENT)
    params = norm["params"]
    _u32be(buf, len(params))  # ARITY, explicit.
    for p in params:
        _pack_string(buf, p["type"])  # type only (name dropped); null type stays distinct.
    _pack_string(buf, norm["return_type"])


# --------------------------------------------------------------------------- #
# Tier-1 logical pack + id (SPEC.md section 3)
# --------------------------------------------------------------------------- #
#
# Layout (versioned):
#   u8(PACK_VERSION)
#   _pack_string(file_role)
#   _pack_string(qualified_symbol)
#   _pack_string(kind)
#   _pack_signature(normalized_signature)
#
# The FOUR keyed fields are exactly the logical key. `body`/provenance is NOT a field (that is why
# a body-only change keeps the id stable). `file` (raw path) is NOT a field (it is provenance);
# `file_role` is a coarse role label (e.g. "src" | "test"), distinct from the raw path so a move
# within the same role does not double-change the key.

_ENTITY_FIELDS = ("file_role", "qualified_symbol", "kind", "normalized_signature")


def logical_pack(entity: Dict[str, Any]) -> bytes:
    """Build the CHIE typed binary pack for an entity descriptor (SPEC.md section 3)."""
    if not isinstance(entity, dict):
        raise IdModelError("entity must be an object")
    buf = bytearray()
    _u8(buf, PACK_VERSION)
    _pack_string(buf, entity.get("file_role"))
    _pack_string(buf, entity.get("qualified_symbol"))
    _pack_string(buf, entity.get("kind"))
    _pack_signature(buf, entity.get("normalized_signature"))
    return bytes(buf)


def logical_id(entity: Dict[str, Any]) -> str:
    """Lowercase-hex SHA-256 over ``idmodel:v1:logical\\n`` || logical_pack(entity)."""
    return hashlib.sha256(_LOGICAL_DOMAIN_PREFIX + logical_pack(entity)).hexdigest()


# --------------------------------------------------------------------------- #
# The two named ids of a spec-graph node (SPEC.md section 3.1)
# --------------------------------------------------------------------------- #
#
# A spec-graph node has exactly two Tier-1 ids, built from the same pack over different descriptors.
# They are NOT interchangeable; every caller names the one it means.
#
#   spec_revision_logical_id(node)  file_role = the node's `level`; the signature counts only for
#                                   `operation`/`interface`. This is the `logical_id` spec-graph's
#                                   Normalize writes onto each node, so it feeds the graph digest and
#                                   specdiff's per-revision identity. Frozen: changing it moves every
#                                   graph digest.
#   engine_join_id(node)            file_role = null (a spec node is abstract: no file); the
#                                   signature counts for `operation`/`interface` only, the kinds
#                                   Normalize keeps it on, so a raw node and its normalized form get
#                                   the same key. This is the key the engine's projection grades a
#                                   desired node under, the verdict overlays join on, and the NL
#                                   intake/coverage path derives.

_SIGNATURE_KINDS = ("operation", "interface")


def spec_revision_logical_id(node: Dict[str, Any]) -> str:
    """A spec node's per-revision logical id (file_role = level). spec-graph's `node_logical_id`."""
    if not isinstance(node, dict):
        raise IdModelError("spec node must be an object")
    sig = node.get("signature") if node.get("kind") in _SIGNATURE_KINDS else None
    return logical_id({
        "file_role": node.get("level"),
        "qualified_symbol": node.get("name"),
        "kind": node.get("kind"),
        "normalized_signature": normalize_signature(sig),
    })


def engine_join_id(node: Dict[str, Any]) -> str:
    """A spec node's engine/overlay join key (file_role = null; the signature of an `operation`/
    `interface`). Other kinds' signatures are ignored, exactly as Normalize drops them: otherwise a
    raw signed component and its normalized form (which the renderer joins on) would get two keys."""
    if not isinstance(node, dict):
        raise IdModelError("spec node must be an object")
    sig = node.get("signature") if node.get("kind") in _SIGNATURE_KINDS else None
    return logical_id({
        "file_role": None,
        "qualified_symbol": node.get("name"),
        "kind": node.get("kind"),
        "normalized_signature": normalize_signature(sig),
    })


# --------------------------------------------------------------------------- #
# Tier-2 occurrence / evidence record (SPEC.md section 5)
# --------------------------------------------------------------------------- #
#
# A thin PROVENANCE wrapper around the existing per-revision hashes (greenfield boundary digest,
# chunker definition_id/node_id, etc). These are NEVER used as cross-revision identity. The
# occurrence_id is content-addressed via canon (semantic-content profile) over the provenance, so
# it is itself deterministic per revision but is explicitly demoted to evidence.

def occurrence_record(
    *,
    revision: str,
    file: str,
    span: Dict[str, Any],
    provenance: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a Tier-2 occurrence record. `provenance` carries upstream per-revision hashes verbatim.

    Returns a dict carrying an ``occurrence_id`` = canon semantic-content digest of the record's
    content (revision/file/span/provenance). occurrence_ids are evidence, never logical identity.
    """
    content = {
        "revision": revision,
        "file": file,
        "span": span,
        "provenance": provenance,
    }
    occ_id = canon.digest(content, "semantic-content")
    return {"occurrence_id": occ_id, **content}


# --------------------------------------------------------------------------- #
# Correspondence-entry validation helper (SPEC.md section 6) — schema is the JSON Schema file.
# --------------------------------------------------------------------------- #

# correspondence/schema.json `confidence`: a string decimal in [0, 1], or a non-negative scaled
# integer. Integers are additionally bounded by 2**53 - 1 so both ports accept the same set (a JS
# number is exact only up to there). A JSON number written with a fraction part but an integral
# value (`1.0`, `1e0`) parses to a float here and to the integer 1 in JS, which cannot tell them
# apart; it is accepted as that integer so both ports agree on the same JSON text. A non-integral
# float is rejected. Mirrored in ts/idmodel.ts.
_CONFIDENCE_DECIMAL = re.compile(r"(?:0(?:\.[0-9]+)?|1(?:\.0+)?)")
MAX_SCALED_CONFIDENCE = 2 ** 53 - 1


def _validate_confidence(conf: Any) -> None:
    # bool first: True/False are ints in Python, and the TS port rejects them.
    if isinstance(conf, bool):
        raise IdModelError("confidence must be a string decimal or scaled int, not a boolean")
    if isinstance(conf, float):
        if not conf.is_integer():
            raise IdModelError("confidence must be a string decimal or scaled int, not a float")
        conf = int(conf)
    if isinstance(conf, str):
        if _CONFIDENCE_DECIMAL.fullmatch(conf) is None:
            raise IdModelError("confidence string must be a decimal in [0,1] such as \"0.92\" or "
                               "\"1.0\" (got %r)" % conf)
        return
    if isinstance(conf, int):
        if not 0 <= conf <= MAX_SCALED_CONFIDENCE:
            raise IdModelError("scaled-int confidence must be in [0, 2**53-1] (got %d)" % conf)
        return
    raise IdModelError("confidence must be a string decimal or scaled int")


def validate_correspondence_entry(entry: Dict[str, Any]) -> None:
    """Lightweight structural check that an entry honors the lifecycle/origin enums + confidence.

    confidence is a STRING decimal in [0,1] or a non-negative scaled int — never a non-integral
    float (canon forbids floats) and never a boolean.
    """
    if entry.get("lifecycle") not in LIFECYCLE:
        raise IdModelError("invalid lifecycle: %r" % entry.get("lifecycle"))
    if entry.get("mapping_origin") not in MAPPING_ORIGIN:
        raise IdModelError("invalid mapping_origin: %r" % entry.get("mapping_origin"))
    _validate_confidence(entry.get("confidence"))
    if not isinstance(entry.get("manual_override", False), bool):
        raise IdModelError("manual_override must be a boolean")
