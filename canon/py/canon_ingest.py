"""canon v2 — ingestion-boundary Unicode NFC normalizer (SPEC.md section 5).

canon v2 removed mandatory Unicode NFC from the hash (``canonical_bytes`` no longer normalizes), so
``canon`` is now Unicode-DB-independent. The normalization that canon v1 performed in-hash RELOCATES
here, to the ingestion boundary: external strings (authored-graph identifiers/names/prose, realized
code identifiers) are NFC-normalized ONCE as they enter the system, before they ever reach canon. By
the time canon serializes them they are already NFC, so canon's output is byte-identical to v1's for
already-NFC content (the entire committed corpus) while no longer depending on a pinned Unicode DB.

The pin did not disappear — it MOVED. NFC is still defined against a Unicode version, and the Python
and TypeScript ports must agree, so the ``unicodedata2`` backport pin (Node ICU 16.0) and the
fail-closed version assertion live HERE now. Removing the pin from canon while forgetting to apply
NFC at ingest would silently reintroduce cross-language NFC skew; this module is where ingest paths
get it. See SPEC.md section 5 and canon/py/requirements.txt.

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
# the host CPython was built with (3.10 ships 13.0.0; newer builds differ), so it cannot guarantee
# the SAME NFC as the TS port (Node ICU). We pin the maintained PyPI backport ``unicodedata2``
# exactly to Node's Unicode version (16.0) and use it for ALL ingest NFC. See canon/py/requirements
# .txt for the exact pin; the assertion below is fail-closed so a wrong build can NEVER silently
# diverge.
# --------------------------------------------------------------------------- #
EXPECTED_UNICODE = "16.0"  # major.minor; matches Node's process.versions.unicode

try:
    import unicodedata2 as unicodedata  # pinned Unicode 16.0 DB (see requirements.txt)
except ImportError as exc:  # pragma: no cover - environment misconfiguration
    raise ImportError(
        "canon's ingest boundary requires the pinned 'unicodedata2' backport for a deterministic "
        "Unicode DB (install canon/py/requirements.txt: unicodedata2==16.0.0). Stdlib 'unicodedata' "
        "is bound to the host CPython build and would NOT match the TypeScript port byte-for-byte."
    ) from exc


def _unicode_major_minor(version: str) -> str:
    """Reduce a Unicode version string (e.g. '16.0.0') to 'major.minor' (e.g. '16.0')."""
    return ".".join(version.split(".")[:2])


# Fail-closed version assertion (SPEC.md section 5). A mismatch here means the NFC DB in use is NOT
# the pinned one, which is a determinism hole for a parity engine — so we refuse to load rather than
# normalize identifiers with a divergent DB. unicodedata2 reports e.g. '16.0.0'; Node reports '16.0'.
_ACTUAL_UNICODE = _unicode_major_minor(unicodedata.unidata_version)
if _ACTUAL_UNICODE != EXPECTED_UNICODE:
    raise RuntimeError(
        "canon ingest Unicode DB mismatch: expected %s, got %s (from unicodedata2 %s). NFC "
        "determinism across the Python and TypeScript ingest paths is not guaranteed; refusing to "
        "load." % (EXPECTED_UNICODE, _ACTUAL_UNICODE, unicodedata.unidata_version)
    )


class IngestError(ValueError):
    """Raised for ingest-time normalization failures (e.g. a key collision after NFC)."""


def normalize_string(s: str) -> str:
    """NFC-normalize a single external string under the pinned Unicode DB. Idempotent."""
    return unicodedata.normalize("NFC", s)


def normalize_keyed(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    """NFC-normalize a mapping's string keys (detecting post-NFC collisions) and recurse into values.

    The key-collision-after-NFC check moved here from canon v1's ``_encode_object``: canon v2 sorts
    already-NFC keys by code point as-is and no longer normalizes, so the collision must be caught at
    ingest where the keys are first normalized.
    """
    out: Dict[str, Any] = {}
    for k, v in mapping.items():
        if not isinstance(k, str):
            raise IngestError("object keys must be strings, got %r" % type(k).__name__)
        nk = normalize_string(k)
        if nk in out:
            raise IngestError("key collision after NFC normalization: %r" % nk)
        out[nk] = normalize_tree(v)
    return out


def normalize_tree(value: Any) -> Any:
    """Recursively NFC-normalize every string value and object key in a nested structure.

    Non-string scalars pass through unchanged. Lists preserve order (canon never reorders arrays).
    """
    if isinstance(value, str):
        return normalize_string(value)
    if isinstance(value, dict):
        return normalize_keyed(value)
    if isinstance(value, (list, tuple)):
        return [normalize_tree(v) for v in value]
    return value
