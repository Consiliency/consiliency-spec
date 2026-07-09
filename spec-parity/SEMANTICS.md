# Parity Semantics — formal contract for `P(E(C),S)` and `N()`

Status: **NORMATIVE**, frozen for v1. Owned by `spec`
(Consiliency). This is the design/contract step that MUST precede any projection code.

This document, together with the schemas in `spec-parity/schemas/` and the data file
`spec-parity/kind-alignment.json`, defines the parity semantics **completely enough that someone else can
implement `P` and `N` with no further decisions except those explicitly marked `OPEN`.** It builds on
**canon v1** (`../canon/SPEC.md`) and **idmodel v1** (`../idmodel/SPEC.md`) and does not restate their rules;
it cites them.

The functions being defined:

```
P(E(C), S)  -> projection      # projects realized Boundary IR E(C) onto the frontier selected by S
N(projection) -> finding_set + certificate   # the 5-dimension parity checker; deterministic, NEVER LLM-graded
```

`N(S) = N(P(E(C),S))` is the parity result. `N` emits a machine-readable `finding_set`
(`schemas/result-state.schema.json`) and a `certificate` (`schemas/certificate.schema.json`).

---

## 0. Inputs, identity, and the non-negotiables

- **`S`** — a desired-state graph authored in the Phase-2 metamodel: nodes of kind `component | capability |
  interface | operation | type | state | invariant | error | event | security_rule | performance | prohibition |
  acceptance_criterion`, plus decomposition edges. Content-addressed by idmodel v1.
- **`E(C)`** — the realized Interface Boundary IR (greenfield) and/or treesitter-chunker boundary/chunk
  output, consumed through Phase-1's cleaned API. Nodes carry Tier-2 occurrence ids (idmodel sec 5); their
  Tier-1 logical ids are computed per idmodel sec 3.
- **Identity is idmodel's.** Desired entities are keyed by Tier-1 `logical_id`; realized facts carry Tier-2
  `occurrence_id`. Cross-revision continuity is the **correspondence map** (idmodel sec 6), never a raw hash.
- **Determinism.** Every output of `P` and `N` is content-addressed (canon) and reproducible byte-for-byte
  for fixed inputs + fixed `projection_algo_version`. **No floats anywhere** (canon sec 6): all confidence /
  threshold / score fields are string-decimals or scaled ints.
- **Never silently green.** The result-state machinery (sec 6) exists so that a fact the extractor cannot
  observe yields `unknown`, never a silent `pass`.

---

## 1. Frontier selection

The **frontier** is the explicit altitude at which parity is checked. Soundness and completeness are
well-posed only *against the frontier* — without it, "is the code complete vs the spec" is unanswerable
because every implementation has detail the spec never mentions.

### 1.1 What the frontier is

The frontier is the set of **desired-graph nodes that are in-scope for a parity run**, together with the
realized facts that correspond to them at or above that altitude. Formally:

```
Frontier(S, run) = { desired node d in S : in_scope(d, run) == true }
```

Everything in `S` is *potentially* in-scope; the run declares which subset is actually checked.

### 1.2 How the frontier is declared/derived (the deterministic rule)

Frontier membership is the **disjunction** of three deterministic sources, evaluated in this order; a node is
in-scope if ANY yields true:

1. **Explicit run selector (highest precedence).** A run MAY carry a `frontier_selector` = an ordered list of
   desired `logical_id`s (and/or a kind filter). If present and non-empty, the frontier is exactly the
   declared nodes **plus their decomposition descendants** (sec 1.3). An empty/absent selector falls through.
2. **Node-level `frontier` flag.** A desired node MAY declare `frontier: true` in its metamodel attributes.
   All such nodes (plus descendants) are in-scope. This is the default authoring mechanism.
3. **Default closure (fallback).** If neither (1) nor (2) selects anything, the frontier is **every
   `capability` node and every `component`/`interface`/`operation` reachable from a `capability` via
   decomposition edges.** Leaf `type`/`field` detail below the lowest declared node is NOT auto-in-scope (it
   is refinement-below-frontier, sec 3) unless explicitly named.

The chosen source and the resolved node set are recorded in the projection so the run is auditable.

### 1.3 Frontier closure over decomposition

When a node is in-scope, its **decomposition descendants** (children via `spec` decomposition edges,
transitively) are in-scope **up to the next node that itself declares an explicit frontier** (a child marked
`frontier:true` becomes its own boundary; you do not check *through* it twice). This makes the frontier a
clean cut through the desired graph: above the cut = checked; below = refinement-below-frontier (sec 3).

### 1.4 Edges targeting subgraphs — `OPEN-3`

An open metamodel question leaves open "may edges target subgraphs?" (subgraph taxonomy). This shapes
whether a frontier node can be a *subgraph* rather than a single node. **v1 decision:** the frontier is a set
of **nodes** only; an edge whose endpoint is a subgraph is resolved to that subgraph's **root node** for
frontier purposes. If the metamodel later admits subgraph endpoints as first-class, revise sec 1.3. Marked
`OPEN-3`.

---

## 2. Correspondence: matching desired nodes to realized facts

Before any dimension can run, `P` must decide, for each in-scope desired node `d`, which realized facts (if
any) correspond to it. This uses idmodel:

- **Primary:** the idmodel **correspondence map** (idmodel sec 6) entry whose continuity line contains `d`'s
  Tier-1 `logical_id`, resolved to the realized `occurrence_id`(s) for the run's revision.
- **Fallback (no correspondence entry):** compute the realized fact's Tier-1 `logical_id` and match by equality
  to `d`'s `logical_id` for the same revision (idmodel sec 3).
- **Kind gate:** a correspondence is only parity-bearing if the realized kind aligns to `d`'s desired kind per
  `kind-alignment.json` (sec 5). A correspondence across a kind that does not align records
  `ambiguous_kind_alignment` and yields `unknown` for kind-dependent checks.

The result of `P` is a **projection**: for each in-scope `d`, a `match` record = `{desired d,
realized_occurrences[], match_origin (correspondence|logical_id|none), confidence}` — confidence as a
string-decimal.

---

## 3. Allowed-freedom below the frontier

Realized code legitimately contains structure the spec does not mention. Classifying that structure correctly
is what stops the soundness/closure dimensions from drowning in false positives.

A realized fact that lies **below** an in-scope frontier node (i.e. it is a decomposition descendant, in the
realized graph, of a matched frontier node, and is not itself matched to any in-scope desired node) is
classified as exactly one of:

- **`refinement_below_frontier`** — realized detail that *implements* a frontier node without contradicting
  it: private helpers, internal types, sub-operations, bodies. **Allowed; never a soundness violation.** The
  spec deliberately does not constrain it. (idmodel makes this concrete: a **body-only change keeps the
  Tier-1 id stable** — the body is not part of the key, idmodel sec 2 — so body detail is below-frontier by
  construction.)
- **`permitted_freedom`** — realized structure the spec explicitly *permits to vary*: an `interface` the spec
  marks `open`/extensible, an enum the schema declares order-insensitive, an implementation-choice point. The
  desired node must *declare* the permission (via its `permitted_freedom` token set); otherwise it is not
  permitted_freedom.

  **Token vocabulary (the authority).** The exact set of `permitted_freedom` tokens, and the PRECISE
  operational rule each one carries, is pinned by **`spec-parity/permitted-freedom-vocab.json`** (schema:
  `schemas/permitted-freedom-vocab.schema.json`) — the single source of truth across all three layers
  (`spec-graph` validates declared tokens against it; `spec-engine`'s soundness matcher dispatches on each
  token's `rule_kind`). The v1 vocabulary is **closed** (an unknown token is a `spec-graph` validation error,
  and in the engine an in-memory unknown token is `unsupported` — never silently honored) but **additive by
  design** (adding a token + its `rule_kind` handler is a minor vocab bump; re-meaning/removing one is major).
  The v1 tokens:

  | token | `rule_kind` | what realized deviation BELOW the frontier it permits (without being a soundness violation) | how the checker treats it |
  |---|---|---|---|
  | `open_interface` | `allow_signature_superset` | On a **matched** `interface`/`operation`, a realized normalized signature that is a SUPERSET-BY-EXTENSION of the desired one: identical `return_type`, desired param types are a PREFIX of the realized param types (realized may APPEND params, never drop/reorder/retype a declared one or change the return type). | When the realized signature is a strict trailing extension → a `permitted_freedom` finding with `result_state: pass` (the deviation is recorded as permitted, not dropped), and the soundness check is `pass`. A non-extension deviation does NOT fire the token → ordinary `structural_mismatch` `fail`. |
  | `order_insensitive_enum` | `order_insensitive_members` | On a **matched** `type` (enum/record) carrying a `members` list, a realized `members` list that equals the desired one **as a set** (membership ignoring order). Membership is still pinned — a member added/removed is NOT permitted. | Equal set + different order → `permitted_freedom` `pass`; equal set + equal order → ordinary `pass` (no finding); different membership → `structural_mismatch` `fail`; realized side carries no `members` (no evidence) → `unknown` (never a silent pass). |

  A `permitted_freedom` classification is **never** applied to a fact that VIOLATES an in-scope
  constraint/prohibition projected downward (next paragraph) — a token excuses spec-permitted *variation*, not
  a violation.

A realized fact below the frontier that is **neither** — i.e. it sits below the frontier but **violates a
constraint that the frontier node or an in-scope `prohibition` projects downward** — is NOT excused by being
below the frontier. It is a soundness/prohibition finding (sec 7). Example: a frontier `interface` says
"no I/O"; a private helper below it does network I/O → that is a violation, not refinement.

**Rule (deterministic):** a below-frontier fact is `refinement_below_frontier` by default; it is
`permitted_freedom` only if a covering desired node declares the permission; it is a finding only if a covering
in-scope constraint/prohibition is violated. This classification is what the **closure** dimension audits
(sec 7): nothing in-scope may be left unclassified.

---

## 4. Closed-world prohibition domains

A prohibition is a desired assertion that something MUST NOT exist/happen. Checking a prohibition soundly
requires a **bounded fact domain** — you can only conclude "this does not exist" over a domain you have fully
observed. This is the load-bearing mechanism behind `unknown` (validation #8).

### 4.1 Each prohibition declares its closed-world domain

A `prohibition` node in `S` MUST carry a `domain` descriptor naming the **bounded set of facts** over which it
is decided. A domain is one of the v1 domain types (extensible):

| domain type | bounded fact set | example |
|---|---|---|
| `edge_set` | the realized edges of given type(s) within the frontier subgraph | "no `calls` edge to module X" |
| `node_kind_set` | realized nodes of given kind(s) within the frontier subgraph | "no new `interface` beyond the declared set" |
| `signature_facts` | normalized signatures (idmodel sec 4) of matched operations | "no operation takes a raw socket" |
| `capability_registration` | the realized facts that register a capability/handler/store | pmcp "no new gateway tool/handler/store" |
| `external` | a fact domain the current extractor does NOT model | (always yields `unknown`, see 4.3) |

The domain pins **exactly which realized facts are in evidence** for that prohibition. A prohibition without a
declared domain is invalid (`unsupported`, recorded as a metamodel error) — never assumed global.

### 4.2 The three-way decision rule (verbatim)

For prohibition `p` with declared domain `D`:

```
coverage(D, E(C)) = "full"    if every fact slot in D is observable in E(C)
                  = "partial" otherwise   (at least one slot the extractor cannot observe)

decide(p):
  if exists an observed fact in E(C) that VIOLATES p            -> fail
  else if coverage(D, E(C)) == "full"                           -> pass
  else  (no violation observed, coverage partial)               -> unknown   # NEVER pass
```

A violating fact always wins (a single observed violation is `fail` even on partial coverage). Absence of a
violation is `pass` **only** under full coverage; otherwise `unknown`. This is the construction that makes
"unobservable → unknown" enforceable rather than aspirational.

### 4.3 Prohibitions the extractor cannot observe

The `capability_registration` and `external` domains are precisely the ones that bite pmcp's Non-Goals ("no
new gateway tool/handler/store") — these are not direct Interface-Boundary facts. If the Phase-1 `E(C)`
extractor does not surface registration facts for the relevant subgraph, `coverage` is `partial` → the
prohibition returns **`unknown`**, recorded as `prohibition_unobservable`. It is **never** a silent `pass` or
green. When the extractor is later extended to surface those facts, the same prohibition can resolve to
`pass`/`fail` with no metamodel change.

### 4.4 The `edge_set` forbidden-call-target scan (the concrete `edge_set` scanner)

An `edge_set` domain says "no edge of these type(s) does X within the frontier subgraph." The most common
instance — and the one the in-anger determinism prohibition uses — is a **forbidden-call-target** assertion:
*the export module MUST NOT call wall-clock time (`new Date()` / `Date.now()`).* To be DECIDED (rather than
left `prohibition_domain_unscanned` → `unsupported` for want of a scanner, sec 7.4), such a domain MUST pin a
**forbidden predicate**, and the engine MUST run the scan below.

**Domain descriptor (the forbidden predicate).** An `edge_set` domain that is to be scanned carries:

- `edge_types` — the realized edge type(s) the domain ranges over (e.g. `["calls"]`).
- `forbidden_call_targets` — an explicit, ordered list of **forbidden target tokens** (e.g. `["new Date",
  "Date.now", "Date"]`). These are matched against the edge's realized **target token** (the callee, as the
  extractor records it). Matching is **exact token equality** against the list (NOT a loose substring — so
  `validateDateRange` does not false-positive and `new Date` is not missed). An `edge_set` domain with NO
  `forbidden_call_targets` (a bare `{type: edge_set, edge_types: [...]}`) declares no decidable assertion and
  stays `prohibition_domain_unscanned` → `unsupported` (the checker gap is real; sec 7.4).

**The scan (the bounded fact domain `D`).** `D` = every edge of a type in `edge_types` whose `from` endpoint
shares a **semantic_path with a boundary in the prohibition's GOVERNED subgraph** — the realized boundaries
the prohibition is `governed_by`-attached to (sec 10.1), expanded to their realized descendants (the bodies).
This is sec 4.1's "within the frontier subgraph" made precise for a *governed* prohibition: the domain is
bounded to **what the prohibition is attached to**, NOT the entire frontier. (An export-determinism
prohibition `governed_by` `exportBundle` + `canonicalize` scans those two functions' call edges — ~9 edges in
the real graphbase run — not unrelated frontier methods like `Graph.addNode`.) For each domain edge, the
**fact slot** the prohibition reads is the edge's **target token** (the callee identifier). The decision then
follows sec 4.2:

```
violation  := exists an edge in D whose target token ∈ forbidden_call_targets        -> fail
coverage(D) = "full"   iff every edge in D carries an OBSERVABLE target token         (sec 4.2 slot rule)
            = "partial" iff any edge in D has a missing/empty/non-token target slot
decide: violation -> fail ; else full -> pass ; else partial -> unknown (prohibition_unobservable)
```

**The coverage carve-out (load-bearing, why `edge_set` is observable here despite `unresolved` edges).** Sec 5
degrades a check that **depends on resolving an edge to a boundary** to `unknown` when the edge is
`resolution = unresolved|ambiguous`. A forbidden-call-target scan does **NOT** depend on edge *resolution* —
its fact slot is the **target token**, not the resolved boundary. The treesitter-chunker extractor records the
target token for an unresolved/ambiguous call (e.g. `JSON.stringify`, `Object.keys`, `new Map` all surface as
`calls` edges with `resolution: unresolved` but an intact `to` token), so a `new Date` call would surface its
token identically. Therefore, **for a target-token scan, coverage is full iff every domain edge carries an
observable target token — edge `resolution` status does NOT reduce coverage.** This carve-out is *scoped to the
token-presence scan*; it is NOT a blanket "unresolved edges are fully observable." A scan that needed the
resolved callee boundary (e.g. "no call INTO module X" by boundary identity) would still take sec 5's
`unresolved`/`ambiguous` → `unknown` degradation. (Empirically: over the real graphbase governed export
subgraph — `exportBundle` + `canonicalize`, 9 `calls` edges — every edge carries an observable target token (0
missing) and none is a Date token, so the in-anger determinism prohibition decides `pass` under full coverage.
Had any domain edge carried a missing/computed target token, coverage would be partial → `unknown`, never a
silent pass.)

**Membership is by SEMANTIC PATH, not boundary_id (the occurrence-split guard).** tsc emits the SAME source
symbol at one `semantic_path` as **multiple occurrence nodes** (e.g. a declaration + an overload), each with a
distinct `@<content-hash>` `boundary_id`. The correspondence map (sec 2) matches the desired node to **one**
occurrence, but the call edges may fire from a **sibling** occurrence at the same path (the implementation that
carries the body). Scoping the domain by `boundary_id` alone would silently DROP that sibling's edges — and in
the real graphbase run that sibling is exactly `bundle/canonicalize.ts::canonicalize` (the determinism-critical
function the prohibition is about): its matched occurrence carried no call edges while its sibling occurrence
emitted all four (`Object.keys`/`Array.isArray`/`sort`/`value.map`). A boundary_id-scoped scan would therefore
have reported `pass` **without ever scanning canonicalize** — a silent green over an unobserved-but-governed
slot. So `D` membership is by the GOVERNED boundaries' **semantic_paths**: an edge whose `from` boundary shares
a semantic_path with any governed boundary (or its descendant body) is in the domain (a node lacking a
semantic_path falls back to its boundary_id, so it is never excluded). This reconciles the occurrence split so
every edge of a governed symbol is scanned.

**Non-vacuity guard.** A `pass` over an EMPTY scanned set would be a vacuous (false) green. The scanner
therefore asserts the scanned domain is **non-empty** (at least one in-domain edge of a declared `edge_type`);
if the domain descriptor names `edge_types` that match no in-scope edge at all, the domain is not actually
observed and the prohibition is `unknown` (`prohibition_unobservable`), not a silent `pass`.

---

## 5. Kind-alignment matrix

The mapping from realized kinds (greenfield Interface Boundary IR kinds + treesitter-chunker boundary/chunk
kinds) to spec desired kinds is the **data file `spec-parity/kind-alignment.json`** (schema:
`schemas/kind-alignment.schema.json`). That file is the single source of truth; this section narrates it and
pins the handling rules.

- **Exact / approximate / edge_evidence** alignments are defined in the matrix. Edges (`imports|calls|
  dependencies`) are `edge_evidence` — consumed as evidence for interface/operation/component checks, never as
  desired nodes. An edge with `resolution = unresolved|ambiguous` degrades the dependent check to `unknown`
  (the fact is not fully observable).
- **Unmapped realized kind** (expected often — chunker kinds are per-grammar/open-set across 47 languages): it
  bears completeness against no desired kind; kind-dependent soundness checks return `unsupported` (the checker
  has no aligned desired kind), never `pass`/`fail`; it is recorded as `unmapped_realized_kind`; it remains
  eligible for closure classification (sec 3).
- **Wrapper kind** (`alignment: "wrapper"` in the matrix — e.g. treesitter-chunker `decorated_definition`
  (python) and `export_statement` (js)): a *syntactic* wrapper the extractor emits ALONGSIDE the wrapped inner
  node (the real def/class/function), confirmed empirically against tsc 3.0.0 — a `decorated_definition` is
  emitted at the SAME `semantic path` as its wrapped `method`/`class`. The wrapper carries no identity of its
  own; the inner node already bears identity + kind + signature. A wrapper is therefore **coalesced to its
  inner node** (`coalesce: inner_by_semantic_path`): when an in-scope realized node shares the wrapper's
  `provenance.semantic_path` and has a *mapped non-wrapper* kind, the wrapper is **NOT counted as an
  `unmapped_realized_kind` and produces NO soundness `unsupported`** — the wrapped node carries the
  soundness/identity. Projection records the `(wrapper, inner)` pair in `coalesced_wrapper_kinds` so the
  coalescing is visible, never silent. **Fallback (the safety guard):** a wrapper kind with NO coincident
  mapped inner is NOT coalesced — it falls through to the unmapped-realized-kind rule above (→ `unsupported`),
  so a lone wrapper never silently vanishes and never silently passes. (`export_statement` in tsc 3.0.0 sits at
  the file-level `semantic path` with no own symbol, so it does not coincide with its exported function and
  takes this fallback — the safe direction; in the realized sample it is out-of-frontier regardless.) A
  coalesced wrapper remains eligible for closure classification (sec 3).
- **Unmapped desired kind** (a future metamodel kind): never silently dropped; its checks return `unsupported`,
  recorded `unmapped_desired_kind`.
- **Ambiguous mapping** (e.g. greenfield `property` → `type` or `operation`): the desired graph's own declared
  kind, resolved via correspondence, wins; if undecidable → `unknown` + `ambiguous_kind_alignment`.

### 5.1 Greenfield realized kinds — `OPEN-1` RESOLVED

Greenfield's node-`kind` values are now reconciled against the **published closed set** in
`greenfield/interface_boundary.py:27-29` (`SYMBOL_NODE_KINDS = frozenset({module, class, interface, struct,
trait, function, method})`), confirmed via `IF-0-PUBKINDS-1`. The greenfield rows in `kind-alignment.json` are
flagged `CONFIRMED` and cover **exactly** that set. Versus the prior core-graph NodeKind proxy this **added
`struct`** (greenfield emits it for Go/Rust record types — `struct → type`, mirroring the tsc-source `struct →
type` and protobuf `message → type` rows) and **dropped the dead `field`/`property` rows** (greenfield never
emits them; the `field_declaration`/`property_declaration` tsc-source and protobuf `field` spellings remain
under their own `realized_source` keys). A greenfield kind outside this closed set still uses the unmapped-kind
fallback. `OPEN-1` is **resolved**.

---

## 6. Result states

The verbatim v1 contract enum (`schemas/result-state.schema.json#/$defs/result_state`):

```
pass | fail | unknown | unsupported | not_applicable
```

### 6.1 Per-check definitions (precise)

| state | when it applies |
|---|---|
| **pass** | The check is **in-scope and applicable**, the required fact was **observed**, and there is **no violation**. (For prohibitions: no violation AND full closed-world coverage, sec 4.2.) |
| **fail** | An in-scope violation was **observed**: a structural mismatch against an in-scope desired element; a prohibition violated; a missing capability/required element. |
| **unknown** | The check is **in-scope and applicable**, but the required fact was **NOT OBSERVABLE** in this `E(C)` (data absent). This is the anti-silent-pass guard: partial closed-world coverage (sec 4.2), an `unresolved`/`ambiguous` edge a check depends on, or a correspondence the map cannot resolve. *Distinct from `unsupported`: the question is askable, the answer is just not in evidence.* |
| **unsupported** | The **checker** cannot ask the question — a capability gap in `P`/`N`: an unmapped desired kind, an unmapped realized kind on a kind-dependent check, a dimension not implemented for this entity class, a prohibition with no declared domain. *Distinct from `unknown`: the data might exist, but the checker has no way to evaluate it.* |
| **not_applicable** | No subject exists, so the check is vacuous. E.g. the `prohibition` dimension on an entity that declares no prohibition; `revision_alignment` on a first revision (no prior to align to). Labeled explicitly so it is **not** miscounted as an evaluated `pass`. |

**The `unknown` vs `unsupported` line (the discriminator a reviewer most needs):** *data absent in this E(C)*
→ `unknown`; *checker cannot ask the question at all* → `unsupported`. "We don't know yet" vs "we can't ask."

### 6.2 Each check produces exactly one state

A "check" is one (dimension, subject) pair. `N` evaluates every in-scope check and emits one `result_state`
per check as a `finding` (when not `pass`) and rolls them up per dimension (sec 6.3) and overall (sec 6.4).

### 6.3 Per-dimension rollup

Each of the five dimensions reports one `result_state` = the rollup of its checks, using the **same severity
order** as sec 6.4: `fail` if any check fails; else `unknown` if any unknown; else `unsupported` if any
unsupported; else `pass`; `not_applicable` only if the dimension had **no subject at all**.

### 6.4 Overall aggregation rule (pinned — a reviewer would otherwise invent it)

```
overall_result_state =
  not_applicable  if the frontier (sec 1) selected NO in-scope subject at all
                  (every dimension is not_applicable)   # degenerate / empty-frontier guard
  else fail       if any dimension == fail
  else unknown    if any dimension == unknown
  else unsupported if any dimension == unsupported
  else pass
  # not_applicable on an INDIVIDUAL dimension (when other dimensions did have subjects)
  # is NEUTRAL: it never raises or lowers the overall state.
```

**Empty-frontier guard (load-bearing):** a run whose frontier selects nothing — `S` has no capabilities, no
`frontier:true` node, no run selector, so even the sec-1.2(3) default closure is empty — has **zero in-scope
checks**. Aggregating zero checks must NOT fall through to `pass`: a green certificate over no checks is
exactly the silent-green this contract forbids. Such a run returns `overall_result_state = not_applicable`
(the certificate is explicitly "nothing was in scope"), never `pass`. `pass` is reserved for a run where at
least one dimension had a real subject and every in-scope check was clean.

Net effect: **any of `fail` / `unknown` / `unsupported` makes the certificate non-green**, and an empty run is
`not_applicable` (also non-green) — consistent with "never silently green." A green (`pass`) certificate means:
at least one check was in scope, and every in-scope check was askable, observable, and clean. The certificate
carries BOTH the `overall_result_state` and the per-dimension `dimension_results` (sec 9).

> `OPEN-4`: severity ordering of `unsupported`. v1 makes `unsupported` non-green (below `unknown` in
> severity). Rationale: a checker gap on an in-scope check is a real "we didn't verify this" and should not
> read as green. If consumers prefer `unsupported` to be neutral (green-eligible) for genuinely
> out-of-checker-scope kinds, that is a 0C/4 gating-policy decision. Marked `OPEN-4`.

---

## 7. The five parity dimensions

Each dimension below has (a) a one-sentence operational definition, (b) what it checks, and (c) its allowed
result-state range. All five ALWAYS appear in `dimension_results` (a dimension with no subject reports
`not_applicable`).

| dimension | operational definition | allowed states |
|---|---|---|
| **completeness** | Every in-scope desired element has a corresponding realized fact. | pass · fail · unknown · unsupported · not_applicable |
| **soundness** | Every realized fact at/above the frontier that contradicts an in-scope desired element is flagged; allowed-freedom (sec 3) is excused. | pass · fail · unknown · unsupported · not_applicable |
| **closure** | Every in-scope realized fact at/above the frontier is classified — matched, refinement_below_frontier, or permitted_freedom; nothing in-scope is left unclassified. | pass · fail · unknown · unsupported · not_applicable |
| **prohibition** | Every in-scope `prohibition` is decided over its closed-world domain (sec 4). | pass · fail · unknown · unsupported · not_applicable |
| **revision_alignment** | `E(C)`'s revision and `S`'s pinned revision resolve through the idmodel correspondence map; lifecycle is consistent. | pass · fail · unknown · unsupported · not_applicable |

### 7.1 completeness
For each in-scope desired node `d`, if `P` found at least one parity-bearing realized correspondence → the
element is present. **fail** if a required `d` has no correspondence (`missing_desired_element`; for a
`capability` with no realized decomposition, `capability_missing`). **unknown** if correspondence is
unresolvable (map says `ambiguous`, or the only candidate is via an `unresolved` edge). **unsupported** if `d`
is an unmapped/no-realized-path kind (sec 5 / `kind-alignment.json` `no_realized_mapping`). **not_applicable**
if the frontier selected no completeness-bearing nodes.

### 7.2 soundness
For each realized fact at/above the frontier corresponding to a `d`, compare structure (kind alignment,
normalized signature per idmodel sec 4, declared constraints). **fail** on `structural_mismatch` or a violated
in-scope constraint. Allowed-freedom (sec 3) is **excused** (never a soundness fail). **unknown** if the
comparison depends on a fact not observed (e.g. signature normalization needs a type the extractor left null
where the spec is typed). **unsupported** for unmapped realized kinds on a kind-dependent comparison.

### 7.3 closure
Audits sec 3: every in-scope realized fact at/above the frontier is in exactly one class. **fail** if a fact
at/above the frontier is **unclassifiable** (neither matched, nor refinement, nor permitted, nor a clean
finding) — that is a projection/coverage hole, not a clean result. **unknown** if classification depends on an
unobserved decomposition edge. **pass** when the partition is total.

### 7.4 prohibition
Runs sec 4.2 per in-scope `prohibition`. **fail** on observed violation; **pass** on no-violation-under-full-
coverage; **unknown** (`prohibition_unobservable`) on no-violation-under-partial-coverage; **unsupported** if a
prohibition declares no domain; **not_applicable** if no in-scope prohibition exists.

### 7.5 revision_alignment
Resolves `E(C)`'s revision and `S`'s pinned revision through the correspondence map. **pass** if continuity
resolves with a consistent lifecycle. **fail** if the map asserts an incompatible lifecycle (e.g. the desired
element is `deleted` in the spec revision but realized as present, or vice-versa with a `superseded` conflict).
**unknown** if the relevant correspondence lifecycle is **`ambiguous`** (idmodel sec 6) — ambiguity is exactly
"we cannot tell," → `unknown`, never `pass`. **not_applicable** on a first revision (no prior to align to).
**unsupported** if no correspondence map is supplied for the run.

---

## 8. Waiver / suppression lifecycle

Schema: `schemas/waiver.schema.json`. A waiver records **accepted drift** without letting it silently pass.

**Core rule (load-bearing):** a waiver **NEVER flips a finding/dimension to `pass`.** The underlying
`result_state` stays `fail`/`unknown`; the finding is marked `waiver_ref`, and the certificate records it as
**waived**. Suppression changes only how the result is *gated/presented* downstream, never the measured truth.
This mirrors the `unknown` philosophy: never overwrite "we don't know / it failed" with green.

- **Scope** (`waiver.scope`) — at least one selector; matches a finding iff every present selector matches.
- **suppressed_states** — a subset of `[fail, unknown]` only. A waiver can **never** suppress `unsupported`
  (that is a checker gap, fix the checker — not accepted drift) and never applies to `pass`/`not_applicable`.
- **expiry** — a bounded ISO date (`YYYY-MM-DD`), end-of-day UTC. **No open-ended waivers.** A fixed date is a
  deterministic, hashed semantic field (NOT a wall-clock). After expiry the waiver lifecycle is `expired` and
  it **stops suppressing** — the finding gates again.
- **approver_ref** — a reference to the governance approval (ratification id / portal governance_event_id),
  not a name/timestamp. Wall-clock of approval is provenance → `locator` (non-hashed, canon sec 10).
- **waiver_id** — canon `semantic-content` digest over the hashed content fields. Identifies the waiver by
  meaning; changing the reason/scope/expiry mints a new waiver (forces a fresh approval trail).
- **lifecycle** — `proposed → active → expired|revoked`. Only `active` (and unexpired) waivers suppress.

A waiver does not change `overall_result_state` computed from raw results; instead the certificate's
`waivers_ref` lets a downstream gate decide whether to treat a waived `fail`/`unknown` as blocking. The raw
truth is always preserved in `dimension_results`/`findings`.

---

## 9. Certificate field set

Schema: `schemas/certificate.schema.json`. The certificate is a **digest-bearing record** under the canon
`certificate` profile (`spec-canon:v2:certificate\n`); its top-level `digest` is excluded from its own
preimage (canon sec 8) and **is the certificate's authoritative identity**. There is deliberately **no
separate hashed `certificate_id` field** — a hashed id would sit inside its own preimage and be circular; a
non-hashed `certificate_id` mirror of `digest` may live in `locator` for storage indexing. **Produce once,
store verbatim** (canon sec 9).

A certificate MUST pin (union of the Storage `certificates` record shape and Phase-3a task item 8):

- **`schema_version`** — the **certificate schema version** (currently `"1"`, a `const` in
  `certificate.schema.json`). This IS the certificate-contract version: it is a hashed, meaning-bearing
  field inside the certificate's own preimage, so the version travels with — and is byte-pinned by — the
  certificate's `digest`. It is deliberately named `schema_version` (not `certificate_schema_version`):
  renaming it would change every existing certificate's frozen preimage and break produce-once/verbatim
  replay. Bump it only on a breaking change to the certificate field set.
- **`ec_revision_id`** — the realized-side revision this certificate is about (distinct from the spec
  revision, which `spec_revision_digest` pins).
- **`spec_revision_digest`** — pins the exact spec/desired revision.
- **`desired_graph_digest`** — canon `semantic-content` digest of `S`.
- **`ec_digest`** + **`ec_reproducible`** — the `E(C)` digest and whether it is byte-reproducible. A
  certificate over a non-reproducible `E(C)` is **advisory** (`ec_reproducible:false`) and not gating
  (Benchmark C: parity over non-reproducible `E(C)` is meaningless).
- **`code_head_sha`** — the code HEAD the extract came from.
- **`canon_version`, `idmodel_version`, `kind_alignment_version`, `projection_algo_version`** — every input
  whose change can change the result is pinned, so a certificate is reproducible and two certificates that
  legitimately differ can be told apart by version. **`canon_version` is `v2` since NFCBOUNDARY** (canon
  relocated Unicode NFC out of the hash to the ingestion boundary; digest prefix `spec-canon:v1:`→
  `spec-canon:v2:`). This is a **certificate-version boundary**: every cert digest changed at v1→v2 by
  construction (the prefix is in the preimage), but **no parity verdict changed** — only the digests and
  `canon_version` moved. `idmodel_version` / `projection_algo_version` are UNCHANGED, so a v2 cert is
  distinguishable from a v1 cert by `canon_version` alone. **Historical-cert policy:** a v1 certificate
  stays valid *as a v1 record*; a v1 digest is never compared against a v2 digest; a mixed-`canon_version`
  graph is rejected at the consumer (never silently re-hashed). See `canon/SPEC.md` §9. Downstream
  consumers cut over to v2 wholesale (metadata-only notification; OPEN-5).
- **`overall_result_state`** + **`dimension_results`** (all five) — per sec 6. **Invariant:** these are
  byte-equal (after canon canonicalization) to the referenced `finding_set`'s `overall_result_state` /
  `dimension_results` — the certificate copies, never recomputes, them. An implementer derives the embedded
  copies from the `finding_set` at `findings_ref`; they must not drift.
- **`permitted_freedom_vocab_version`** — version of `permitted-freedom-vocab.json`; a vocab change can
  excuse (or stop excusing) a below-frontier deviation, so it is pinned alongside the other input versions.
- **`findings_ref`** — digest pointer to the `finding_set` (pointer, not payload; portal `metadata_only`).
- **`waivers_ref?`** — digest pointer to the waivers in effect (waived findings stay non-pass).
- **`digest`** — the certificate-profile content digest (the certificate's authoritative identity).
- **`locator`** — non-hashed envelope (wall-clock, producer, run_id, optional `certificate_id` mirror).

### 9.1 Digest byte-match domain + gate type — `OPEN-5`
An open consumer-integration question leaves open, for a later phase: (a) the **digest byte-match domain** between `spec`'s
certificate emitter and downstream verifiers, and (b) **`parity-gate` vs `policy-gate`**
checkpoint_type. The certificate schema is built to satisfy either (the `certificate` profile + verbatim
storage make byte-replay possible), but the exact verifier-side comparison domain and the checkpoint label are
co-designed with consumers in a later phase. Marked `OPEN-5`.

---

## 10. Worked examples (the implementability test)

Walking two concrete checks end-to-end. If any step needed a judgment not written above, that would be a gap;
both resolve with only the rules in this document.

### 10.1 pmcp "no new gateway tool/handler/store" prohibition → `unknown`

1. **Frontier (sec 1):** the run selects pmcp's gateway `capability`; its decomposition descendants
   (components/operations) are in-scope (sec 1.3). The `prohibition` node "no new gateway tool/handler/store"
   is in-scope (it hangs off the capability).
2. **Closed-world domain (sec 4.1):** the prohibition declares `domain.type = capability_registration` — the
   bounded fact set = realized facts that register a gateway tool/handler/store within the frontier subgraph.
3. **Observability (sec 4.3):** the Phase-1 `E(C)` extractor (greenfield Interface Boundary IR) surfaces
   boundaries + `imports|calls|dependencies` edges, but **registration facts are not direct Boundary-IR
   facts**. So `coverage(D, E(C)) = partial`.
4. **Decision (sec 4.2):** no violating fact is observed AND coverage is partial → **`unknown`**, finding code
   `prohibition_unobservable`.
5. **Dimension rollup (sec 6.3):** the `prohibition` dimension = `unknown`.
6. **Overall (sec 6.4):** `overall_result_state = unknown` (no fail, at least one unknown) → **non-green**.
7. **Certificate (sec 9):** `overall_result_state: unknown`, `dimension_results[prohibition].result_state:
   unknown`, `findings_ref` → a finding `{dimension: prohibition, result_state: unknown, code:
   prohibition_unobservable, subject.prohibition_id: ...}`. Never a silent pass. (When the extractor later
   surfaces registration facts, the same prohibition resolves `pass`/`fail` with no metamodel change.)

### 10.2 An ordinary completeness check → `pass`

1. **Frontier (sec 1):** a `capability` with a child `operation` `billing.invoice.computeTotal`
   (`frontier:true`), in-scope with its decomposition descendants.
2. **Correspondence (sec 2):** the correspondence map resolves `computeTotal`'s Tier-1 `logical_id` to a
   realized `occurrence_id` for the run's revision (`match_origin: correspondence`).
3. **Kind alignment (sec 5):** desired kind `operation`; realized greenfield kind `method` → `exact` per
   `kind-alignment.json`. Parity-bearing.
4. **Completeness (sec 7.1):** a parity-bearing correspondence exists → element present → **`pass`** (no
   finding emitted for a pass).
5. **Soundness (sec 7.2):** normalized signature (idmodel sec 4: names dropped, types canonicalized, arity
   preserved) matches the desired signature → `pass`. A param-rename alone would not change the id and would
   not fail (allowed-freedom / idmodel body-only stability).
6. **Closure (sec 7.3):** the operation's private helpers below the frontier are classified
   `refinement_below_frontier` (sec 3) → closure `pass`.
7. **Overall (sec 6.4):** if all five dimensions are `pass`/`not_applicable` → `overall_result_state: pass` →
   **green** certificate.

---

## 11. OPEN decisions (surfaced, not silently resolved)

| id | decision | where it must be resolved |
|---|---|---|
| `OPEN-2` | chunker kinds are per-grammar/open-set; matrix is known-mappings + fallback, not exhaustive. | Inherent; the fallback rule (sec 5) handles it. Extend the matrix as grammars are characterized. |
| `OPEN-3` | "May edges target subgraphs?" — affects whether a frontier endpoint can be a subgraph. v1: nodes only, subgraph endpoints resolve to root node. | Deferred metamodel decision. |
| `OPEN-4` | Severity of `unsupported` in aggregation. v1: non-green (below `unknown`). | 0C/4 gating-policy co-design if consumers want it neutral. |
| `OPEN-5` | Digest byte-match domain between emitter and downstream verifiers; `parity-gate` vs `policy-gate` checkpoint_type. | Deferred, co-designed with consumers. |

**Resolved decisions:**

- `OPEN-1` (greenfield exact node-`kind` value list) — **RESOLVED**. Greenfield published its closed kind set
  `frozenset({module, class, interface, struct, trait, function, method})` at
  `greenfield/interface_boundary.py:27-29` (`IF-0-PUBKINDS-1`); the greenfield rows in `kind-alignment.json`
  are reconciled to it and flagged `CONFIRMED` (struct added → `type`; dead `field`/`property` rows dropped).
  See sec 5.1.

Everything not listed here is fully pinned by this document + the three schemas + `kind-alignment.json`.

---

## 12. Delivery — the metadata-only portal payload (Phase 0C)

> **Status: v0 draft — pending portal co-design sign-off.** The portal repo is **fenced**; this section and
> `schemas/portal-payload.schema.json` are drafted spec-side and **freeze after Wave-B review**. The shape may
> change once the portal co-designs it; until then it is a self-consistent, testable contract that the spec
> side can produce and prove.

A **delivery** is the metadata-only, render-ready **projection of a certificate** that a consumer (the portal)
renders. It is NOT the certificate, and NOT the finding_set — it is a redacted, content-addressed *summary*.

Schema: `schemas/portal-payload.schema.json`. Producer: `spec-engine/deliver.py` (`project(certificate,
finding_set) -> payload`), a pure deterministic function, canon-serialized and content-addressed.

### 12.1 Why two inputs (certificate AND finding_set)

The certificate carries `dimension_results` (states + `finding_id`s) and `findings_ref` (a pointer) but **no
`findings` array**. The render-ready finding *summaries* (title, location ref, result_state, waiver_ref) live in
the `finding_set` behind `findings_ref`. So the projection takes **both** already-produced artifacts — it
**re-runs nothing** (produce-once, canon §9) — and asserts `certificate.findings_ref ==
finding_set.finding_set_id` so a certificate can never be paired with the wrong finding_set.

### 12.2 What the payload INCLUDES (allowlist)

The payload is built by **allowlist**: only known-safe fields are copied in. Nothing is "copied then scrubbed".

- **`certificate_digest`** — the certificate's authoritative identity (its `digest`). The portal can replay-
  verify against the stored certificate by this digest (canon §9 verbatim replay; `OPEN-5` byte-match domain).
- **`spec_revision_digest`, `desired_graph_digest`, `ec_digest`, `code_head_sha`, `ec_revision_id`** — the
  provenance pins. All are **digests/ids** — content-addressed, **not fetchable URLs** (SSRF-safe).
- **`overall_result_state`** + **`dimension_results`** (all five, state-only) — for the badge and per-dimension
  rendering. Copied verbatim from the certificate (never recomputed).
- **`badge`** — a derived render hint, **green IFF `overall_result_state == pass`**. `not_applicable` is
  **neutral/non-green** (sec "never silently green" / line ~298), `fail`→failing, `unknown`/`unsupported`→
  non-green. The badge is a convenience; `overall_result_state` is the load-bearing truth.
- **`ec_reproducible`** + **`advisory`** — `advisory = not ec_reproducible`. A non-reproducible E(C) makes the
  delivery **advisory** (the portal must not gate on it).
- **`finding_summaries`** — one per finding, each carrying ONLY: `dimension`, `result_state`, `code`,
  `title` (**derived from `code`**, a stable machine label — NOT the finding `message`), a **location ref**
  (`subject.desired_logical_id` / `subject.realized_occurrence_id` — idmodel digests, SSRF-safe), the kinds
  (`desired_kind`/`realized_kind`), and `waiver_ref` if the finding is waived.
- **Version pins** (`canon_version`, `idmodel_version`, `kind_alignment_version`,
  `permitted_freedom_vocab_version`, `projection_algo_version`, `schema_version`, `payload_schema_version`) —
  so a rendered delivery is reproducible and two legitimately-different deliveries are tellable apart.
- **`waivers_ref?`** — the digest pointer (pointer, not payload), present iff the certificate carries one.

### 12.3 What the payload EXCLUDES (the metadata-only guarantee)

A delivery carries **NO** of the following, and the projection's allowlist makes this structural, not
best-effort:

- **Raw evidence** — `finding.evidence_refs` and any `evidence`/`evidence_payload` field are dropped. Only
  content-addressed *refs* would ever survive, and even those are not copied into summaries.
- **Source text** — no realized/desired source snippets, bodies, or file contents.
- **Secrets** — any `secret`/`token`/`credential`-shaped field is never in the allowlist.
- **Finding `message`** — explicitly NOT-meaning-bearing (`result-state.schema.json`) and the field most likely
  to carry incident detail in a real finding; the `title` is derived from `code` instead.
- **Fetchable internal URLs** — every ref in the payload is a digest/id, never an `http(s)://internal-host`
  URL. The portal resolves digests against its own trusted store; it never fetches a URL the payload hands it.

**Proven, not asserted:** the conformance gate injects synthetic `evidence`/`secret` fields into BOTH the
certificate AND a finding (findings are the higher-risk injection point — real evidence lives there) and proves
the resulting payload contains **none** of them and is byte-identical to the un-injected payload. The allowlist
is what makes the guarantee hold; an additional assert-absent recursive scan is kept as defense-in-depth.

### 12.4 Determinism + content-addressing

The payload is canon-serialized and carries a top-level **`digest`** = canon **`semantic-content`** digest of
the payload (no new canon profile is introduced — that would be a canon-contract change). canon's top-level
digest-exclusion (§8) removes `digest` from its own preimage automatically, mirroring the certificate. The
projection is **byte-reproducible**: two runs over the same `(certificate, finding_set)` yield byte-identical
payloads and the identical `digest`. Volatile/provenance data (wall-clock, host) would live in a non-hashed
`locator` envelope (canon §10) if added later; v0 carries none.

### 12.5 OPEN (folds into `OPEN-5`)

The exact byte-match domain the portal uses to replay-verify `certificate_digest`, and the final payload field
set, are co-designed with the (fenced) portal post-Wave-B. `payload_schema_version` starts at `"0"` to signal
the draft status; it bumps to `"1"` on portal sign-off.

---

## 13. XG2 authority event contract (IF-0-XG2-1)

The XG1 certificate proves **parity**. It does **not** confer authority for a consumer decision. XG2 adds a
separate signed authority event, `event_type: governance_bridge_decision`, recorded in the append-only
`spec-engine/authority/authority-event.schema.json` ledger contract. The cert and the authority event travel
together, but they answer different questions:

- the cert proves the producer's parity verdict for a specific `decision_id` and `canon_version`;
- the authority event proves that the Portal/org layer granted authority for that exact audience.

### 13.1 Audience scoping

Every authority entry carries an exact audience scoping tuple:
`{repo, env, lineage, policy_epoch, canon_version, decision_id}`.
Matching is **exact** and fail-closed. A consumer gate rejects repo, env, lineage, policy_epoch,
canon_version, or decision-id drift as a scope mismatch; there is no silent prefix, wildcard, or partial match.

### 13.2 Custody binding + separation of powers

Each entry carries `key_id` plus a `custody_binding` object. The custody binding freezes who is allowed to sign
and records that the **phase-loop driver cannot mint authority**. The driver may verify and route authority
events, but the authority-conferral event originates from the Portal/org layer, never from the automation
driver. A signature that claims a driver origin, a wrong approver, or the wrong key id is rejected fail-closed.

### 13.3 Append-only history, revocation, and supersession

The ledger is append-only and hash-chained through `previous_entry_digest`, `entry_digest`, and
`inclusion_proof`. Authority history is never rewritten:

- revocation is a later signed entry that marks the scoped decision revoked;
- supersession is a later signed entry that replaces an older entry while preserving history.

The effective-authority resolver selects the latest valid signed entry for the exact audience after applying
revocation and supersession. Missing signatures, tampering, duplicate entries, broken links, expired validity,
revoked entries, superseded entries, and scope mismatches all fail closed.

### 13.4 External freeze reference

IF-0-XG2-1 is a **spec-produced contract**. A downstream **governed pipeline fail-close gate** consumes
this contract as an external freeze reference. spec owns the authority-event schema, append-only ledger, scoped
resolver behavior, and custody semantics; it does **not** own the consumer enforcement switch or downstream UI
ratification flow.
