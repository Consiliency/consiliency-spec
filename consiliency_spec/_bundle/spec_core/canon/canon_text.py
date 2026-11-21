"""canon's strict JSON-text decode: the ONE parser for certificate and payload text read back.

Parses JSON text with canon's number grammar (``canon._parse_bare_int`` / ``_parse_bare_float`` /
``_reject_constant``: a fraction or exponent spelling and bare ``-0`` are floats and are refused, as
is an integer beyond +/-(2^53 - 1) and ``NaN`` / ``Infinity``, at every number token in the text,
including one a later duplicate key would overwrite), refuses a duplicated object key, and holds the
result to canon's structural and numeric domain: string keys, no floats, integers within
+/-(2^53 - 1), at most ``canon.MAX_DEPTH`` nesting levels. String content is not checked here: a lone
surrogate is refused where canon encodes the value (a digest preimage), not at decode.

Two readers share it, so they cannot disagree on the same bytes:

  * the public reference verifier, ``consiliency_spec.certificate_verifier``, runs the manifest-pinned
    byte copy ``scripts/sync_bundles.py`` writes to ``consiliency_spec/_bundle/spec_core/canon/``;
  * ``spec_core.engine.delivered_verify`` reads a delivered certificate and portal payload with it.

Every refusal is a ``StrictTextError`` whose message names the reason. Standard library only, and
the one import is relative, so the bundled copy binds the bundled canon.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Tuple, Union

from . import canon


class StrictTextError(ValueError):
    """JSON text, or a parsed value, outside canon's strict domain."""


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StrictTextError("duplicate object key %r" % key)
        out[key] = value
    return out


def check_domain(value: Any, depth: int = 0) -> Any:
    """Copy a parsed value into canon's domain or raise StrictTextError: string keys only, no floats,
    integers within +/-(2^53 - 1), at most canon's 128 nesting levels."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if not -canon._MAX_BARE_INT <= value <= canon._MAX_BARE_INT:
            raise StrictTextError("integer beyond +/-(2^53 - 1)")
        return value
    if isinstance(value, float):
        raise StrictTextError("floats are forbidden in canonical content")
    if isinstance(value, Mapping):
        if depth >= canon.MAX_DEPTH:
            raise StrictTextError(canon._DEPTH_MESSAGE)
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrictTextError("object keys must be strings")
            out[key] = check_domain(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if depth >= canon.MAX_DEPTH:
            raise StrictTextError(canon._DEPTH_MESSAGE)
        return [check_domain(item, depth + 1) for item in value]
    raise StrictTextError("unsupported value type %s" % type(value).__name__)


#: The json.loads keyword arguments of the strict parse: canon's number hooks, no duplicate key.
LOADS_HOOKS = {
    "parse_int": canon._parse_bare_int,
    "parse_float": canon._parse_bare_float,
    "parse_constant": canon._reject_constant,
    "object_pairs_hook": _no_duplicate_keys,
}
#: The refusal messages parse() names (with canon's own CanonError messages, and canon's depth
#: message for text nested past the parser's recursion limit).
NOT_UTF8 = "%s is not valid UTF-8"
INVALID_JSON = "invalid JSON: %s"
DEPTH_MESSAGE = canon._DEPTH_MESSAGE


def parse(data: Union[str, bytes, bytearray], what: str) -> Any:
    """Parse JSON `data` (a str, or UTF-8 bytes) under canon's number grammar with no duplicate key,
    WITHOUT the domain check. `what` names the document in the not-UTF-8 message. Raises TypeError
    for any other argument type.

    The public verifier's ``_decode`` spells these steps out in its own frame, with LOADS_HOOKS and
    the messages above, then calls check_domain itself, so json.loads and check_domain run at the
    stack depth they always had there (calling this function would add a frame under json.loads,
    and calling loads() one under check_domain too); tests pin the two to the same verdicts and
    reasons on identical bytes."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            raise StrictTextError(NOT_UTF8 % what) from None
    if not isinstance(data, str):
        raise TypeError("%s must be JSON text or UTF-8 bytes, got %s" % (what, type(data).__name__))
    try:
        return json.loads(data, **LOADS_HOOKS)
    except StrictTextError:
        raise
    except canon.CanonError as e:
        raise StrictTextError(str(e)) from None
    except RecursionError:
        raise StrictTextError(DEPTH_MESSAGE) from None
    except json.JSONDecodeError as e:
        raise StrictTextError(INVALID_JSON % e.msg) from None


def loads(data: Union[str, bytes, bytearray], what: str) -> Any:
    """parse() then check_domain(): JSON `data` strictly parsed and held to canon's domain."""
    return check_domain(parse(data, what))
