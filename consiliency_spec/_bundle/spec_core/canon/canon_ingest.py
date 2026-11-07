"""canon v2 — ingestion-boundary Unicode NFC normalizer (SPEC.md section 5).

canon v2 removed mandatory Unicode NFC from the hash (``canonical_bytes`` no longer normalizes), so
``canon`` is now Unicode-DB-independent. The normalization that canon v1 performed in-hash RELOCATES
here, to the ingestion boundary: external strings (authored-graph identifiers/names/prose, realized
code identifiers) are NFC-normalized ONCE as they enter the system, before they ever reach canon. By
the time canon serializes them they are already NFC, so canon's output is byte-identical to v1's for
already-NFC content (the entire committed corpus) while no longer depending on a pinned Unicode DB.

The pin did not disappear — it MOVED. NFC is still defined against a Unicode version, and the Python
and TypeScript ports must agree, so the ``unicodedata2`` backport pin (Node ICU 16.0) and the
fail-closed version assertion live HERE now (the stdlib DB stands in only where the backport is not
installed and the stdlib already reports the pinned version, as on CPython 3.14). Removing the pin
from canon while forgetting to apply NFC at ingest would silently reintroduce cross-language NFC
skew; this module is where ingest paths get it. See SPEC.md section 5 and canon/py/requirements.txt.

Public API:
    normalize_string(s) -> str                       # NFC a single external string
    normalize_keyed(mapping) -> dict                  # NFC keys (+ collision detection) and values
    normalize_tree(value) -> value                    # NFC every string/key in a nested structure
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

# --------------------------------------------------------------------------- #
# Unicode database pin (SPEC.md section 5) — RELOCATED from canon to the ingest boundary.
#
# NFC is defined against a Unicode version. Stdlib ``unicodedata`` is bound to whatever Unicode DB
# the host CPython was built with (3.12 ships 15.0.0, 3.14 ships 16.0.0, later builds will differ),
# so the host DB alone cannot guarantee the SAME NFC as the TS port (Node ICU). The database is
# resolved in this order and asserted fail-closed below, so a wrong build can NEVER silently diverge:
#   - the maintained PyPI backport ``unicodedata2`` when it is installed (pinned exactly to Node's
#     Unicode version, 16.0; see canon/py/requirements.txt). An installed backport at any other
#     version is a misconfiguration and refuses to load; it is never skipped in favour of the stdlib.
#   - only when the backport is NOT installed: the stdlib DB, and only if it already reports the
#     pinned version. The packages leave the backport out on CPython 3.14 (whose stdlib is Unicode
#     16.0.0) through the marker ``python_version != '3.14' or platform_python_implementation !=
#     'CPython'``; canon/conformance/test_unicode_skew.py proves the two 16.0 databases agree on
#     every code point.
# --------------------------------------------------------------------------- #
EXPECTED_UNICODE = "16.0"  # major.minor; matches Node's process.versions.unicode


def _unicode_major_minor(version: str) -> str:
    """Reduce a Unicode version string (e.g. '16.0.0') to 'major.minor' (e.g. '16.0')."""
    return ".".join(version.split(".")[:2])


try:
    import unicodedata2 as unicodedata  # pinned Unicode 16.0 DB (see requirements.txt)
    UNICODE_DB_SOURCE = "unicodedata2"
except ModuleNotFoundError as exc:
    if exc.name != "unicodedata2":  # the backport is present but broken: never mask that
        raise
    import unicodedata as _stdlib_unicodedata

    if _unicode_major_minor(_stdlib_unicodedata.unidata_version) != EXPECTED_UNICODE:
        raise ImportError(
            "canon's ingest boundary needs a Unicode %s database for a deterministic NFC: install the "
            "pinned 'unicodedata2' backport (canon/py/requirements.txt: unicodedata2==16.0.0). This "
            "interpreter's stdlib 'unicodedata' reports %s and would NOT match the TypeScript port "
            "byte-for-byte." % (EXPECTED_UNICODE, _stdlib_unicodedata.unidata_version)
        ) from exc
    unicodedata = _stdlib_unicodedata
    UNICODE_DB_SOURCE = "stdlib unicodedata"


# Fail-closed version assertion (SPEC.md section 5). A mismatch here means the NFC DB in use is NOT
# the pinned one, which is a determinism hole for a parity engine — so we refuse to load rather than
# normalize identifiers with a divergent DB. unicodedata2 reports e.g. '16.0.0'; Node reports '16.0'.
_ACTUAL_UNICODE = _unicode_major_minor(unicodedata.unidata_version)
if _ACTUAL_UNICODE != EXPECTED_UNICODE:
    raise RuntimeError(
        "canon ingest Unicode DB mismatch: expected %s, got %s (from %s %s). NFC "
        "determinism across the Python and TypeScript ingest paths is not guaranteed; refusing to "
        "load." % (EXPECTED_UNICODE, _ACTUAL_UNICODE, UNICODE_DB_SOURCE, unicodedata.unidata_version)
    )


class IngestError(ValueError):
    """Raised for ingest-time normalization failures (e.g. a key collision after NFC)."""


# The canon nesting-depth cap (SPEC.md section 1; MUST equal canon.MAX_DEPTH, asserted by
# canon/conformance/test_ingest_nfc.py). normalize_tree recurses BEFORE canon sees the value, so
# without its own cap a deep tree raised RecursionError here (near depth 500) instead of a typed
# error (CAN-12). A container's depth is 1 + the containers enclosing it.
MAX_DEPTH = 128
_DEPTH_MESSAGE = "nesting depth exceeds the maximum of %d" % MAX_DEPTH


def normalize_string(s: str) -> str:
    """NFC-normalize a single external string under the pinned Unicode DB. Idempotent."""
    return unicodedata.normalize("NFC", s)


def normalize_keyed(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    """NFC-normalize a mapping's string keys (detecting post-NFC collisions) and recurse into values.

    The key-collision-after-NFC check moved here from canon v1's ``_encode_object``: canon v2 sorts
    already-NFC keys by code point as-is and no longer normalizes, so the collision must be caught at
    ingest where the keys are first normalized. ``mapping`` itself is depth 1.
    """
    return _normalize_keyed(mapping, 1)


def normalize_tree(value: Any) -> Any:
    """Recursively NFC-normalize every string value and object key in a nested structure.

    Non-string scalars pass through unchanged. Lists preserve order (canon never reorders arrays).
    A container nested deeper than MAX_DEPTH raises IngestError.
    """
    return _normalize(value, 0)


def _normalize_keyed(mapping: Mapping[str, Any], depth: int) -> Dict[str, Any]:
    # ``depth`` is the mapping's own nesting depth.
    if depth > MAX_DEPTH:
        raise IngestError(_DEPTH_MESSAGE)
    out: Dict[str, Any] = {}
    for k, v in mapping.items():
        if not isinstance(k, str):
            raise IngestError("object keys must be strings, got %r" % type(k).__name__)
        nk = normalize_string(k)
        if nk in out:
            raise IngestError("key collision after NFC normalization: %r" % nk)
        out[nk] = _normalize(v, depth)
    return out


def _normalize(value: Any, depth: int) -> Any:
    # ``depth`` is the number of containers enclosing ``value``.
    if isinstance(value, str):
        return normalize_string(value)
    if isinstance(value, dict):
        return _normalize_keyed(value, depth + 1)
    if isinstance(value, (list, tuple)):
        if depth + 1 > MAX_DEPTH:
            raise IngestError(_DEPTH_MESSAGE)
        out = []
        for v in value:
            out.append(_normalize(v, depth + 1))
        return out
    return value
