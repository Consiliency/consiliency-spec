"""canon v2 — Python reference implementation.

Normative contract: ../SPEC.md. This is a clean-room custom canonical encoder; it does NOT use
``json.dumps`` for canonical output (stdlib JSON escaping / number / key-order behavior is not
guaranteed identical to other languages). See SPEC.md sections referenced inline.

canon v2 (vs v1): Unicode NFC is NO LONGER applied inside ``canonical_bytes``. Callers deliver
already-NFC content (NFC happens once at the ingestion boundary — see canon/py/canon_ingest.py), so canon
is now Unicode-DB-INDEPENDENT and no longer pins ``unicodedata2``. The digest domain prefix is
``spec-canon:v2:``, so a v2 digest can never collide with a v1 digest of the same input (domain
separation, SPEC.md section 8). All other rules are unchanged: key code-point sort, integers-only,
lone-surrogate rejection, minimal escaping, top-level ``digest``-key exclusion, the four profiles.

Public API (SPEC.md section 9):
    canonical_bytes(value) -> bytes
    digest(value, profile) -> str   (lowercase hex)

Helpers:
    split_record(record, content_keys) -> (content, envelope)   (SPEC.md section 10; reference-only)
    decode_input(tagged) -> native value                        (SPEC.md section 2; test harness)
    decode_input_json(text) -> native value                     (SPEC.md section 2; tagged JSON TEXT)

A Python ``tuple`` encodes exactly like a ``list`` (SPEC.md section 1): it is Python's native
immutable array, not a distinct canonical type, and no other port can produce one.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Tuple

# canon v2 performs NO Unicode NFC and depends on NO Unicode DB version (SPEC.md section 5). NFC is
# applied at the ingestion boundary (canon/py/canon_ingest.py), which owns the relocated ``unicodedata2``
# pin and the fail-closed version assertion. canon v2 only requires that keys are already NFC so its
# code-point sort is stable; it does not normalize them itself.

# SPEC.md section 8 — the four digest profiles. ``locator`` is intentionally NOT a profile.
PROFILES = ("semantic-content", "run", "artifact-byte", "certificate")
_DOMAIN_PREFIX = "spec-canon:v2:"

# SPEC.md section 1 — maximum nesting depth (CAN-12). A container's depth is
# 1 + the number of containers enclosing it, so ``[]`` is depth 1 and ``[[]]`` is depth 2. A value
# that holds any container deeper than MAX_DEPTH is rejected with CanonError, identically in every
# port, at decode and at encode. Without the cap the ports failed differently and non-deterministically
# (Python RecursionError near 250, TS RangeError between 2,000 and 5,000, Rust SIGABRT), and the Rust
# JSON entry points rejected anything past serde_json's own 127-level text limit.
MAX_DEPTH = 128
_DEPTH_MESSAGE = "nesting depth exceeds the maximum of %d" % MAX_DEPTH

# SPEC.md section 2 — a BARE JSON number in a tagged input decodes to an integer only inside
# +/-(2^53 - 1), the range every port's JSON parser holds exactly (D6). Beyond it the TypeScript
# JSON.parse silently rounds (9007199254740993 -> ...992) and Rust's serde_json falls back to f64,
# so the same JSON text produced different bytes per port. Larger integers must use a $int tag.
_MAX_BARE_INT = 2 ** 53 - 1
_BARE_INT_MESSAGE = "bare JSON integer beyond +/-(2^53 - 1): use a $int tag"


class CanonError(ValueError):
    """Raised for any value outside the supported canonical domain (SPEC.md section 1/6)."""


# --------------------------------------------------------------------------- #
# String encoding (SPEC.md section 5)
# --------------------------------------------------------------------------- #

def _encode_string(s: str) -> str:
    # canon v2 does NOT normalize: callers deliver already-NFC content (see canon/py/canon_ingest.py).
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            # Unpaired surrogate: not valid scalar text. Python would raise on UTF-8 encode while
            # JS emits U+FFFD -> silent byte-divergence. Reject in BOTH (SPEC.md section 5).
            raise CanonError("unpaired surrogate U+%04X is not allowed in canonical content" % cp)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif cp <= 0x1F:
            # Control chars: \uXXXX lowercase hex. No short escapes (no \n, \t, ...).
            out.append("\\u%04x" % cp)
        else:
            # Everything else, incl. all non-ASCII and U+007F, is raw (ensure_ascii=False).
            out.append(ch)
    out.append('"')
    return "".join(out)


# --------------------------------------------------------------------------- #
# Number encoding (SPEC.md section 6) — integers only.
# --------------------------------------------------------------------------- #

def _encode_int(value: int) -> str:
    # bool is handled earlier; here value is a genuine int. str(int) is exactly the canonical
    # shortest decimal form: optional '-', no leading zeros (except '0'), no '+', no exponent.
    return str(value)


# --------------------------------------------------------------------------- #
# Object encoding (SPEC.md sections 3, 7) — keys sorted by code point.
# --------------------------------------------------------------------------- #

def _encode_object(obj: dict, depth: int) -> str:
    # canon v2 does NOT NFC-normalize keys (the ingest boundary did that, and it also detected
    # post-NFC collisions — see canon/py/canon_ingest.py.normalize_keyed). canon sorts the already-NFC keys
    # by Unicode code point as-is. Python str comparison is already by code point (correct for astral
    # chars). ``depth`` is this object's own nesting depth (already checked by the caller).
    for k in obj:
        if not isinstance(k, str):
            raise CanonError("object keys must be strings, got %r" % type(k).__name__)
    parts = []
    for k in sorted(obj.keys()):
        parts.append(_encode_string(k) + ":" + _encode_value(obj[k], depth))
    return "{" + ",".join(parts) + "}"


def _encode_array(arr: Iterable, depth: int) -> str:
    # Insertion order preserved ALWAYS (SPEC.md section 4). Never sort. An explicit loop, not a
    # generator inside join(): it keeps the Python and C frame cost per nesting level low, so the
    # MAX_DEPTH cap (not the interpreter's recursion limit) is what a deep value hits.
    parts = []
    for v in arr:
        parts.append(_encode_value(v, depth))
    return "[" + ",".join(parts) + "]"


# --------------------------------------------------------------------------- #
# Value dispatch — bool BEFORE int (SPEC.md section 6 boolean trap).
# --------------------------------------------------------------------------- #

def _encode_value(value: Any, depth: int = 0) -> str:
    # ``depth`` is the number of containers enclosing ``value`` (0 at the top level).
    # Reject markers from the type-tagged decoder (SPEC.md section 2/6).
    if isinstance(value, FloatMarker):
        raise CanonError("floats are forbidden in canonical content; pre-represent as int or string")
    if isinstance(value, NanMarker):
        raise CanonError("NaN is forbidden in canonical content")
    if isinstance(value, InfMarker):
        raise CanonError("Infinity is forbidden in canonical content")
    if value is None:
        return "null"
    if isinstance(value, bool):  # MUST precede int: isinstance(True, int) is True.
        return "true" if value else "false"
    if isinstance(value, int):
        return _encode_int(value)
    if isinstance(value, float):
        raise CanonError("floats are forbidden in canonical content; pre-represent as int or string")
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            raise CanonError(_DEPTH_MESSAGE)
        return _encode_object(value, depth + 1)
    if isinstance(value, (list, tuple)):  # a tuple is Python's native array alias (SPEC.md section 1)
        if depth >= MAX_DEPTH:
            raise CanonError(_DEPTH_MESSAGE)
        return _encode_array(value, depth + 1)
    raise CanonError("unsupported type for canonical content: %s" % type(value).__name__)


# --------------------------------------------------------------------------- #
# Public API (SPEC.md sections 8, 9)
# --------------------------------------------------------------------------- #

def canonical_bytes(value: Any) -> bytes:
    """Deterministic canonical UTF-8 bytes for a value in the supported domain."""
    return _encode_value(value).encode("utf-8")


def _strip_top_level_digest(value: Any) -> Any:
    # SPEC.md section 8: exclude a top-level ``digest`` key only (nested is ordinary content).
    if isinstance(value, dict) and "digest" in value:
        return {k: v for k, v in value.items() if k != "digest"}
    return value


def digest(value: Any, profile: str) -> str:
    """Lowercase-hex SHA-256 over domain_prefix(profile) || canonical_bytes(value)."""
    if profile not in PROFILES:
        raise CanonError("unknown digest profile: %r (allowed: %s)" % (profile, ", ".join(PROFILES)))
    prefix = (_DOMAIN_PREFIX + profile + "\n").encode("ascii")
    body = canonical_bytes(_strip_top_level_digest(value))
    return hashlib.sha256(prefix + body).hexdigest()


# --------------------------------------------------------------------------- #
# Content/envelope split (SPEC.md section 10)
# --------------------------------------------------------------------------- #

def split_record(record: dict, content_keys: Iterable[str]) -> Tuple[dict, dict]:
    """Split a record into (content, envelope). Only ``content`` is ever hashed."""
    keyset = set(content_keys)
    content = {k: v for k, v in record.items() if k in keyset}
    envelope = {k: v for k, v in record.items() if k not in keyset}
    return content, envelope


# --------------------------------------------------------------------------- #
# Type-tagged input decoder (SPEC.md section 2) — test harness, identical across languages.
# --------------------------------------------------------------------------- #

class FloatMarker:
    """A value the encoder must reject (stands in for a float literal)."""


class NanMarker:
    pass


class InfMarker:
    """A value the encoder must reject (stands in for +/-Infinity).

    ``sign`` is informational only: the encoder rejects every InfMarker identically and never reads
    it, so it cannot affect any output (SPEC.md section 2).
    """

    def __init__(self, sign: int):
        self.sign = sign


# The one payload grammar every port shares (SPEC.md section 2): optional '-' then ASCII digits.
# `[0-9]`, NOT `\d` — Python's `\d` (and `int()`) accept non-ASCII digits such as Bengali "০১",
# and `int()` also accepts surrounding whitespace, '+' and '_' separators that the other ports do
# not. Leading zeros and "-0" match and normalise ("007" -> 7, "-0" -> 0).
_INT_PAYLOAD = re.compile(r"-?[0-9]+")  # used with fullmatch: `$` would admit a trailing "\n"


def _parse_int_payload(payload: Any) -> int:
    # Fixed message: never interpolate the payload (a rejected value must not leak via the error).
    if not isinstance(payload, str) or not _INT_PAYLOAD.fullmatch(payload):
        raise CanonError("invalid $int payload: expected optional '-' then ASCII digits")
    return int(payload)


def decode_input(node: Any) -> Any:
    """Decode a type-tagged vector input tree into native values (or reject markers).

    Every tag's payload has exactly one accepted JSON type (SPEC.md section 2); anything else is a
    fixed-message CanonError, never a truthiness or iteration coercion. Containers deeper than
    MAX_DEPTH are rejected here as well as in the encoder (SPEC.md section 1).
    """
    return _decode(node, 0)


def _container_depth(depth: int) -> int:
    # ``depth`` counts the containers enclosing the one being built; return the new container's depth.
    if depth >= MAX_DEPTH:
        raise CanonError(_DEPTH_MESSAGE)
    return depth + 1


def _decode(node: Any, depth: int) -> Any:
    if isinstance(node, dict) and len(node) == 1:
        (tag, payload), = node.items()
        if tag == "$int":
            return _parse_int_payload(payload)  # grammar-checked decimal string -> exact int
        if tag == "$float":
            return FloatMarker()
        if tag == "$nan":
            return NanMarker()
        if tag == "$inf":
            # Rejected by the encoder whatever the payload; the sign is informational, and a
            # non-number payload must not turn the rejection into a TypeError.
            negative = isinstance(payload, (int, float)) and not isinstance(payload, bool) and payload < 0
            return InfMarker(-1 if negative else 1)
        if tag == "$str":
            if not isinstance(payload, str):
                raise CanonError("invalid $str payload: expected a JSON string")
            return payload
        if tag == "$bool":
            # Python bool(payload) made [], {} and 0.0 false while Rust and TS made them true
            # (CAN-9). The payload must BE a boolean.
            if not isinstance(payload, bool):
                raise CanonError("invalid $bool payload: expected a JSON boolean")
            return payload
        if tag == "$null":
            if payload is not True:
                raise CanonError("invalid $null payload: expected true")
            return None
        if tag == "$obj":
            if not isinstance(payload, dict):
                raise CanonError("invalid $obj payload: expected a JSON object")
            inner = _container_depth(depth)
            return {k: _decode(v, inner) for k, v in payload.items()}
        if tag == "$arr":
            if not isinstance(payload, list):
                raise CanonError("invalid $arr payload: expected a JSON array")
            inner = _container_depth(depth)
            return [_decode(v, inner) for v in payload]
        # one-key dict that is not a tag: fall through to plain-dict handling
    if isinstance(node, dict):
        inner = _container_depth(depth)
        return {k: _decode(v, inner) for k, v in node.items()}
    if isinstance(node, list):
        inner = _container_depth(depth)
        return [_decode(v, inner) for v in node]
    # bare scalars: str/bool/None pass through; a bare JSON integer is an int only inside the range
    # every port's JSON parser holds exactly (SPEC.md section 2; D6).
    if isinstance(node, bool):
        return node
    if isinstance(node, int):
        if -_MAX_BARE_INT <= node <= _MAX_BARE_INT:
            return node
        raise CanonError(_BARE_INT_MESSAGE)
    if isinstance(node, float):
        # Bare JSON float in a vector input -> a float marker (encoder rejects).
        return FloatMarker()
    return node


_FLOAT_MESSAGE = "floats are forbidden in canonical content; pre-represent as int or string"


def _parse_bare_int(text: str) -> Any:
    # json.loads hands EVERY integer-spelled number token here, in text order, including one that a
    # later duplicate key overwrites, so the check covers every number in the text (SPEC.md section 2).
    if text == "-0":
        # Rust's serde_json reads bare `-0` as the float -0.0, and no port can tell it apart from
        # `-0.0` after parsing. It is a float spelling everywhere.
        raise CanonError(_FLOAT_MESSAGE)
    if len(text.lstrip("-")) > 16:
        # Past +/-(2^53 - 1) by length alone (JSON forbids leading zeros); never call int() on it (a
        # multi-thousand-digit literal would raise ValueError from the int-string-conversion limit).
        raise CanonError(_BARE_INT_MESSAGE)
    value = int(text)
    if not -_MAX_BARE_INT <= value <= _MAX_BARE_INT:
        raise CanonError(_BARE_INT_MESSAGE)
    return value


def _parse_bare_float(_text: str) -> Any:
    # Every fraction or exponent spelling (`1.0`, `1e3`), at any magnitude and even if a later
    # duplicate key overwrites it, is a float: the text is rejected at parse, as in Rust and TypeScript.
    raise CanonError(_FLOAT_MESSAGE)


def _reject_constant(_name: str) -> Any:
    # NaN / Infinity / -Infinity are not JSON; the stdlib accepts them, Rust and JS do not.
    raise CanonError("invalid JSON: NaN and Infinity literals are not JSON")


def decode_input_json(text: str) -> Any:
    """Parse tagged-input JSON TEXT and decode it (SPEC.md section 2), as the Rust and TypeScript
    ``decode_input_json`` / ``decodeInputJson`` entry points do.

    EVERY number token in the text is checked, including one that a later duplicate key overwrites:
    a fraction or exponent spelling (``1.0``, ``1e3``) or bare ``-0`` is rejected as a float, and an
    integer beyond +/-(2^53 - 1) as a bare integer, both at parse. Duplicate keys otherwise keep the
    last member (Rust alone also rejects an overwritten member serde_json cannot parse or that is
    over-deep: SPEC.md section 2). Invalid JSON and text nested past the parser's recursion limit are
    CanonError.
    """
    try:
        node = json.loads(text, parse_int=_parse_bare_int, parse_float=_parse_bare_float,
                          parse_constant=_reject_constant)
    except RecursionError:
        raise CanonError(_DEPTH_MESSAGE) from None
    except json.JSONDecodeError as error:
        raise CanonError("invalid JSON: %s" % error.msg) from None
    return decode_input(node)
