"""spec-graph v1 — Python reference implementation (desired-state semantic metamodel).

Normative contract: ../SPEC.md. Builds on canon v1 (../../canon/py/canon.py) and
idmodel v1 (../../idmodel/py/idmodel.py).

The model is a typed node/edge desired-state graph (the ``S`` in ``N(S) = N(P(E(C),S))``). It owns
an OPEN, VERSIONED ``kind`` registry spanning architecture AND code-level semantics (an earlier
closed 7-value arch-only kind enum is a reference only; this is the gap it could not fill).

Two-identifier model (SPEC.md section 1) — load-bearing:
  - logical_id (idmodel)       : a node's cross-revision identity (NARROW). Edges reference nodes by
                                 name; logical_id is the continuity key. Stable under body/rationale
                                 change; changes under rename/move/signature change.
  - content_digest (canon)     : canon semantic-content digest over ALL formal fields (WIDE). Changes
                                 whenever ANY formal field changes — including fields idmodel does not
                                 see (error code, invariant expression, ...).
The graph digest is canon over the normalized nodes+edges hashed content. A rationale-only edit does
NOT change the graph digest; any formal edit does. Only the two-id split delivers both.

Public API:
    NODE_KINDS, EDGE_KINDS, LEVELS, REGISTRY_VERSION   - the open versioned registry (v1 set)
    node_descriptor(node)        -> dict   idmodel four-field descriptor for a node
    node_logical_id(node)        -> str    idmodel.logical_id of a node (NARROW id)
    node_content_digest(node)    -> str    canon semantic-content over a node's hashed fields (WIDE)
    edge_content_digest(edge)    -> str    canon semantic-content over an edge's hashed fields
    normalize(graph)             -> dict   Normalize(S): canonical, hashed+envelope, sorted, idempotent
    graph_digest(graph)          -> str    canon semantic-content over the normalized hashed content
    validate(graph)              -> None   shape (schema strength) + structural + cross-level +
                                           reference checks (raises)
    SpecGraphError                         the error type
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, FrozenSet

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "canon", "py"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "idmodel", "py"))
import canon  # noqa: E402
import canon_ingest as ingest  # noqa: E402  (canon v2: NFC at the ingestion boundary, not in canon)
import idmodel  # noqa: E402

SPEC_GRAPH_VERSION = "1"
REGISTRY_VERSION = "1"

LEVELS = ("contract", "architecture", "detailed")
# Strictly-descending decomposition order (SPEC.md section 4).
_LEVEL_INDEX = {lvl: i for i, lvl in enumerate(LEVELS)}

NODE_KINDS = (
    "component", "capability", "interface", "operation", "type",
    "state", "invariant", "error", "event", "security_rule",
    "performance", "prohibition", "acceptance_criterion",
)

EDGE_KINDS = (
    "decomposes_to", "depends_on", "provides", "exposes", "raises",
    "emits", "transitions_to", "governed_by", "verified_by",
)

# Closed-world prohibition domain types (Phase-3a SEMANTICS.md section 4.1, verbatim v1 set).
# `external` always yields `unknown` (the extractor does not model that domain). Extensible.
PROHIBITION_DOMAIN_TYPES = (
    "edge_set", "node_kind_set", "signature_facts", "capability_registration", "external",
)

# Per-kind formal fields beyond the common set (SPEC.md section 3).
# `domain` is a prohibition's closed-world domain descriptor (Phase-3a parity hook, section 4.1):
# it bounds the fact set the prohibition is decided over. WITHOUT it a prohibition is `unsupported`.
_KIND_FORMAL_FIELDS = {
    "interface": ("signature",),
    "operation": ("signature",),
    "type": ("type_form", "members"),
    "invariant": ("expression",),
    "error": ("code",),
    "event": ("payload_ref",),
    "security_rule": ("rule",),
    "performance": ("budget",),
    "prohibition": ("statement", "observable", "domain"),
    "acceptance_criterion": ("given", "when", "then"),
}
_KIND_REQUIRED_FIELDS = {
    "operation": ("signature",),
    "invariant": ("expression",),
    "security_rule": ("rule",),
    "performance": ("budget",),
    "prohibition": ("statement", "observable", "domain"),
    "acceptance_criterion": ("then",),
}

# permitted_freedom token vocabulary (Phase-3a SEMANTICS.md sec 3). The vocabulary file
# spec-parity/permitted-freedom-vocab.json is the SINGLE SOURCE OF TRUTH for the closed/versioned
# token set; validate() rejects any declared token not in it (unknown token = validation error in
# v1, additive by design). Loaded lazily + cached so the registry value list is never duplicated here.
_PF_VOCAB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "spec-parity", "permitted-freedom-vocab.json")
_PF_VOCAB_TOKENS_CACHE: Optional[FrozenSet[str]] = None


def permitted_freedom_tokens() -> FrozenSet[str]:
    """The set of valid permitted_freedom token ids, read from the vocabulary file (the single
    source of truth). Cached after first read."""
    global _PF_VOCAB_TOKENS_CACHE
    if _PF_VOCAB_TOKENS_CACHE is None:
        with open(_PF_VOCAB_PATH, "r", encoding="utf-8") as fh:
            vocab = json.load(fh)
        _PF_VOCAB_TOKENS_CACHE = frozenset(t["id"] for t in vocab["tokens"])
    return _PF_VOCAB_TOKENS_CACHE


# Common FORMAL (hashed) parity-hook fields valid on ANY node (Phase-3a SEMANTICS.md), handled
# inline in _node_hashed_content / validate:
#   frontier          - bool, default False; declares node in-scope for a parity run (section 1.2.2).
#   permitted_freedom - order-insensitive string-token set; what realized code may vary below the
#                       frontier without a soundness violation (section 3). Sorted in Normalize.
# Both are FORMAL: P/N read them to compute the parity result, so they MUST be inside the
# desired_graph_digest (Phase-3a SEMANTICS.md section 9 — every result-affecting input is pinned).
FRONTIER_DEFAULT = False

# Fields that are ENVELOPE (never hashed) — canon content/envelope split (SPEC.md sections 2-3).
# provenance/unratified are the brownfield draft-S markers: a node's draft origin
# (code_derived | doc_derived | human_authored, + optional boundary_id/clause_id) and whether it is
# still an UNRATIFIED draft. They are NON-HASHED ENVELOPE exactly like rationale/notes — so a node's
# provenance/unratified does NOT change its graph_digest OR its logical_id (the bootstrapped draft
# stays content-addressable + identity-stable). LOAD-BEARING: if they ever entered the digest, the
# whole brownfield freeze breaks.
_NODE_ENVELOPE_FIELDS = ("rationale", "notes", "source_position", "provenance", "unratified")
_EDGE_ENVELOPE_FIELDS = ("rationale", "notes", "source_position")
# source_position is additionally excluded from round-trip equality (re-derived on parse).

# Draft-S provenance origins. A node with NO provenance is treated as human_authored
# (the pre-brownfield default — the hand-authored self-spec is an authority). code_derived/doc_derived/
# llm_proposed (NL intake draft) are draft origins; only human_authored (+ not unratified) is.
PROVENANCE_ORIGINS = ("code_derived", "doc_derived", "llm_proposed", "human_authored")


class SpecGraphError(ValueError):
    """Raised for any structural / registry / reference violation."""


# --------------------------------------------------------------------------- #
# Two-identifier model (SPEC.md section 1)
# --------------------------------------------------------------------------- #

def node_descriptor(node: Dict[str, Any]) -> Dict[str, Any]:
    """Build the idmodel four-field descriptor for a node (SPEC.md section 1 mapping).

    file_role <- level ; qualified_symbol <- name ; kind <- kind ;
    normalized_signature <- normalize_signature(signature) for operation/interface else null.
    """
    sig = node.get("signature") if node.get("kind") in ("operation", "interface") else None
    return {
        "file_role": node.get("level"),
        "qualified_symbol": node.get("name"),
        "kind": node.get("kind"),
        "normalized_signature": idmodel.normalize_signature(sig),
    }


def node_logical_id(node: Dict[str, Any]) -> str:
    """The NARROW cross-revision id (idmodel). Edges reference nodes by name; this is the key.

    It is idmodel's `spec_revision_logical_id` (file_role = level), the id that feeds the graph
    digest. It is NOT the engine/overlay join key, which is idmodel's `engine_join_id`."""
    return idmodel.spec_revision_logical_id(node)


def _node_hashed_content(node: Dict[str, Any]) -> Dict[str, Any]:
    """The meaning-bearing (hashed) subset of a node: common + per-kind formal fields.

    Envelope fields (rationale/notes/source_position) are excluded. The normalized signature is used
    (param names dropped, types canonicalized) so the WIDE digest agrees with the idmodel view of the
    signature, while still covering everything idmodel does NOT see.
    """
    kind = node.get("kind")
    content: Dict[str, Any] = {
        "kind": kind,
        "name": node.get("name"),
        "level": node.get("level"),
    }
    if "tags" in node and node["tags"]:
        content["tags"] = _nfc_sorted(node["tags"])  # schema-declared order-insensitive set
    # Common parity hooks (FORMAL): frontier only when it deviates from the default (so a node that
    # omits it hashes identically to one that sets it to the default); permitted_freedom sorted.
    if bool(node.get("frontier", FRONTIER_DEFAULT)) != FRONTIER_DEFAULT:
        content["frontier"] = bool(node.get("frontier"))
    if node.get("permitted_freedom"):
        content["permitted_freedom"] = _nfc_sorted(node["permitted_freedom"])  # order-insensitive set
    if kind in ("operation", "interface") and node.get("signature") is not None:
        content["signature"] = idmodel.normalize_signature(node["signature"])
    for field in _KIND_FORMAL_FIELDS.get(kind, ()):  # type: ignore[arg-type]
        if field == "signature":
            continue  # handled above
        if field in node and node[field] is not None:
            if field == "domain":
                content[field] = _normalize_domain(node[field])
            else:
                content[field] = node[field]
    # canon v2: apply Unicode NFC at this ingestion chokepoint (every graph-digest path — the WIDE
    # node digest and graph_digest via _graph_hashed_content — flows through here) so authored
    # identifiers/names/prose are already-NFC before canon, which no longer normalizes.
    return ingest.normalize_tree(content)


# A prohibition `domain` carries `type` (+ optional `allowed_rule`) plus order-insensitive token-set
# sub-fields. Those sets are SORTED in the hashed content (like tags/permitted_freedom) so author order
# never enters the digest; `type`/`allowed_rule` pass through unchanged. (Bare `{type}` is untouched.)
_DOMAIN_SET_SUBFIELDS = ("edge_types", "forbidden_call_targets", "kinds")


def _normalize_domain(domain: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in domain.items():
        out[k] = _nfc_sorted(v) if (k in _DOMAIN_SET_SUBFIELDS and v is not None) else v
    return out


def _nfc_sorted(tokens: Any) -> List[Any]:
    """Sort an order-insensitive token set AFTER NFC. Sorting first and normalizing afterwards (in
    normalize_tree) let a decomposed spelling sort differently from its NFC form, so the set's order
    and digest could change after one Render/Parse cycle."""
    return sorted(ingest.normalize_tree(list(tokens)))


def node_content_digest(node: Dict[str, Any]) -> str:
    """The WIDE digest: canon semantic-content over ALL formal fields of a node."""
    return canon.digest(_node_hashed_content(node), "semantic-content")


def _edge_hashed_content(edge: Dict[str, Any]) -> Dict[str, Any]:
    content: Dict[str, Any] = {
        "kind": edge.get("kind"),
        "source": edge.get("source"),
        "target": edge.get("target"),
    }
    if "tags" in edge and edge["tags"]:
        content["tags"] = _nfc_sorted(edge["tags"])
    # canon v2: NFC at the ingestion chokepoint (mirrors _node_hashed_content) so edge identifiers
    # are already-NFC before canon.
    return ingest.normalize_tree(content)


def edge_content_digest(edge: Dict[str, Any]) -> str:
    """canon semantic-content over an edge's hashed fields {kind, source, target, tags?}."""
    return canon.digest(_edge_hashed_content(edge), "semantic-content")


# --------------------------------------------------------------------------- #
# Normalize(S)  (SPEC.md section 5)
# --------------------------------------------------------------------------- #

def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(_node_hashed_content(node))
    out["logical_id"] = node_logical_id(node)
    out["content_digest"] = node_content_digest(node)
    # preserve envelope (Normalize drops nothing from the envelope), source_position excluded later
    for field in _NODE_ENVELOPE_FIELDS:
        if field in node and node[field] is not None:
            out[field] = node[field]
    return out


def _normalize_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(_edge_hashed_content(edge))
    out["content_digest"] = edge_content_digest(edge)
    for field in _EDGE_ENVELOPE_FIELDS:
        if field in edge and edge[field] is not None:
            out[field] = edge[field]
    return out


def normalize(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize(S): node/edge-level normalize + schema-declared ordering. Idempotent.

    Imposes ordering BEFORE canon sees the graph (canon preserves list order ALWAYS), so authoring
    order never leaks into the graph digest. Sorts:
      - nodes by (logical_id, content_digest, name)
      - edges by content_digest
    Preserves the envelope (rationale/notes/source_position) — they live outside hashed content.
    """
    nodes = [_normalize_node(n) for n in graph.get("nodes", [])]
    edges = [_normalize_edge(e) for e in graph.get("edges", [])]
    nodes.sort(key=lambda n: (n["logical_id"], n["content_digest"], n["name"]))
    edges.sort(key=lambda e: e["content_digest"])
    out: Dict[str, Any] = {
        "spec_graph_version": graph.get("spec_graph_version", SPEC_GRAPH_VERSION),
        "registry_version": graph.get("registry_version", REGISTRY_VERSION),
        "nodes": nodes,
        "edges": edges,
    }
    for field in ("graph_id", "rationale", "notes"):
        if field in graph and graph[field] is not None:
            out[field] = graph[field]
    return out


def _graph_hashed_content(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a NORMALIZED graph to hashed content only (SPEC.md section 2)."""
    def strip_node(n: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in _node_hashed_content(n).items()}

    def strip_edge(e: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in _edge_hashed_content(e).items()}

    return {
        "spec_graph_version": normalized.get("spec_graph_version", SPEC_GRAPH_VERSION),
        "nodes": [strip_node(n) for n in normalized["nodes"]],
        "edges": [strip_edge(e) for e in normalized["edges"]],
    }


def hashed_content(graph: Dict[str, Any]) -> Dict[str, Any]:
    """The graph's HASHED CONTENT: Normalize(S) reduced to the fields graph_digest covers (SPEC.md
    section 2), i.e. graph_digest's exact preimage. NFC-normalized, sets sorted, nodes and edges in
    Normalize order, every envelope field and derived id dropped. The parity engine grades THIS view
    (spec-parity SEMANTICS sec 9), so anything it grades is, by construction, inside graph_digest."""
    return _graph_hashed_content(normalize(graph))


def graph_digest(graph: Dict[str, Any]) -> str:
    """canon semantic-content over the NORMALIZED graph's hashed content (SPEC.md section 2).

    Accepts a raw or already-normalized graph; normalizes first (idempotent). graph_id and all
    rationale/notes/source_position envelope fields are excluded.
    """
    return canon.digest(hashed_content(graph), "semantic-content")


# --------------------------------------------------------------------------- #
# Validation (SPEC.md sections 3-4)
# --------------------------------------------------------------------------- #

# validate() has ONE rule set (0.5.0). The 0.4.0 engine-entry subset (ENGINE_SUBSET_SKIP_RULES, which
# relaxed level ordering and operation-requires-signature) is gone: the engine, the MCP broker, the
# render producers and every authoring path run the same full validation.


# --- Shape checks: the schema's per-node/per-edge field sets and scalar types, hand-rolled so the
# validator needs no jsonschema at runtime (spec-graph is vendored into a pip package). These mirror
# schema/spec-graph.schema.json; the reference test suite asserts the field sets stay equal to it.
NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*")

GRAPH_FIELDS = frozenset({
    "spec_graph_version", "registry_version", "graph_id", "rationale", "notes", "doc",
    "spec_revision_id", "nodes", "edges",
})
NODE_FIELDS = frozenset({
    "kind", "name", "level", "signature", "tags", "frontier", "permitted_freedom", "domain",
    "type_form", "members", "expression", "code", "payload_ref", "rule", "budget", "statement",
    "observable", "given", "when", "then", "rationale", "notes", "provenance", "unratified",
    "source_position",
})
EDGE_FIELDS = frozenset({"kind", "source", "target", "tags", "rationale", "notes", "source_position"})
# Normalize(S) writes these derived ids onto each node (and content_digest onto each edge). A
# normalized graph is a valid input, so validate() admits them, but only when each one equals the
# value recomputed from the object's own fields: a stale or forged id is rejected, never trusted.
NODE_DERIVED_FIELDS = frozenset({"logical_id", "content_digest"})
EDGE_DERIVED_FIELDS = frozenset({"content_digest"})
ENDPOINT_FIELDS = frozenset({"endpoint_kind", "id"})
SIGNATURE_FIELDS = frozenset({"params", "return_type"})
PARAM_FIELDS = frozenset({"name", "type"})
MEMBER_FIELDS = frozenset({"name", "type"})
SOURCE_POSITION_FIELDS = frozenset({"file", "line", "col"})
PROVENANCE_FIELDS = frozenset({"origin", "boundary_id", "clause_id"})
DOMAIN_FIELDS = frozenset({"type", "kinds", "allowed_rule", "edge_types", "forbidden_call_targets"})

# Node string fields (common envelope + per-kind formal scalars). `payload_ref` is a node NAME.
_NODE_STRING_FIELDS = ("type_form", "expression", "code", "rule", "budget", "statement", "given",
                       "when", "then", "rationale", "notes")
_GRAPH_STRING_FIELDS = ("graph_id", "rationale", "notes", "doc", "spec_revision_id")


def _check_fields(obj: Dict[str, Any], allowed: FrozenSet[str], what: str) -> None:
    extra = sorted(k for k in obj if k not in allowed)
    if extra:
        raise SpecGraphError("%s has unknown field(s) %s (allowed: %s)" % (what, extra, sorted(allowed)))


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(t, str) for t in v)


def _is_str_or_null(v: Any) -> bool:
    return v is None or isinstance(v, str)


def _is_name(v: Any) -> bool:
    return isinstance(v, str) and NAME_PATTERN.fullmatch(v) is not None


def _check_source_position(sp: Any, what: str) -> None:
    if not isinstance(sp, dict):
        raise SpecGraphError("%s: source_position must be an object {file, line, col}" % what)
    _check_fields(sp, SOURCE_POSITION_FIELDS, what + " source_position")
    if "file" in sp and not isinstance(sp["file"], str):
        raise SpecGraphError("%s: source_position.file must be a string" % what)
    for k in ("line", "col"):
        if k in sp and (isinstance(sp[k], bool) or not isinstance(sp[k], int) or sp[k] < 1):
            raise SpecGraphError("%s: source_position.%s must be an integer >= 1" % (what, k))


def _check_signature(sig: Any, what: str) -> None:
    if not isinstance(sig, dict):
        raise SpecGraphError("%s: signature must be an object {params, return_type}" % what)
    _check_fields(sig, SIGNATURE_FIELDS, what + " signature")
    if "params" in sig:
        params = sig["params"]
        if not isinstance(params, list):
            raise SpecGraphError("%s: signature.params must be a list" % what)
        for i, p in enumerate(params):
            if not isinstance(p, dict):
                raise SpecGraphError("%s: signature.params[%d] must be an object {name, type}" % (what, i))
            _check_fields(p, PARAM_FIELDS, "%s signature.params[%d]" % (what, i))
            if "name" in p and not isinstance(p["name"], str):
                raise SpecGraphError("%s: signature.params[%d].name must be a string" % (what, i))
            if "type" in p and not _is_str_or_null(p["type"]):
                raise SpecGraphError("%s: signature.params[%d].type must be a string or null"
                                     % (what, i))
    if "return_type" in sig and not _is_str_or_null(sig["return_type"]):
        raise SpecGraphError("%s: signature.return_type must be a string or null" % what)


def _check_members(members: Any, what: str) -> None:
    if not isinstance(members, list):
        raise SpecGraphError("%s: members must be a list of {name, type?}" % what)
    for i, m in enumerate(members):
        if not isinstance(m, dict) or "name" not in m:
            raise SpecGraphError("%s: members[%d] must be an object with a name" % (what, i))
        _check_fields(m, MEMBER_FIELDS, "%s members[%d]" % (what, i))
        if not isinstance(m["name"], str):
            raise SpecGraphError("%s: members[%d].name must be a string" % (what, i))
        if "type" in m and not _is_str_or_null(m["type"]):
            raise SpecGraphError("%s: members[%d].type must be a string or null" % (what, i))


def _check_domain(domain: Any, what: str) -> None:
    """The prohibition `domain` shape (spec-graph.schema.json #/$defs/prohibition_domain). A set
    sub-field must be a list of strings: a bare string would be sorted into its characters, and a
    null one is dropped by the renderer while Normalize keeps it."""
    if not isinstance(domain, dict) or "type" not in domain:
        raise SpecGraphError("%s requires a domain descriptor object {type: ...}" % what)
    _check_fields(domain, DOMAIN_FIELDS, what + " domain")
    if domain["type"] not in PROHIBITION_DOMAIN_TYPES:
        raise SpecGraphError("%s domain.type %r not in v1 set %s"
                             % (what, domain["type"], list(PROHIBITION_DOMAIN_TYPES)))
    for k in _DOMAIN_SET_SUBFIELDS:
        if k in domain and not _is_str_list(domain[k]):
            raise SpecGraphError("%s domain.%s must be a list of strings" % (what, k))
    if "allowed_rule" in domain and not isinstance(domain["allowed_rule"], str):
        raise SpecGraphError("%s domain.allowed_rule must be a string" % what)


def _check_node_shape(node: Dict[str, Any], name: str) -> None:
    what = "node %r" % name
    _check_fields(node, NODE_FIELDS | NODE_DERIVED_FIELDS, what)
    for f in _NODE_STRING_FIELDS:
        if f in node and not isinstance(node[f], str):
            raise SpecGraphError("%s: %s must be a string" % (what, f))
    if "payload_ref" in node and not _is_name(node["payload_ref"]):
        raise SpecGraphError("%s: payload_ref must be a node name matching %s"
                             % (what, NAME_PATTERN.pattern))
    if "observable" in node and not isinstance(node["observable"], bool):
        raise SpecGraphError("%s: observable must be a boolean" % what)
    if "tags" in node and not _is_str_list(node["tags"]):
        raise SpecGraphError("%s: tags must be a list of strings" % what)
    if "signature" in node:
        _check_signature(node["signature"], what)
    if "members" in node:
        _check_members(node["members"], what)
    if "domain" in node:
        _check_domain(node["domain"], what)
    if "source_position" in node:
        _check_source_position(node["source_position"], what)


def _check_node_derived(node: Dict[str, Any], name: str) -> None:
    """Run last for a node, once every field it hashes has passed its checks."""
    if "logical_id" in node and node["logical_id"] != node_logical_id(node):
        raise SpecGraphError("node %r: logical_id %r is not the id Normalize derives from the node"
                             % (name, node["logical_id"]))
    if "content_digest" in node and node["content_digest"] != node_content_digest(node):
        raise SpecGraphError("node %r: content_digest %r is not the digest of the node's hashed "
                             "content" % (name, node["content_digest"]))


def _check_edge_shape(edge: Dict[str, Any], i: int) -> None:
    what = "edges[%d]" % i
    _check_fields(edge, EDGE_FIELDS | EDGE_DERIVED_FIELDS, what)
    for end in ("source", "target"):
        ep = edge.get(end)
        if isinstance(ep, dict):
            _check_fields(ep, ENDPOINT_FIELDS, "%s.%s" % (what, end))
            if not _is_name(ep.get("id")):
                raise SpecGraphError("%s.%s.id must be a node name matching %s (got %r)"
                                     % (what, end, NAME_PATTERN.pattern, ep.get("id")))
    for f in ("rationale", "notes"):
        if f in edge and not isinstance(edge[f], str):
            raise SpecGraphError("%s: %s must be a string" % (what, f))
    if "tags" in edge and not _is_str_list(edge["tags"]):
        raise SpecGraphError("%s: tags must be a list of strings" % what)
    if "source_position" in edge:
        _check_source_position(edge["source_position"], what)


def validate(graph: Dict[str, Any]) -> None:
    """Shape + structural + registry + cross-level + reference validation. Raises SpecGraphError.

    The shape checks give it the schema's strength without jsonschema: the name pattern, no field
    outside the schema's set on a node, an edge or any object inside them, and every field's scalar
    type (see _check_node_shape / _check_edge_shape). Unknown top-level keys are tolerated (envelope,
    never hashed). A normalized graph's derived ids are admitted only when they recompute.

    There is one rule set: every caller (authoring, the engine at entry, the MCP broker, the render
    producers) validates in full."""
    if not isinstance(graph, dict):
        raise SpecGraphError("spec graph must be an object")
    if graph.get("spec_graph_version") != SPEC_GRAPH_VERSION:
        raise SpecGraphError("spec_graph_version must be %r" % SPEC_GRAPH_VERSION)
    # Top-level keys outside GRAPH_FIELDS are tolerated: they are envelope, never enter the graph
    # digest or any id, and existing callers attach their own (e.g. a `metadata` label). The known
    # top-level fields are type-checked; nodes and edges get the full field-set check.
    if "registry_version" in graph and graph["registry_version"] != REGISTRY_VERSION:
        raise SpecGraphError("registry_version must be %r" % REGISTRY_VERSION)
    for f in _GRAPH_STRING_FIELDS:
        if f in graph and not isinstance(graph[f], str):
            raise SpecGraphError("spec graph %s must be a string" % f)

    for key in ("nodes", "edges"):
        if key not in graph:
            raise SpecGraphError("spec graph requires %r (a list; use [] for none)" % key)
        items = graph[key]
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            raise SpecGraphError("%s must be a list of objects" % key)

    names: Dict[str, str] = {}  # name -> level
    for node in graph.get("nodes", []):
        kind = node.get("kind")
        name = node.get("name")
        level = node.get("level")
        if kind not in NODE_KINDS:
            raise SpecGraphError("unknown node kind: %r (registry v%s)" % (kind, REGISTRY_VERSION))
        if not isinstance(name, str) or not name:
            raise SpecGraphError("node missing required name (kind=%r)" % kind)
        if not _is_name(name):
            raise SpecGraphError("node name %r must match %s (a dotted identifier)"
                                 % (name, NAME_PATTERN.pattern))
        if level not in LEVELS:
            raise SpecGraphError("node %r has invalid level: %r" % (name, level))
        if name in names:
            raise SpecGraphError("duplicate node name: %r" % name)
        names[name] = level
        _check_node_shape(node, name)
        for field in _KIND_REQUIRED_FIELDS.get(kind, ()):  # type: ignore[arg-type]
            if node.get(field) is None:
                raise SpecGraphError("node %r (kind %r) requires field %r" % (name, kind, field))

        # Common parity-hook field types (Phase-3a hooks).
        if "frontier" in node and not isinstance(node["frontier"], bool):
            raise SpecGraphError("node %r: frontier must be a boolean" % name)
        if "permitted_freedom" in node:
            pf = node["permitted_freedom"]
            if not isinstance(pf, list) or not all(isinstance(t, str) for t in pf):
                raise SpecGraphError("node %r: permitted_freedom must be a list of strings" % name)
            # Tokens MUST be drawn from the permitted_freedom vocabulary (the single source of
            # truth). An unknown token is a validation error in v1 (additive by design — adding a
            # token is a minor vocab bump). This is what keeps spec-graph, SEMANTICS.md, and the
            # spec-engine matcher agreeing on one vocabulary instead of an ad-hoc string set.
            vocab = permitted_freedom_tokens()
            for tok in pf:
                if tok not in vocab:
                    raise SpecGraphError(
                        "node %r: unknown permitted_freedom token %r (v1 vocabulary = %s; "
                        "see spec-parity/permitted-freedom-vocab.json)"
                        % (name, tok, sorted(vocab)))

        # Draft-S provenance/unratified markers (brownfield freeze). ENVELOPE/non-hashed
        # (do not enter the digest); validated structurally so a malformed marker fails closed. Both
        # optional: a node without them is a pre-brownfield (human_authored, ratified) node.
        if "provenance" in node:
            prov = node["provenance"]
            if not isinstance(prov, dict):
                raise SpecGraphError("node %r: provenance must be an object {origin, ...}" % name)
            origin = prov.get("origin")
            if origin not in PROVENANCE_ORIGINS:
                raise SpecGraphError(
                    "node %r: provenance.origin %r not in %s"
                    % (name, origin, list(PROVENANCE_ORIGINS)))
            for k in prov:
                if k not in PROVENANCE_FIELDS:
                    raise SpecGraphError("node %r: unknown provenance sub-field %r" % (name, k))
                if k != "origin" and not isinstance(prov[k], str):
                    raise SpecGraphError("node %r: provenance.%s must be a string" % (name, k))
        # `unratified` is TRUE-OR-ABSENT (an absent marker means ratified/not-a-draft). An explicit
        # `false` is REJECTED so the invariant is enforced, not merely conventional: the truthiness-
        # based envelope render drops a falsy field while normalize preserves it, so an explicit
        # `unratified=false` would silently break the Parse(Render(S))==Normalize(S) round-trip.
        # Rejecting it closes that hole at the frozen contract (SCAF/DOCS/RATIFY pin to this).
        if "unratified" in node:
            if not isinstance(node["unratified"], bool):
                raise SpecGraphError("node %r: unratified must be a boolean" % name)
            if node["unratified"] is False:
                raise SpecGraphError(
                    "node %r: unratified is true-or-absent — omit it for a ratified node "
                    "(an explicit `false` would break the DSL round-trip)" % name)

        # Prohibition closed-world domain (Phase-3a SEMANTICS.md section 4.1): required object {type}
        # whose type is one of the v1 domain types. A prohibition without one is `unsupported`.
        if kind == "prohibition":
            _check_domain(node.get("domain"), "prohibition %r" % name)
        _check_node_derived(node, name)

    for i, edge in enumerate(graph.get("edges", [])):
        _check_edge_shape(edge, i)
        ek = edge.get("kind")
        if ek not in EDGE_KINDS:
            raise SpecGraphError("unknown edge kind: %r" % ek)
        src = _endpoint_id(edge.get("source"))
        tgt = _endpoint_id(edge.get("target"))
        if src not in names:
            raise SpecGraphError("edge %r source references unknown node %r" % (ek, src))
        if tgt not in names:
            raise SpecGraphError("edge %r target references unknown node %r" % (ek, tgt))
        if "content_digest" in edge and edge["content_digest"] != edge_content_digest(edge):
            raise SpecGraphError("edges[%d]: content_digest %r is not the digest of the edge's "
                                 "hashed content" % (i, edge["content_digest"]))
        if ek == "decomposes_to":
            # Strictly descending: the target sits at a strictly LOWER level than
            # the source in contract->architecture->detailed. Skipping a level (contract->detailed)
            # is valid; same-level and upward decomposition are not.
            si, ti = _LEVEL_INDEX[names[src]], _LEVEL_INDEX[names[tgt]]
            if ti <= si:
                raise SpecGraphError(
                    "decomposes_to %r -> %r must descend to a strictly lower level in order "
                    "contract->architecture->detailed (got %s -> %s)"
                    % (src, tgt, names[src], names[tgt])
                )


def _endpoint_id(endpoint: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(endpoint, dict):
        raise SpecGraphError("edge endpoint must be an object {endpoint_kind, id}")
    if endpoint.get("endpoint_kind") != "node":
        raise SpecGraphError("v1 endpoints are node-only (got %r)" % endpoint.get("endpoint_kind"))
    return endpoint.get("id")
