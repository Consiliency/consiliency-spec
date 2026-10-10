"""Reference verifier for spec-parity certificates (SEMANTICS.md section 9).

``verify_certificate()`` re-derives everything a certificate pins that a consumer can recompute,
using only what this package ships: the bundled canon v2 port (``canon/py``), the spec-graph and
idmodel reference modules (``consiliency_spec/_bundle/``, the engine's own ``spec_graph.py`` and
``idmodel.py``) and the public schemas. It never re-grades: it checks that the certificate is
well-formed, consistent with the ``digest`` it carries and with itself, and that the inputs a caller
holds are the inputs it certifies.

What ``valid`` does NOT establish: origin. Anyone can edit a certificate and re-hash it; the result
is a different, self-consistent certificate. The digest is the certificate's identity (SEMANTICS
section 9), so compare ``result.digest`` with a digest obtained from a channel you trust, or resolve
``authority_ref`` against the authority ledger (section 13), which this verifier does not do. What a
re-hash cannot defeat are the bindings to inputs you hold yourself: S', E(C), the finding set at
``findings_ref`` and the payload.

Checks (each is reported, with a reason, in ``CertificateVerification.checks``):

* ``decode``                certificate text parses under canon's strict number rules: a
                            fraction or exponent spelling (``2.0``, ``2e0``) or a bare ``-0`` is a
                            float and is rejected, a bare integer must lie within +/-(2^53 - 1),
                            ``NaN``/``Infinity`` are rejected, and so is a duplicated key.
* ``schema``                the certificate's field set against ``certificate.schema.json``
                            (required fields, no unknown field, ``schema_version`` ``"2"``,
                            ``spec_authority`` / ``ec_reproducible`` / ``authority_ref`` shapes,
                            five dimension results).
* ``json_schema``           full JSON Schema 2020-12 validation (``jsonschema`` is required).
* ``canon_version``         ``"v2"``: a canon-v1 digest is never recomputed under v2.
* ``aggregation``           ``overall_result_state`` is the SEMANTICS section 6.4 aggregation of the
                            certificate's own ``dimension_results`` (and a vacuous certificate is
                            ``not_applicable``); a dimension is ``not_applicable`` exactly when it
                            evaluated no check (section 6.3).
* ``certificate_digest``    canon ``certificate`` profile digest of the certificate (without its
                            top-level ``digest`` and its non-hashed ``locator``) equals ``digest``.
* ``finding_set_id`` / ``findings_ref`` / ``finding_set_copy``  (``finding_set=``) the finding set's
                            id recomputes, equals the certificate's ``findings_ref``, and the
                            certificate's ``overall_result_state`` / ``dimension_results`` are
                            byte-equal copies of the finding set's, and so is ``vacuity`` (the
                            certificate's mirrors the finding set's; both absent is equal).
* ``finding_set_schema``    (``finding_set=``) JSON Schema validation against
                            ``result-state.schema.json`` (the ``finding_set`` record).
* ``finding_set_consistency``  (``finding_set=``) every ``finding_id`` recomputes, each dimension's
                            ``finding_ids`` are exactly its findings, each dimension's state is the
                            section 6.3 rollup of its findings, and the finding set's own
                            ``overall_result_state`` is the section 6.4 aggregation.
* ``ec_digest``             (``ec=``) canon ``semantic-content`` digest of E(C).
* ``desired_graph_valid`` / ``desired_graph_digest`` / ``spec_revision_digest`` / ``spec_authority``
                            (``desired_graph=``) the binding procedure of SEMANTICS section 9 for a
                            candidate S': S' passes the full spec-graph rule set, its normalized
                            ``graph_digest`` and its ``spec_revision_digest`` both equal the
                            certificate's (the binding PAIR), and ``is_authoritative(S')`` agrees
                            with ``spec_authority``.
* ``payload_digest`` / ``payload_schema`` / ``payload_binding``  (``payload=``) the portal payload's
                            digest recomputes, it is valid against ``portal-payload.schema.json``,
                            and every field it copies from the certificate agrees with it. Without
                            a finding set, ``payload_binding`` also checks ``finding_summaries``
                            against what the certificate pins: ids unique and sorted and exactly the
                            union of its dimensions' ``finding_ids``, each summary listed under its
                            own dimension, ``title`` derived from ``code``, the section 6.3 rollup
                            of each dimension's summaries equal to its state, and no summary under a
                            ``not_applicable`` dimension.
* ``payload_projection``    (``payload=`` and ``finding_set=``) the payload, minus its envelope and
                            digest, is byte-equal to the SEMANTICS section 12 projection of
                            (certificate, finding_set), i.e. what the engine's ``deliver()``
                            produces: one summary per finding, its allowlisted location, title and
                            waiver ref. A re-hashed payload that drops, invents or rewrites a
                            summary fails here, and the reason names the first differing key.
* ``authoritative``         (``require_authoritative=True``) the certificate is gating: grounded,
                            ``ec_reproducible`` exactly ``true``, bound to a supplied S', and its
                            ``ec_digest`` matched a supplied E(C) (an authoritative verdict is about
                            code you hold, so E(C) is required, not optional, here).

``valid`` is true iff no check failed. A ``draft`` or ``ec_reproducible: false`` certificate can
be valid: it is then a true record of an advisory or draft run, which ``advisory``,
``spec_authority`` and ``authoritative`` report. Every input is untrusted, and an input fault is a
failed check, never an exception. Only caller misuse raises: a ``TypeError`` for an argument of the
wrong type, and ``VerifierUnavailable`` when the environment cannot run a check: ``jsonschema``
missing (always needed); when ``desired_graph=`` is passed, the pinned Unicode 16.0 database missing
or of another version; or a bundled module, schema or data file whose bytes do not match the package
manifest. ``pip install "consiliency-spec[verify]"`` provides the dependencies. The verdict never
depends on what is installed: the verifier either runs every check it was asked for or raises.

Pass inputs as JSON text or bytes where you can. A parsed ``dict`` cannot show how a number was
spelled, so only text input can reject ``2.0`` that an earlier parser has already turned into ``2``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import types
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

__all__ = [
    "CertificateVerification",
    "VerificationCheck",
    "VerifierUnavailable",
    "verify_certificate",
]

JsonInput = Union[Mapping[str, Any], str, bytes, bytearray]

PASS = "pass"
FAIL = "fail"

CANON_VERSION = "v2"
_MAX_BARE_INT = 2 ** 53 - 1
_MAX_DEPTH = 128
#: Non-hashed envelope keys (canon section 10): never part of a digest preimage.
_ENVELOPE_KEYS = ("locator",)
_INTENT_LEVELS = frozenset({"contract", "architecture"})


class VerifierUnavailable(RuntimeError):
    """The verifier cannot run a requested check in this environment (a missing optional
    dependency, or bundled code whose bytes do not match the package manifest)."""


@dataclass(frozen=True)
class VerificationCheck:
    """One check's outcome: ``status`` is ``"pass"`` or ``"fail"``."""

    name: str
    status: str
    reason: str

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "status": self.status, "reason": self.reason}


@dataclass(frozen=True)
class CertificateVerification:
    """The structured verdict of ``verify_certificate``.

    ``valid``           no check failed.
    ``bound``           a supplied S' passed the four binding checks (``desired_graph_valid``,
                        ``desired_graph_digest``, ``spec_revision_digest``, ``spec_authority``),
                        reported independently of every other check: S' is the graph this record
                        pins, whether or not the record is otherwise valid.
    ``authoritative``   ``valid`` and ``bound``, E(C) supplied and matching ``ec_digest``,
                        ``spec_authority == "grounded"`` and ``ec_reproducible is True``: the
                        certificate may gate (given a digest you trust; see the module docstring).
    ``advisory``        the certificate does not claim a measured-reproducible E(C).
    ``digest`` / ``spec_authority`` / ``ec_reproducible`` / ``overall_result_state`` are read from
    the certificate (``None`` when it could not be decoded) and are only trustworthy when ``valid``.
    ``recomputed`` holds every digest the verifier derived itself.
    """

    valid: bool
    bound: bool
    authoritative: bool
    advisory: bool
    digest: Optional[str]
    spec_authority: Optional[str]
    ec_reproducible: Optional[bool]
    overall_result_state: Optional[str]
    checks: Tuple[VerificationCheck, ...]
    recomputed: Dict[str, str] = field(default_factory=dict)

    def check(self, name: str) -> Optional[VerificationCheck]:
        for item in self.checks:
            if item.name == name:
                return item
        return None

    @property
    def failures(self) -> Tuple[VerificationCheck, ...]:
        return tuple(item for item in self.checks if item.status == FAIL)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "bound": self.bound,
            "authoritative": self.authoritative,
            "advisory": self.advisory,
            "digest": self.digest,
            "spec_authority": self.spec_authority,
            "ec_reproducible": self.ec_reproducible,
            "overall_result_state": self.overall_result_state,
            "checks": [item.to_dict() for item in self.checks],
            "recomputed": dict(self.recomputed),
        }


# --------------------------------------------------------------------------------------------- #
# Loading the bundled reference modules
#
# canon.py, canon_ingest.py (public data files) and idmodel.py, spec_graph.py (consiliency_spec/_bundle)
# are manifest-pinned but not importable modules, and they import one another by bare top-level name. Each is loaded by absolute
# path and injected into sys.modules under its bare name only for the duration of the load, so a
# foreign same-named module in the host process can never stand in for the bundled encoder, and the
# host's sys.modules and sys.path are restored afterwards, all under the process-wide lock below.
# --------------------------------------------------------------------------------------------- #

_CANON_FILES = (("canon", "canon/py/canon.py"),)
_GRAPH_FILES = (
    ("canon_ingest", "canon/py/canon_ingest.py"),
    ("idmodel", "consiliency_spec/_bundle/idmodel/py/idmodel.py"),
    ("spec_graph", "consiliency_spec/_bundle/spec-graph/py/spec_graph.py"),
)
#: The permitted_freedom vocabulary spec_graph.validate reads (a public data file).
_PF_VOCAB = "spec-parity/permitted-freedom-vocab.json"
_MANIFEST_FILE = "consiliency-spec.public-manifest.json"
# The process-wide lock, shared with the consiliency-spec ingest packages' loaders, which save, mutate
# and restore the same bare sys.modules names. It is anchored in the interpreter's builtins namespace
# under a fixed key (a sys.modules wipe cannot drop it), and the legacy sys.modules holder is kept
# pointing at the same lock for a peer that only knows the holder. Same scheme as their loaders.
_NAMESPACE_LOCK_KEY = "consiliency_spec.namespace_lock"
_NAMESPACE_LOCK_HOLDER = "_consiliency_spec_namespace_lock"
_BUILTINS_NAMESPACE = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)


def _shared_namespace_lock() -> Any:
    holder = types.ModuleType(_NAMESPACE_LOCK_HOLDER)
    holder.LOCK = threading.RLock()  # type: ignore[attr-defined]
    legacy = sys.modules.setdefault(_NAMESPACE_LOCK_HOLDER, holder)
    lock = _BUILTINS_NAMESPACE.setdefault(_NAMESPACE_LOCK_KEY, legacy.LOCK)
    legacy.LOCK = lock
    return lock


_LOCK = _shared_namespace_lock()
_LOADED: Dict[str, types.ModuleType] = {}


def _public_root() -> Path:
    """Where the public files live: the source checkout this module sits in (decided once, by the
    manifest beside it), else the installed package's ``_data`` directory."""
    source = Path(__file__).resolve().parent.parent
    if (source / _MANIFEST_FILE).is_file() and (source / "consiliency_spec").is_dir():
        return source
    return Path(str(resources.files(__package__).joinpath("_data")))


def _locate(rel: str) -> Path:
    """A manifest path on disk: package modules sit at their import path (in a checkout and in an
    installed wheel alike), every other public file under the public root."""
    if rel.startswith("consiliency_spec/"):
        return Path(__file__).resolve().parent.parent / rel
    return _public_root() / rel


def _pinned_sha256(root: Path) -> Dict[str, str]:
    manifest = json.loads((root / _MANIFEST_FILE).read_text(encoding="utf-8"))
    return {row["path"]: row["sha256"] for row in manifest["public_files"]}


def _load(files: Tuple[Tuple[str, str], ...]) -> None:
    root = _public_root()
    pinned = _pinned_sha256(root)
    sources = []
    for name, rel in files:
        path = _locate(rel)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != pinned.get(rel):
            raise VerifierUnavailable(
                "bundled %s does not match its manifest sha256; refusing to execute it" % rel)
        sources.append((name, path, data))
    names = [name for name, _ in files] + [name for name in _LOADED]
    saved_modules = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    saved_path = list(sys.path)
    try:
        sys.modules.update(_LOADED)  # already-loaded siblings (canon) bind to the same instance
        fresh: Dict[str, types.ModuleType] = {}
        for name, path, data in sources:
            # Execute exactly the bytes whose sha256 was checked, never a second read of the file.
            module = types.ModuleType(name)
            module.__file__ = str(path)
            sys.modules[name] = module  # before exec, so dependents bind to it
            exec(compile(data, str(path), "exec"), module.__dict__)
            if name == "spec_graph":
                # spec_graph reads the permitted_freedom vocabulary from its own repo layout. Hand it
                # the manifest-checked public copy instead: prime its cache from verified bytes (and
                # point its path at the public file).
                if not (hasattr(module, "_PF_VOCAB_PATH")
                        and hasattr(module, "_PF_VOCAB_TOKENS_CACHE")):  # pragma: no cover
                    raise VerifierUnavailable("spec_graph no longer exposes its vocabulary cache")
                vocab = json.loads(_read_pinned(_PF_VOCAB).decode("utf-8"))
                module._PF_VOCAB_PATH = str(_locate(_PF_VOCAB))
                module._PF_VOCAB_TOKENS_CACHE = frozenset(tok["id"] for tok in vocab["tokens"])
            fresh[name] = module
        _LOADED.update(fresh)
    finally:
        sys.path[:] = saved_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved_modules)


def _canon() -> Any:
    with _LOCK:
        if "canon" not in _LOADED:
            _load(_CANON_FILES)
        return _LOADED["canon"]


def _spec_graph() -> Any:
    with _LOCK:
        if "spec_graph" not in _LOADED:
            _canon()
            try:
                _load(_GRAPH_FILES)
            except VerifierUnavailable:
                raise
            except (ImportError, RuntimeError) as e:
                # ImportError: no unicodedata2. RuntimeError: canon_ingest's fail-closed assertion
                # that the Unicode DB is exactly 16.0.
                raise VerifierUnavailable(
                    "binding a desired graph needs the pinned Unicode 16.0 database for NFC: "
                    'install "consiliency-spec[verify]" (unicodedata2==16.0.0): %s' % e) from e
        return _LOADED["spec_graph"]


# --------------------------------------------------------------------------------------------- #
# Strict decoding: canon's number grammar, no floats, no duplicate keys
# --------------------------------------------------------------------------------------------- #

class _DecodeError(ValueError):
    pass


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DecodeError("duplicate object key %r" % key)
        out[key] = value
    return out


def _check_domain(value: Any, depth: int = 0) -> Any:
    """Copy a parsed value into canon's domain or raise _DecodeError: string keys only, no floats,
    integers within +/-(2^53 - 1), at most canon's 128 nesting levels."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if not -_MAX_BARE_INT <= value <= _MAX_BARE_INT:
            raise _DecodeError("integer beyond +/-(2^53 - 1)")
        return value
    if isinstance(value, float):
        raise _DecodeError("floats are forbidden in canonical content")
    if isinstance(value, Mapping):
        if depth >= _MAX_DEPTH:
            raise _DecodeError("nesting depth exceeds the maximum of %d" % _MAX_DEPTH)
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _DecodeError("object keys must be strings")
            out[key] = _check_domain(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if depth >= _MAX_DEPTH:
            raise _DecodeError("nesting depth exceeds the maximum of %d" % _MAX_DEPTH)
        return [_check_domain(item, depth + 1) for item in value]
    raise _DecodeError("unsupported value type %s" % type(value).__name__)


def _decode(value: JsonInput, what: str) -> Any:
    """Text/bytes: parse strictly with canon's number hooks. Mapping: check it is in canon's domain.
    Raises TypeError for a wrong argument type (misuse) and _DecodeError for bad content."""
    canon = _canon()
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            raise _DecodeError("%s is not valid UTF-8" % what) from None
    if isinstance(value, str):
        try:
            parsed = json.loads(value, parse_int=canon._parse_bare_int,
                                parse_float=canon._parse_bare_float,
                                parse_constant=canon._reject_constant,
                                object_pairs_hook=_no_duplicate_keys)
        except _DecodeError:
            raise
        except canon.CanonError as e:
            raise _DecodeError(str(e)) from None
        except RecursionError:
            raise _DecodeError("nesting depth exceeds the maximum of %d" % _MAX_DEPTH) from None
        except json.JSONDecodeError as e:
            raise _DecodeError("invalid JSON: %s" % e.msg) from None
        return _check_domain(parsed)
    if isinstance(value, Mapping):
        return _check_domain(value)
    raise TypeError("%s must be a mapping, JSON text or UTF-8 bytes, got %s"
                    % (what, type(value).__name__))


def _strip_envelope(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _ENVELOPE_KEYS}


# --------------------------------------------------------------------------------------------- #
# The anti-vacuity predicate (SEMANTICS sec 9 `spec_authority`; the engine's is_authoritative)
# --------------------------------------------------------------------------------------------- #

def is_authoritative(spec: Any) -> bool:
    """True iff S has >= 1 contract/architecture node whose effective provenance origin is
    ``human_authored`` and that is not marked ``unratified``. Absent ``provenance`` / ``origin``
    means ``human_authored``; absent ``unratified`` means ratified. Fails closed on a malformed
    marker: an ``unratified`` other than absent/null/false, a non-object ``provenance`` or a
    non-string ``origin`` never confers authority."""
    nodes = spec.get("nodes", []) if isinstance(spec, Mapping) else []
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if (not isinstance(node, Mapping) or not isinstance(node.get("level"), str)
                or node.get("level") not in _INTENT_LEVELS):
            continue
        unratified = node.get("unratified")
        if unratified is not None and unratified is not False:
            continue
        prov = node.get("provenance")
        if prov is None:
            origin = "human_authored"
        elif isinstance(prov, Mapping):
            origin = prov.get("origin")
            if origin is None:
                origin = "human_authored"
            elif not isinstance(origin, str):
                continue
        else:
            continue
        if origin == "human_authored":
            return True
    return False


# --------------------------------------------------------------------------------------------- #
# Schema checks
# --------------------------------------------------------------------------------------------- #

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def _read_pinned(rel: str) -> bytes:
    """A public data file's bytes, refused unless they match the manifest sha256 (the same check the
    bundled code gets): a tampered schema or vocabulary must not silently loosen the verdict."""
    data = _locate(rel).read_bytes()
    if hashlib.sha256(data).hexdigest() != _pinned_sha256(_public_root()).get(rel):
        raise VerifierUnavailable("public file %s does not match its manifest sha256; refusing to "
                                  "verify against it" % rel)
    return data


def _load_public_schema(name: str) -> Dict[str, Any]:
    with _LOCK:
        if name not in _SCHEMA_CACHE:
            rel = "spec-parity/schemas/%s.schema.json" % name
            _SCHEMA_CACHE[name] = json.loads(_read_pinned(rel).decode("utf-8"))
        return _SCHEMA_CACHE[name]


def _result_state_enums() -> Tuple[frozenset, frozenset]:
    defs = _load_public_schema("result-state")["$defs"]
    return frozenset(defs["result_state"]["enum"]), frozenset(defs["parity_dimension"]["enum"])


def _schema_problems(record: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
    """The load-bearing subset of a record schema, checked without jsonschema: required fields, no
    unknown field, top-level const / enum / string / boolean / pattern constraints."""
    problems: List[str] = []
    props = schema["properties"]
    missing = [k for k in schema["required"] if k not in record]
    if missing:
        problems.append("missing required field(s): %s" % ", ".join(missing))
    unknown = sorted(k for k in record if k not in props)
    if unknown and schema.get("additionalProperties") is False:
        problems.append("unknown field(s): %s" % ", ".join(unknown))
    for key, rule in props.items():
        if key not in record:
            continue
        value = record[key]
        if "const" in rule and value != rule["const"]:
            problems.append("%s must be %r, got %r" % (key, rule["const"], value))
        if "enum" in rule and value not in rule["enum"]:
            problems.append("%s must be one of %s, got %r" % (key, rule["enum"], value))
        if rule.get("type") == "string" and not isinstance(value, str):
            problems.append("%s must be a string" % key)
        if rule.get("type") == "boolean" and not isinstance(value, bool):
            problems.append("%s must be a boolean" % key)
        if "pattern" in rule and not (isinstance(value, str) and re.search(rule["pattern"], value)):
            problems.append("%s does not match %s" % (key, rule["pattern"]))
    return problems


def _is_member(value: Any, allowed: frozenset) -> bool:
    """Membership for an untrusted value: only a string can be a member (an unhashable list or dict
    must not raise TypeError)."""
    return isinstance(value, str) and value in allowed


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _dimension_problems(record: Mapping[str, Any]) -> List[str]:
    states, dimensions = _result_state_enums()
    problems: List[str] = []
    overall = record.get("overall_result_state")
    if "overall_result_state" in record and not _is_member(overall, states):
        problems.append("overall_result_state %r is not a result_state" % (overall,))
    dims = record.get("dimension_results")
    if "dimension_results" not in record:
        return problems
    if not isinstance(dims, list) or len(dims) != len(dimensions):
        return problems + ["dimension_results must hold exactly %d entries" % len(dimensions)]
    seen: List[str] = []
    for item in dims:
        if not isinstance(item, Mapping):
            problems.append("a dimension_results entry is not an object")
            continue
        if (not _is_member(item.get("dimension"), dimensions)
                or not _is_member(item.get("result_state"), states)):
            problems.append("malformed dimension_results entry %r" % (dict(item),))
            continue
        seen.append(item["dimension"])
    if sorted(seen) != sorted(dimensions):
        problems.append("dimension_results must name each parity dimension once")
    return problems


#: SEMANTICS sec 6.3 / 6.4 severity order (most severe first); not_applicable is neutral.
_SEVERITY = ("fail", "unknown", "unsupported")


def _aggregate(states: List[str]) -> str:
    """SEMANTICS sec 6.4: not_applicable iff every dimension is; else the most severe of fail >
    unknown > unsupported among the rest; else pass."""
    if all(state == "not_applicable" for state in states):
        return "not_applicable"
    for severity in _SEVERITY:
        if severity in states:
            return severity
    return "pass"


def _well_formed_dimensions(dims: Any) -> Optional[List[Mapping[str, Any]]]:
    """The dimension_results entries if each is an object with a string dimension and a known
    result_state; else None (the schema checks report why)."""
    states, _ = _result_state_enums()
    if not isinstance(dims, list) or not dims:
        return None
    for item in dims:
        if (not isinstance(item, Mapping) or not isinstance(item.get("dimension"), str)
                or not _is_member(item.get("result_state"), states)):
            return None
    return dims


def _aggregation_problems(record: Mapping[str, Any]) -> List[str]:
    """overall_result_state against the record's own dimension_results (SEMANTICS sec 6.3 / 6.4):
    a pure function of fields the record carries, so nothing is re-graded."""
    dims = _well_formed_dimensions(record.get("dimension_results"))
    if dims is None:
        return ["dimension_results are malformed; overall_result_state cannot be checked"]
    problems: List[str] = []
    expected = _aggregate([d["result_state"] for d in dims])
    overall = record.get("overall_result_state")
    if overall != expected:
        problems.append("overall_result_state %r contradicts its dimension_results, which "
                        "aggregate to %r (SEMANTICS sec 6.4)" % (overall, expected))
    for d in dims:
        if "checks_evaluated" not in d:
            continue
        count = d["checks_evaluated"]
        if not _is_count(count):
            problems.append("%s checks_evaluated %r is not a count" % (d["dimension"], count))
        elif (count == 0) != (d["result_state"] == "not_applicable"):
            problems.append("%s is %r with %d checks evaluated: a dimension is not_applicable "
                            "exactly when it evaluated no check (SEMANTICS sec 6.3)"
                            % (d["dimension"], d["result_state"], count))
    if "vacuity" in record and expected != "not_applicable":
        problems.append("a vacuous record must be not_applicable in every dimension")
    return problems


def _finding_set_problems(canon: Any, fs: Mapping[str, Any]) -> List[str]:
    """A finding set against itself (SEMANTICS sec 6.2 / 6.3 / 6.4): each finding_id recomputes over
    {dimension, result_state, code, subject}; each dimension's finding_ids are exactly its findings;
    each dimension's state is the rollup of its findings; overall is the sec 6.4 aggregation."""
    states, dimensions = _result_state_enums()
    findings = fs.get("findings")
    if not isinstance(findings, list):
        return ["findings is not a list"]
    problems: List[str] = []
    by_dimension: Dict[str, List[Mapping[str, Any]]] = {}
    for i, f in enumerate(findings):
        if not isinstance(f, Mapping) or not all(k in f for k in ("finding_id", "dimension",
                                                                    "result_state", "code",
                                                                    "subject")):
            problems.append("finding %d is not a well-formed finding" % i)
            continue
        if not _is_member(f["dimension"], dimensions) or not _is_member(f["result_state"], states):
            problems.append("finding %d has an unknown dimension or result_state" % i)
            continue
        fid, why = _digest_or_reason(lambda: canon.digest(
            {k: f[k] for k in ("dimension", "result_state", "code", "subject")}, "semantic-content"))
        if fid is None or fid != f["finding_id"]:
            problems.append("finding %d: finding_id does not recompute over {dimension, "
                            "result_state, code, subject}%s" % (i, (": " + why) if why else ""))
        by_dimension.setdefault(f["dimension"], []).append(f)
    dims = _well_formed_dimensions(fs.get("dimension_results"))
    if dims is None:
        return problems + ["dimension_results are malformed"]
    for d in dims:
        mine = by_dimension.pop(d["dimension"], [])
        ids = d.get("finding_ids")
        mine_ids = sorted(str(f["finding_id"]) for f in mine)
        if ids is not None and (not isinstance(ids, list) or ids != mine_ids):
            problems.append("%s finding_ids are not exactly its findings" % d["dimension"])
        worst = [s for s in _SEVERITY if any(f["result_state"] == s for f in mine)]
        if d["result_state"] == "not_applicable":
            if mine:
                problems.append("%s is not_applicable but has findings" % d["dimension"])
        elif d["result_state"] != (worst[0] if worst else "pass"):
            problems.append("%s is %r but its findings roll up to %r (SEMANTICS sec 6.3)"
                            % (d["dimension"], d["result_state"], worst[0] if worst else "pass"))
    if by_dimension:
        problems.append("findings for dimensions without a result: %s" % sorted(by_dimension))
    overall = fs.get("overall_result_state")
    expected = _aggregate([d["result_state"] for d in dims])
    if overall != expected:
        problems.append("overall_result_state %r is not the aggregation %r of its "
                        "dimension_results (SEMANTICS sec 6.4)" % (overall, expected))
    return problems


def _require_jsonschema() -> None:
    """Raise VerifierUnavailable unless jsonschema (with referencing) is importable: the full schema
    check is part of every verdict, so it is never silently skipped."""
    try:
        import jsonschema  # noqa: F401
        import referencing.jsonschema  # noqa: F401
    except ImportError as e:
        raise VerifierUnavailable(
            'verify_certificate needs jsonschema>=4.18 for the full schema check: install '
            '"consiliency-spec[verify]": %s' % e) from e


def _jsonschema_errors(record: Mapping[str, Any], schema_name: str) -> List[str]:
    """Full JSON Schema 2020-12 validation against a public schema (with its $ref registry)."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resources_ = []
    for name in ("result-state", "certificate", "portal-payload"):
        schema = _load_public_schema(name)
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        resources_ += [(schema["$id"], resource), ("%s.schema.json" % name, resource)]
    validator = Draft202012Validator(_load_public_schema(schema_name),
                                     registry=Registry().with_resources(resources_))
    return sorted("%s: %s" % ("/".join(str(p) for p in e.absolute_path) or "<root>", e.message)
                  for e in validator.iter_errors(record))


# --------------------------------------------------------------------------------------------- #
# The verifier
# --------------------------------------------------------------------------------------------- #

class _Checks:
    def __init__(self) -> None:
        self.items: List[VerificationCheck] = []

    def add(self, name: str, ok: bool, reason: str) -> bool:
        self.items.append(VerificationCheck(name, PASS if ok else FAIL, reason))
        return bool(ok)

    def failed(self) -> bool:
        return any(item.status == FAIL for item in self.items)


def _digest_or_reason(fn: Callable[[], str]) -> Tuple[Optional[str], str]:
    try:
        return fn(), ""
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _same(canon: Any, a: Any, b: Any) -> bool:
    try:
        return canon.canonical_bytes(a) == canon.canonical_bytes(b)
    except (ValueError, TypeError):
        return False


def verify_certificate(
    certificate: JsonInput,
    *,
    desired_graph: Optional[JsonInput] = None,
    ec: Optional[JsonInput] = None,
    finding_set: Optional[JsonInput] = None,
    payload: Optional[JsonInput] = None,
    require_authoritative: bool = False,
) -> CertificateVerification:
    """Verify a spec-parity certificate (schema_version "2") and, for every input supplied, that the
    certificate pins it. See the module docstring for the checks; nothing is re-graded.

    ``certificate``     the certificate (JSON text, UTF-8 bytes, or a parsed mapping).
    ``desired_graph``   a candidate S'; runs the SEMANTICS section 9 binding procedure.
    ``ec``              the E(C) the certificate claims (``ec_digest``).
    ``finding_set``     the finding set at ``findings_ref``.
    ``payload``         a portal payload delivered for this certificate.
    ``require_authoritative``  also fail unless the certificate is gating (grounded,
                        ``ec_reproducible`` true, S' supplied and bound, E(C) supplied and
                        matching).
    """
    if not isinstance(require_authoritative, bool):
        raise TypeError("require_authoritative must be a bool")
    for name, value in (("certificate", certificate), ("desired_graph", desired_graph), ("ec", ec),
                        ("finding_set", finding_set), ("payload", payload)):
        if value is None and name != "certificate":
            continue
        if not isinstance(value, (Mapping, str, bytes, bytearray)):
            raise TypeError("%s must be a mapping, JSON text or UTF-8 bytes, got %s"
                            % (name, type(value).__name__))
    _require_jsonschema()  # may raise VerifierUnavailable
    spec_graph = _spec_graph() if desired_graph is not None else None  # may raise VerifierUnavailable
    canon = _canon()
    checks = _Checks()
    recomputed: Dict[str, str] = {}

    # -- decode ----------------------------------------------------------------------------------
    try:
        cert = _decode(certificate, "certificate")
    except _DecodeError as e:
        checks.add("decode", False, "certificate: %s" % e)
        return _result(checks, None, recomputed, bound=False, ec_matched=False)
    if not isinstance(cert, dict):
        checks.add("decode", False, "certificate is not a JSON object")
        return _result(checks, None, recomputed, bound=False, ec_matched=False)
    checks.add("decode", True, "certificate parsed under canon's strict number rules")

    # -- schema ----------------------------------------------------------------------------------
    cert_schema = _load_public_schema("certificate")
    problems = _schema_problems(cert, cert_schema) + _dimension_problems(cert)
    checks.add("schema", not problems,
               "; ".join(problems) if problems else
               "field set matches certificate.schema.json (schema_version %r)"
               % cert_schema["properties"]["schema_version"]["const"])
    errors = _jsonschema_errors(cert, "certificate")
    checks.add("json_schema", not errors,
               "; ".join(errors[:5]) if errors else "valid against certificate.schema.json")
    canon_ok = cert.get("canon_version") == CANON_VERSION
    checks.add("canon_version", canon_ok,
               "canon_version is %r" % CANON_VERSION if canon_ok else
               "canon_version %r is not %r: its digests are never recomputed under canon %s"
               % (cert.get("canon_version"), CANON_VERSION, CANON_VERSION))
    problems = _aggregation_problems(cert)
    checks.add("aggregation", not problems,
               "; ".join(problems) if problems else
               "overall_result_state is the SEMANTICS sec 6.4 aggregation of dimension_results")

    # -- the certificate's own digest ------------------------------------------------------------
    claimed = cert.get("digest")
    if canon_ok:
        got, why = _digest_or_reason(lambda: canon.digest(_strip_envelope(cert), "certificate"))
        if got is not None:
            recomputed["certificate_digest"] = got
        checks.add("certificate_digest", got is not None and got == claimed,
                   ("cannot canonicalize the certificate: %s" % why) if got is None else
                   "digest recomputes" if got == claimed else
                   "recomputed %s != certificate digest %r" % (got, claimed))
    else:
        checks.add("certificate_digest", False, "not recomputed: canon_version is not %r"
                   % CANON_VERSION)

    # -- finding set -----------------------------------------------------------------------------
    fs_value: Optional[Dict[str, Any]] = None
    if finding_set is not None:
        fs_value = _check_finding_set(checks, recomputed, canon, cert, finding_set)

    # -- E(C) ------------------------------------------------------------------------------------
    ec_matched = False
    if ec is not None:
        try:
            ec_value = _decode(ec, "ec")
        except _DecodeError as e:
            checks.add("ec_digest", False, "ec: %s" % e)
        else:
            got, why = _digest_or_reason(lambda: canon.digest(ec_value, "semantic-content"))
            if got is not None:
                recomputed["ec_digest"] = got
            ec_matched = got is not None and got == cert.get("ec_digest")
            checks.add("ec_digest", ec_matched,
                       ("cannot canonicalize E(C): %s" % why) if got is None else
                       "ec_digest recomputes" if got == cert.get("ec_digest") else
                       "canon digest of E(C) %s != certificate ec_digest %r"
                       % (got, cert.get("ec_digest")))

    # -- the desired graph: the binding procedure ------------------------------------------------
    bound = False
    if desired_graph is not None:
        bound = _bind_desired_graph(checks, recomputed, canon, spec_graph, cert, desired_graph)

    # -- portal payload --------------------------------------------------------------------------
    if payload is not None:
        _check_payload(checks, recomputed, canon, cert, payload,
                       fs_value if finding_set is not None else None)

    if require_authoritative:
        reasons = []
        if cert.get("spec_authority") != "grounded":
            reasons.append("spec_authority is %r, not 'grounded'" % (cert.get("spec_authority"),))
        if cert.get("ec_reproducible") is not True:
            reasons.append("ec_reproducible is not true (advisory certificate)")
        if not bound:
            reasons.append("no desired graph was bound" if desired_graph is None else
                           "the desired graph did not bind")
        if not ec_matched:
            reasons.append("no E(C) was supplied" if ec is None else
                           "the supplied E(C) does not match ec_digest")
        checks.add("authoritative", not reasons, "; ".join(reasons) if reasons else
                   "grounded, measured-reproducible E(C) matching ec_digest, bound to the "
                   "supplied desired graph")

    return _result(checks, cert, recomputed, bound=bound, ec_matched=ec_matched)


def _result(checks: _Checks, cert: Optional[Dict[str, Any]], recomputed: Dict[str, str], *,
            bound: bool, ec_matched: bool) -> CertificateVerification:
    valid = not checks.failed()
    cert = cert or {}
    authority = cert.get("spec_authority") if isinstance(cert.get("spec_authority"), str) else None
    repro = cert.get("ec_reproducible") if isinstance(cert.get("ec_reproducible"), bool) else None
    overall = cert.get("overall_result_state")
    return CertificateVerification(
        valid=valid,
        bound=bound,
        authoritative=(valid and bound and ec_matched and authority == "grounded"
                       and repro is True),
        advisory=repro is not True,
        digest=cert.get("digest") if isinstance(cert.get("digest"), str) else None,
        spec_authority=authority,
        ec_reproducible=repro,
        overall_result_state=overall if isinstance(overall, str) else None,
        checks=tuple(checks.items),
        recomputed=recomputed,
    )


def _check_finding_set(checks: _Checks, recomputed: Dict[str, str], canon: Any,
                       cert: Mapping[str, Any],
                       finding_set: JsonInput) -> Optional[Dict[str, Any]]:
    """The finding-set checks; returns the decoded finding set (None when it does not decode)."""
    try:
        fs = _decode(finding_set, "finding_set")
    except _DecodeError as e:
        checks.add("finding_set_id", False, "finding_set: %s" % e)
        return None
    if not isinstance(fs, dict) or not {"dimension_results", "findings"} <= set(fs):
        checks.add("finding_set_id", False, "finding_set lacks dimension_results/findings")
        return fs if isinstance(fs, dict) else None
    core = {"dimension_results": fs["dimension_results"], "findings": fs["findings"]}
    got, why = _digest_or_reason(lambda: canon.digest(core, "semantic-content"))
    if got is not None:
        recomputed["finding_set_id"] = got
    checks.add("finding_set_id", got is not None and got == fs.get("finding_set_id"),
               ("cannot canonicalize the finding set: %s" % why) if got is None else
               "finding_set_id recomputes over {dimension_results, findings}"
               if got == fs.get("finding_set_id") else
               "recomputed %s != finding_set_id %r" % (got, fs.get("finding_set_id")))
    ref_ok = got is not None and cert.get("findings_ref") == got
    checks.add("findings_ref", ref_ok,
               "certificate findings_ref is this finding set" if ref_ok else
               "certificate findings_ref %r != the finding set's id %s"
               % (cert.get("findings_ref"), got))
    copies_ok = (_same(canon, cert.get("overall_result_state"), fs.get("overall_result_state"))
                 and _same(canon, cert.get("dimension_results"), fs.get("dimension_results"))
                 and _same(canon, cert.get("vacuity"), fs.get("vacuity")))
    checks.add("finding_set_copy", copies_ok,
               "overall_result_state, dimension_results and vacuity are byte-equal copies"
               if copies_ok else
               "the certificate's overall_result_state/dimension_results/vacuity differ from the "
               "finding set's (the certificate copies, never recomputes, them)")
    errors = _jsonschema_errors(fs, "result-state")
    checks.add("finding_set_schema", not errors,
               "; ".join(errors[:5]) if errors else
               "valid against result-state.schema.json (the finding_set record)")
    problems = _finding_set_problems(canon, fs)
    checks.add("finding_set_consistency", not problems,
               "; ".join(problems[:5]) if problems else
               "every finding_id recomputes and each dimension state is the rollup of its findings")
    return fs


def _bind_desired_graph(checks: _Checks, recomputed: Dict[str, str], canon: Any, spec_graph: Any,
                        cert: Mapping[str, Any], desired_graph: JsonInput) -> bool:
    # (1) the engine's entry check: the full spec-graph rule set.
    try:
        graph = _decode(desired_graph, "desired_graph")
    except _DecodeError as e:
        checks.add("desired_graph_valid", False, "desired_graph: %s" % e)
        return False
    try:
        if not isinstance(graph, dict):
            raise ValueError("S is not a JSON object")
        spec_graph.validate(graph)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as e:
        checks.add("desired_graph_valid", False, "S' is refused by spec-graph validation: %s" % e)
        return False
    checks.add("desired_graph_valid", True, "S' passes the full spec-graph rule set")
    # (2) the normalized graph digest.
    graph_digest, why = _digest_or_reason(lambda: spec_graph.graph_digest(graph))
    if graph_digest is None:
        checks.add("desired_graph_digest", False, "S' cannot be normalized: %s" % why)
        return False
    recomputed["desired_graph_digest"] = graph_digest
    g_ok = graph_digest == cert.get("desired_graph_digest")
    checks.add("desired_graph_digest", g_ok,
               "graph_digest(S') equals desired_graph_digest" if g_ok else
               "graph_digest(S') %s != desired_graph_digest %r"
               % (graph_digest, cert.get("desired_graph_digest")))
    # (3) the revision binding: {spec_revision_id ("" when absent), desired_graph_digest}.
    revision_digest, why = _digest_or_reason(lambda: canon.digest(
        {"spec_revision_id": graph.get("spec_revision_id", ""),
         "desired_graph_digest": graph_digest}, "semantic-content"))
    if revision_digest is not None:
        recomputed["spec_revision_digest"] = revision_digest
    r_ok = revision_digest is not None and revision_digest == cert.get("spec_revision_digest")
    checks.add("spec_revision_digest", r_ok,
               ("cannot digest S' revision: %s" % why) if revision_digest is None else
               "spec_revision_digest(S') equals spec_revision_digest" if r_ok else
               "spec_revision_digest(S') %s != certificate %r (S' carries a different "
               "spec_revision_id, or a different graph)"
               % (revision_digest, cert.get("spec_revision_digest")))
    # (4) authority: is_authoritative(S') == (spec_authority == "grounded").
    s_grounded = is_authoritative(graph)
    claimed = cert.get("spec_authority")
    a_ok = claimed in ("grounded", "draft") and s_grounded == (claimed == "grounded")
    checks.add("spec_authority", a_ok,
               "is_authoritative(S') agrees with spec_authority %r" % (claimed,) if a_ok else
               "is_authoritative(S') is %s but the certificate says spec_authority %r"
               % (s_grounded, claimed))
    return g_ok and r_ok and a_ok


#: Fields a portal payload copies verbatim from its certificate (SEMANTICS sec 12).
_PAYLOAD_COPIED = (
    "schema_version", "projection_algo_version", "canon_version", "idmodel_version",
    "kind_alignment_version", "permitted_freedom_vocab_version", "ec_revision_id",
    "spec_revision_digest", "desired_graph_digest", "ec_digest", "code_head_sha",
    "overall_result_state", "findings_ref",
)

# --------------------------------------------------------------------------------------------- #
# The SEMANTICS sec 12 projection (certificate, finding_set) -> payload, ported verbatim from the
# reference engine's delivery step. The engine's own test suite asserts these tables equal the
# engine's on every committed pair, so they cannot drift.
# --------------------------------------------------------------------------------------------- #

PAYLOAD_SCHEMA_VERSION = "0"

_BADGE = {
    "pass": "green",
    "fail": "failing",
    "unknown": "non_green",
    "unsupported": "non_green",
    "not_applicable": "neutral",
}

_CODE_TITLE = {
    "missing_desired_element": "Missing desired element",
    "structural_mismatch": "Structural mismatch",
    "kind_mismatch": "Realized kind does not align to the desired kind",
    "signature_unobservable": "Signature not observable",
    "signature_undeclared": "Operation declares no signature",   # retired (projection v4): full validation at entry
    "prohibition_violated": "Prohibition violated",
    "prohibition_unobservable": "Prohibition not observable",
    "prohibition_domain_unscanned": "Prohibition domain not scanned",
    "prohibition_no_domain": "Prohibition declares no domain",
    "prohibition_outside_default_closure": "Prohibition governs nothing in the default closure",
    "unmapped_realized_kind": "Unmapped realized kind",
    "unmapped_desired_kind": "Unmapped desired kind",
    "ambiguous_kind_alignment": "Ambiguous kind alignment",
    "no_realized_mapping": "No realized mapping for desired kind",
    "member_set_unobservable": "Type members not observable",
    "permitted_freedom": "Permitted freedom",
    "permitted_freedom_unknown_token": "Unknown permitted-freedom token",
    "unclassified_realized_fact": "Unclassified realized fact",   # retired: closure never emits it
    "correspondence_ambiguous": "Ambiguous correspondence",
    "correspondence_deleted": "Correspondence marks entity deleted",
    "correspondence_superseded": "Correspondence marks entity superseded",   # v1 only (projection v2 retired it)
    "correspondence_duplicate": "Duplicate correspondence entries",
    "correspondence_unresolved": "Unresolved correspondence",
    "capability_missing": "Capability missing",
    "desired_element_unobservable": "Desired element not observable (partial extraction)",
    "closure_extraction_partial": "Closure not total (partial extraction)",
}

_LOCATION_KEYS = (
    "desired_logical_id",
    "realized_occurrence_id",
    "desired_kind",
    "realized_kind",
    "realized_source",
)


def _title_for(code: str) -> str:
    """Stable title for a finding code. Falls back to a humanized form of the CODE (never the
    message) for an unrecognized code."""
    if code in _CODE_TITLE:
        return _CODE_TITLE[code]
    return code.replace("_", " ").strip().capitalize() or "Finding"


def _finding_summary(f: dict) -> dict:
    """Allowlist-project one finding into a metadata-only summary. message/evidence_refs/confidence
    are NEVER read."""
    code = f["code"]
    summary = {
        "finding_id": f["finding_id"],
        "dimension": f["dimension"],
        "result_state": f["result_state"],
        "code": code,
        "title": _title_for(code),
    }
    subject = f.get("subject") or {}
    location = {k: subject[k] for k in _LOCATION_KEYS if k in subject and subject[k] is not None}
    if location:
        summary["location"] = location
    if f.get("waiver_ref"):
        summary["waiver_ref"] = f["waiver_ref"]
    return summary


def _dimension_result(dr: dict) -> dict:
    """Allowlist-project one certificate dimension_result into the state-only payload form."""
    out = {"dimension": dr["dimension"], "result_state": dr["result_state"]}
    if "checks_evaluated" in dr:
        out["checks_evaluated"] = dr["checks_evaluated"]
    return out


def _project(certificate: Mapping[str, Any], finding_set: Mapping[str, Any]) -> Dict[str, Any]:
    """The payload deliver(certificate, finding_set, allow_draft=True) builds, without its digest:
    the same allowlist, the same summaries sorted by finding_id, spec_authority derived the same
    way. No draft refusal and no metadata-only assertion (a verifier reports, it does not deliver).
    Raises KeyError/TypeError/ValueError/AttributeError on malformed input; the caller turns that
    into a failed check."""
    overall = certificate["overall_result_state"]
    if overall not in _BADGE:
        raise ValueError(f"unknown overall_result_state: {overall!r}")
    ec_reproducible = certificate.get("ec_reproducible") is True
    spec_authority = "grounded" if certificate.get("spec_authority") == "grounded" else "draft"
    summaries = [_finding_summary(f) for f in finding_set.get("findings", [])]
    summaries.sort(key=lambda s: s["finding_id"])
    payload: Dict[str, Any] = {
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "schema_version": certificate["schema_version"],
        "projection_algo_version": certificate["projection_algo_version"],
        "canon_version": certificate["canon_version"],
        "idmodel_version": certificate["idmodel_version"],
        "kind_alignment_version": certificate["kind_alignment_version"],
        "permitted_freedom_vocab_version": certificate["permitted_freedom_vocab_version"],
        "certificate_digest": certificate["digest"],
        "ec_revision_id": certificate["ec_revision_id"],
        "spec_revision_digest": certificate["spec_revision_digest"],
        "desired_graph_digest": certificate["desired_graph_digest"],
        "ec_digest": certificate["ec_digest"],
        "ec_reproducible": ec_reproducible,
        "advisory": not ec_reproducible,
        "spec_authority": spec_authority,
        "code_head_sha": certificate["code_head_sha"],
        "overall_result_state": overall,
        "badge": _BADGE[overall],
        "dimension_results": [_dimension_result(dr) for dr in certificate["dimension_results"]],
        "finding_summaries": summaries,
        "findings_ref": certificate["findings_ref"],
    }
    if certificate.get("waivers_ref"):
        payload["waivers_ref"] = certificate["waivers_ref"]
    if certificate.get("authority_ref"):
        payload["authority_ref"] = certificate["authority_ref"]
    if certificate.get("extraction_coverage") is not None:
        payload["extraction_coverage"] = dict(certificate["extraction_coverage"])
    return payload


def _first_difference(canon: Any, got: Mapping[str, Any], expected: Mapping[str, Any]) -> str:
    for key in sorted(set(got) | set(expected)):
        if key not in got:
            return "%s is missing" % key
        if key not in expected:
            return "%s is not part of the projection" % key
        if not _same(canon, got[key], expected[key]):
            return "%s differs" % key
    return "no key differs"  # pragma: no cover - only reached when the two are equal


def _summary_problems(cert: Mapping[str, Any], summaries: Any) -> List[str]:
    """Without the finding set, the summaries checked against what the certificate itself pins: its
    dimensions' finding_ids, the code-derived titles and the sec 6.3 rollup."""
    states, _ = _result_state_enums()
    dims = _well_formed_dimensions(cert.get("dimension_results"))
    if dims is None:
        return ["the certificate's dimension_results are malformed; finding_summaries unchecked"]
    if not isinstance(summaries, list) or not all(
            isinstance(s, Mapping) and isinstance(s.get("finding_id"), str)
            and isinstance(s.get("dimension"), str) and isinstance(s.get("code"), str)
            and _is_member(s.get("result_state"), states) for s in summaries):
        return ["finding_summaries is not a list of well-formed summaries"]
    problems: List[str] = []
    ids = [s["finding_id"] for s in summaries]
    if ids != sorted(set(ids)):
        problems.append("finding_summaries ids are not unique and sorted")
    listed: Dict[str, str] = {}
    for d in dims:
        fids = d.get("finding_ids", [])
        if not isinstance(fids, list) or not all(isinstance(f, str) for f in fids):
            return problems + ["%s finding_ids is not a list of ids" % d["dimension"]]
        for fid in fids:
            listed[fid] = d["dimension"]
    if set(ids) != set(listed):
        problems.append("finding_summaries are not one per finding the certificate lists "
                        "(%d summaries, %d listed findings)" % (len(set(ids)), len(listed)))
    for s in summaries:
        if listed.get(s["finding_id"], s["dimension"]) != s["dimension"]:
            problems.append("summary %s is not listed under its dimension %s"
                            % (s["finding_id"][:12], s["dimension"]))
        if s.get("title") != _title_for(s["code"]):
            problems.append("summary %s title is not derived from its code" % s["finding_id"][:12])
    for d in dims:
        mine = [s["result_state"] for s in summaries if s["dimension"] == d["dimension"]]
        if d["result_state"] == "not_applicable":
            if mine:
                problems.append("%s is not_applicable but has summaries" % d["dimension"])
            continue
        worst = [sev for sev in _SEVERITY if sev in mine]
        rollup = worst[0] if worst else "pass"
        if rollup != d["result_state"]:
            problems.append("%s is %r but its summaries roll up to %r (SEMANTICS sec 6.3)"
                            % (d["dimension"], d["result_state"], rollup))
    return problems


def _check_payload(checks: _Checks, recomputed: Dict[str, str], canon: Any,
                   cert: Mapping[str, Any], payload: JsonInput,
                   fs: Optional[Mapping[str, Any]] = None) -> None:
    try:
        pl = _decode(payload, "payload")
    except _DecodeError as e:
        checks.add("payload_digest", False, "payload: %s" % e)
        return
    if not isinstance(pl, dict):
        checks.add("payload_digest", False, "payload is not a JSON object")
        return
    got, why = _digest_or_reason(lambda: canon.digest(_strip_envelope(pl), "semantic-content"))
    if got is not None:
        recomputed["payload_digest"] = got
    checks.add("payload_digest", got is not None and got == pl.get("digest"),
               ("cannot canonicalize the payload: %s" % why) if got is None else
               "payload digest recomputes" if got == pl.get("digest") else
               "recomputed %s != payload digest %r" % (got, pl.get("digest")))
    problems = _schema_problems(pl, _load_public_schema("portal-payload"))
    errors = _jsonschema_errors(pl, "portal-payload")
    checks.add("payload_schema", not problems and not errors,
               "; ".join((problems + errors)[:5]) if problems or errors else
               "valid against portal-payload.schema.json")
    problems = []
    if pl.get("certificate_digest") != cert.get("digest"):
        problems.append("certificate_digest is not the certificate's digest")
    for key in _PAYLOAD_COPIED:
        if pl.get(key) != cert.get(key) or type(pl.get(key)) is not type(cert.get(key)):
            problems.append("%s disagrees with the certificate" % key)
    for key in ("waivers_ref", "authority_ref", "extraction_coverage"):
        if not _same(canon, pl.get(key), cert.get(key)):
            problems.append("%s disagrees with the certificate" % key)
    repro = cert.get("ec_reproducible") is True
    if (pl.get("ec_reproducible") is True) != repro or pl.get("advisory") is not (not repro):
        problems.append("ec_reproducible/advisory disagree with the certificate "
                        "(only an explicit true is reproducible)")
    authority = "grounded" if cert.get("spec_authority") == "grounded" else "draft"
    if pl.get("spec_authority") != authority:
        problems.append("spec_authority disagrees with the certificate")
    overall = cert.get("overall_result_state")
    if pl.get("badge") != (_BADGE.get(overall) if isinstance(overall, str) else None):
        problems.append("badge is not derived from overall_result_state")
    dims = cert.get("dimension_results")
    pdims = pl.get("dimension_results")
    if not (isinstance(dims, list) and isinstance(pdims, list) and len(dims) == len(pdims) and all(
            isinstance(a, Mapping) and isinstance(b, Mapping)
            and _same(canon, {k: a[k] for k in ("dimension", "result_state", "checks_evaluated")
                              if k in a}, dict(b))
            for a, b in zip(dims, pdims))):
        problems.append("dimension_results disagree with the certificate")
    if fs is None:
        problems += _summary_problems(cert, pl.get("finding_summaries"))
    checks.add("payload_binding", not problems,
               "; ".join(problems[:5]) if problems else
               "payload pairs with the certificate and copies its pins unchanged" +
               ("" if fs is not None else
                "; its finding_summaries are one per listed finding, titled by code, and roll up "
                "to each dimension's state"))
    if fs is not None:
        got_payload = {k: v for k, v in _strip_envelope(pl).items() if k != "digest"}
        try:
            expected = _project(cert, fs)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            checks.add("payload_projection", False, "cannot project (certificate, finding_set): "
                       "%s: %s" % (type(e).__name__, e))
            return
        ok = _same(canon, got_payload, expected)
        checks.add("payload_projection", ok,
                   "payload is exactly the SEMANTICS sec 12 projection of (certificate, "
                   "finding_set)" if ok else
                   "payload is not the sec 12 projection of (certificate, finding_set): %s"
                   % _first_difference(canon, got_payload, expected))
