# Rethlas Architecture

**Status.** Design — not yet implemented. Captures decisions from the design
session of 2026-04-23.

This document is the contract for implementation. If code and this document
diverge, one of them is wrong — investigate and fix.

---

## 1. Vision and Scope

Rethlas is an **agent tool for incremental mathematical knowledge base
construction** via natural-language proofs produced and checked by
LLM-backed components. A single Rethlas installation operates on multiple
independent **workspaces**; each workspace represents one math project.

**In scope (Phase I):**

- Event-sourced knowledge base with append-only knowledge events as single truth
- Codex-backed generator and verifier agents
- Kuzu-backed DAG projection for queries
- Per-node markdown views for Codex browsing
- Coordinator-driven scheduling of generation and verification work
- Content-addressed proof-pair identity via Merkle hashes
- Simple count-based verification progress tracking

**Out of scope (Phase I):**

- Lean / Coq / Isabelle / any formal verification
- Automated theorem provers (Vampire, E, Z3, ...)
- Multiple LLM backends (Codex only; Claude is a future extension)
- Consensus / audit verification across backends
- Distributed multi-machine writers on the same workspace
- Migration of existing inducedorbit data into the new model
- Blueprint LaTeX export
- Cytoscape interactive DAG visualization (dashboard is linear list in Phase I)
- Importer (external library reader)
- Semantic embedding search
- Linter's full projection-drift checks

---

## 2. Tool and Workspace

Rethlas is the tool; a workspace is the data. They live in separate
directories and have separate git histories.

### 2.1 Rethlas repo (tool code)

```
Rethlas/
├── generator/           # generator agent (internal structure follows original Rethlas)
│   ├── .agents/skills/
│   ├── .codex/
│   ├── mcp/
│   ├── AGENTS.md
│   ├── role.py          # NEW: thin wrapper for event-driven invocation
│   └── ...
├── verifier/            # verifier agent (internal structure follows original)
│   ├── .agents/skills/
│   ├── .codex/
│   ├── mcp/
│   ├── api/
│   ├── AGENTS.md
│   ├── role.py          # NEW
│   └── ...
├── coordinator/         # scheduling only
├── librarian/           # maintains dag.kz/ and nodes/
├── linter/              # consistency audit (read-only)
├── dashboard/           # HTTP linear view (Phase I)
├── common/              # shared libraries
│   ├── kb/              # KnowledgeBase interface + Kuzu backend
│   ├── events/          # event read/write/parse
│   ├── runtime/         # codex_runner + log-mtime timeout
│   └── config/          # rethlas.toml loader
├── cli/                 # rethlas command entry points
├── producers.toml       # producer registry
├── pyproject.toml
└── docs/
    ├── ARCHITECTURE.md  # this file
    └── PHASE1.md        # Phase I task list
```

### 2.2 Workspace (project data)

```
<workspace>/
├── rethlas.toml                    # workspace config
├── events/                         # ⭐ knowledge truth only, append-only, git-tracked
│   └── {date}/*.json
├── knowledge_base/                 # derived, gitignored
│   ├── dag.kz/                     # Kuzu graph DB (Python components use this)
│   └── nodes/                      # verified notes only (Codex reads this)
│       └── {kind_prefix}_{label}.md
└── runtime/                        # ephemeral, gitignored
    ├── jobs/                       # one file per in-flight job
    │   └── {job_id}.json           # pid, target, mode, started_at
    ├── logs/
    │   └── {job_id}.codex.log      # Codex subprocess stdout
    └── state/
        └── rejected_batches.jsonl  # recent decoder/librarian rejections
                                    # (surface via dashboard)
```

Workspace `.gitignore`:
```
/knowledge_base/
/runtime/
*.tmp
```

### 2.3 CLI invocation

```bash
pip install -e ~/mycodes/Rethlas           # install tool once

cd ~/mycodes/my_project                    # in workspace
rethlas init                               # initialize events/ skeleton + rethlas.toml
rethlas supervise                          # run librarian + coordinator + dashboard
rethlas dashboard --port 8765              # dashboard only
rethlas linter --mode fast                 # one-shot audit
rethlas rebuild                            # rebuild dag.kz + nodes/ from events
```

Default workspace is cwd. `--workspace <path>` overrides.

---

## 3. Truth Layer: `events/`

`events/` contains only **knowledge truth**. Runtime orchestration, process
state, logs, retries, and job bookkeeping live under `runtime/` and are not
part of the recoverable mathematical truth.

All derived artifacts (Kuzu DB, nodes/*.md files) can be reconstructed from
the truth event stream alone.

### 3.1 Core invariants

1. **Append-only.** Once a truth event file is committed to its canonical
   path, it is never modified, moved, or deleted.
2. **Self-contained.** Each event file contains every piece of information
   needed to interpret it. Event bodies inline content directly. No external
   blob storage.
3. **Events may reference other events** by `event_id`. Since the referenced
   event is in `events/`, truth stays self-contained.
4. **Atomic publication.** Truth publication is atomic. For single-event
   writes this means `.tmp` + rename. For generator multi-event output this
   means whole-batch atomic publish. No partial truth is visible.
5. **Only knowledge actors write truth events.** In Phase I, only
   `user`, `generator`, and `verifier` write to `events/`.

### 3.2 File naming

```
{iso_ms}--{event_type}--{target_or_none}--{actor}--{seq}--{uid}.json
```

- **`iso_ms`**: compact ISO with millisecond precision (`20260423T143015.123`)
- **`event_type`**: dotted name (`user.node_added`, `verifier.run_completed`)
- **`target_or_none`**: DAG node label with `:` escaped to `_`, or `none`
- **`actor`**: producer identifier `{kind}:{instance}` with `:` escaped to `_`
- **`seq`**: zero-padded monotone sequence within the producer's same-millisecond emission batch
- **`uid`**: 8 hex characters for uniqueness and filename ↔ event_id consistency
- **Extension**: `.json` only in Phase I

Examples:
```
20260423T143015.123--user.node_added--def_primary_object--user_alice--0001--a7b2c912.json
20260423T144522.999--verifier.run_completed--lem_block_form_for_x0_plus_u--verifier_codex-gpt-5.4--0001--1f8e22c0.json
```

Directories sharded by date: `events/{YYYY-MM-DD}/`.

Filename metadata is informational; frontmatter / JSON body is authoritative.
Linter checks filename-body consistency.

### 3.3 Event formats

**All truth events use `.json` in Phase I.** Human-readable markdown views
belong to derived `knowledge_base/nodes/*.md`, not to `events/`.

```json
{
  "event_id": "20260423T143015.123-0001-a7b2c912",
  "ts": "2026-04-23T14:30:15.123+08:00",
  "type": "user.node_added",
  "actor": "user:alice",
  "target": "def:primary_object",
  "payload": {
    "kind": "definition",
    "statement": "A primary object is ...",
    "remark": "Local foundational definition.",
    "source_note": ""
  }
}
```

### 3.4 Core event schema fields

| Field            | Required | Description                                  |
| ---------------- | -------- | -------------------------------------------- |
| `event_id`       | ✓        | `{iso_ms}-{seq}-{uid}` unique event identifier |
| `type`           | ✓        | `{producer_kind}.{action}` dotted name       |
| `actor`          | ✓        | `{kind}:{instance}` string                   |
| `target`         | —        | DAG node label if applicable                 |
| `parent_event_id`| —        | Optional direct causal predecessor           |
| `ts`             | ✓        | Full ISO timestamp (matches `event_id` prefix)|
| `cost`           | —        | Per-event resource consumption, see §3.6     |
| `payload`        | ✓        | Structured event payload                     |

### 3.5 Producer openness

Truth-event producers are an intentionally small set in Phase I. New
producers are possible in later phases, but Phase I truth events are limited
to `user`, `generator`, and `verifier`. The librarian rejects truth events
whose `actor` / `type` don't match registered patterns.

Phase I truth producers:
- `user:<name>` — human author
- `generator:codex-gpt-5.4-xhigh` — Codex generator
- `verifier:codex-gpt-5.4` — Codex verifier

### 3.5.1 Phase I truth event types

Phase I truth events are intentionally small and unified:

- `user.node_added`
- `user.node_revised`
- `user.hint_attached`
- `generator.batch_committed`
- `verifier.run_completed`

`user.node_added` and `user.node_revised` carry:

```json
{
  "kind": "definition | external_theorem | lemma | proposition | theorem",
  "statement": "...",
  "proof": "...",
  "remark": "...",
  "source_note": "..."
}
```

For `definition` and `external_theorem`, `proof` is normally empty. For
`lemma` / `proposition` / `theorem`, users may add or revise a node with a
proof already present; that enters the verifier queue at `pass_count = 0`
rather than generator queue at `pass_count = -1`.

`user.node_added` and `user.node_revised` both carry the node's complete
authored state. Neither is a patch event.

For both add and revise, the payload must provide:
- `kind`
- `statement`
- `proof`
- `remark`
- `source_note`

Omitting one of these fields is invalid in Phase I. This is a full-state
schema requirement, not a non-empty-content requirement: `proof` may be the
empty string when the node currently has no proof. `remark` and `source_note`
may be empty strings. `statement` may never be empty.

Special case: for `external_theorem`, `source_note` must be non-empty.

Hint events carry:

```json
{
  "hint": "...",
  "remark": "..."
}
```

`user.hint_attached` is also full-schema in Phase I: both `hint` and `remark`
must be present. `remark` may be empty, but `hint` may not be empty.

Verdict events carry:

```json
{
  "verification_hash": "sha256:...",
  "verdict": "accepted | gap | critical",
  "verification_report": {
    "summary": "...",
    "checked_items": [],
    "gaps": [],
    "critical_errors": [],
    "external_reference_checks": []
  },
  "repair_hint": "..."
}
```

`verifier.run_completed` is full-schema in Phase I: `verification_hash`,
`verdict`, `verification_report`, and `repair_hint` must all be present.
`repair_hint` may be empty. `verification_report` must be present even when
the verdict is `accepted`.

`verification_report` has a fixed minimum structure in Phase I:
- `summary`: string
- `checked_items`: array
- `gaps`: array
- `critical_errors`: array
- `external_reference_checks`: array

Expected consistency:
- `accepted` ⇒ `gaps = []` and `critical_errors = []`
- `gap` ⇒ `gaps` non-empty
- `critical` ⇒ `critical_errors` non-empty

Generator batch-commit events carry:

```json
{
  "attempt_id": "gen-20260424T101530.123-0001-a7b2c912",
  "target": "thm:maximal_orbits_equal_open_orbits",
  "mode": "fresh | repair",
  "nodes": [
    {
      "label": "lem:block_form_for_x0_plus_u",
      "kind": "lemma",
      "statement": "...",
      "proof": "...",
      "remark": "...",
      "source_note": "..."
    },
    {
      "label": "thm:maximal_orbits_equal_open_orbits",
      "kind": "theorem",
      "statement": "...",
      "proof": "...",
      "remark": "...",
      "source_note": "..."
    }
  ]
}
```

Each `nodes[]` entry is the node's full post-commit state, not a patch.
Librarian diffs the committed batch against current KB state to determine
whether each node is new, revised, or proof-only changed.

Each `nodes[]` entry must provide the full authored node schema:
- `label`
- `kind`
- `statement`
- `proof`
- `remark`
- `source_note`

Field presence is mandatory. `statement` must be non-empty. `proof`,
`remark`, and `source_note` may be empty strings, except that
`kind=external_theorem` still requires non-empty `source_note`.

The batch-level `target` field must equal the generator run's primary target
label, and that label must appear in `nodes[]`. A generator run may emit
auxiliary nodes alongside the target, but it may not commit a batch that
omits the target node entirely.

Within one `generator.batch_committed`, each label may appear at most once in
`nodes[]`. Duplicate labels inside the same batch are invalid.

Batch-local forward references are allowed. A node in `nodes[]` may reference
another node that appears later in the same batch, as long as the referenced
label also appears exactly once in that batch and the fully combined batch
graph remains acyclic.

Self-reference is always invalid. A node may not reference its own label in
its `statement` or `proof`, whether directly or via same-batch emission.
This rule applies equally to user-authored node events and generator batches.

### 3.5.2 Label rules

Node labels are part of the long-lived knowledge interface. They must be
globally descriptive, not local placeholders.

Required form:

```text
{prefix}:{slug}
```

Where:
- `prefix ∈ {def, ext, lem, prop, thm}`
- `slug ∈ [a-z0-9_]+`

Rules:
- Label should roughly describe the mathematical content or role of the node
- Label must remain meaningful when read in isolation later
- Local / positional names are invalid

Invalid examples:
- `thm:main`
- `lem:helper`
- `prop:claim1`
- `lem:key_step`
- `def:object`

Valid style examples:
- `def:primary_object`
- `ext:barbasch_signed_tableau_rule`
- `lem:block_form_for_x0_plus_u`
- `prop:symplectic_sign_pair_for_even_block`
- `thm:maximal_orbits_equal_open_orbits`

Librarian validates label syntax and rejects placeholder-style labels in
Phase I.

### 3.6 Per-event cost tracking

Events that consume LLM resources may record consumption inline in the truth
event when that cost can be attached to an actual knowledge update or
verifier judgment. In Phase I:

- `verifier.run_completed` carries the full verifier run cost when available
- generator run cost is recorded once on `generator.batch_committed`

```yaml
cost:
  input_tokens: 12345
  output_tokens: 6789
  reasoning_tokens: 234567
  cost_usd: 0.042
  duration_seconds: 890
```

Dashboard / linter aggregate these by querying the event stream. No
separate cost-tracking store. No budget enforcement in Phase I — just
observability.

### 3.7 Writing truth events

This section collects the rules for how truth events reach `events/` in a
form that librarian can replay deterministically.

#### 3.7.1 Stable event ordering

Librarian replay order must be stable and deterministic. Sorting is by:

1. `iso_ms`
2. `seq`
3. `uid`

`uid` is retained for uniqueness, but it has no primary ordering meaning.
Within a same-millisecond producer batch, `seq` is the canonical order. This
is especially important for generator-emitted statement/proof pairs and
topologically ordered multi-node output.

#### 3.7.2 Generator batch atomicity

One generator run is one generator truth commit. A single
`generator.batch_committed` event carries the full batch of node updates and
must be published atomically.

Rules:

1. Generator stages the full batch outside canonical `events/` visibility.
2. Only once the batch is complete does the wrapper atomically publish the
   single `generator.batch_committed` truth event into `events/{date}/`.
3. Librarian must never observe a partially published generator batch.
4. Each successful generator run emits exactly one `generator.batch_committed`
   truth event carrying:
   - `attempt_id`
   - target label
   - mode (`fresh` / `repair`)
   - full node batch
   - optional batch-level `cost`

This prevents half-attempt truth from entering the KB and gives generator cost
a single stable truth anchor.

---

## 4. Derived Layer

The DAG is derived from events. Two representations are kept in sync by
librarian:

- **`dag.kz/`** — Kuzu graph database (used by Python components)
- **`nodes/*.md`** — per-node markdown files (used by Codex via bash)

Both are gitignored and rebuildable.

**Hard invariant:** the dependency graph is a DAG. Cycles are never allowed.
Any authored truth change that would introduce a dependency cycle is rejected
by librarian and does not change KB state. This applies to both user-authored
node events and generator batch commits.

### 4.1 KuzuDB

- Embedded (no server process)
- Native Cypher query language
- Good for graph queries (closure, cycles, recursion)
- Python binding is first-class
- Sole writer: librarian
- Readers: coordinator, linter, dashboard, librarian

### 4.2 Per-node markdown files

`knowledge_base/nodes/{kind_prefix}_{label_sanitized}.md`. Flat directory.
Filename convention: replace `:` with `_` in labels. Examples:
- `def:primary_object` → `def_primary_object.md`
- `lem:block_form_for_x0_plus_u` → `lem_block_form_for_x0_plus_u.md`
- `thm:maximal_orbits_equal_open_orbits` → `thm_maximal_orbits_equal_open_orbits.md`

**Only nodes with `pass_count >= 1` are written to `nodes/`**.
Unverified (count=0) or failed (count=-1) nodes do NOT appear in this
directory. Librarian deletes the md file when a node transitions from
count ≥ 1 back to count < 1 (e.g., due to hash change or wrong verdict).

`nodes/` is the only Codex-visible knowledge view in Phase I. Both generator
and verifier read only these verified notes.

**Format: YAML frontmatter + markdown body:**

```markdown
---
label: lem:block_form_for_x0_plus_u
kind: lemma
pass_count: 2
depends_on: [def:primary_object, lem:normal_form_for_x0]
---

**Source Note.** Barbasch, Section 3, Theorem 3.4, pp. 45-47.

**Remark.** Imported for the symplectic branch.

**Statement.** Let $X$ be a primary object ...

**Proof.** By \ref{lem:normal_form_for_x0}, ... $\square$
```

**YAML header fields:**
- `label`: the canonical label
- `kind`: definition / external_theorem / lemma / theorem / proposition
- `pass_count`: current integer count (always ≥ 1 for files in nodes/)
- `depends_on`: list of dependency labels, derived from explicit `\ref{...}` occurrences

**Semantics of `pass_count`:**
- `pass_count = 0` is impossible in `nodes/*.md` (filter excludes them)
- `pass_count ≥ 1` means the node has been independently verified at
  least once
- Higher `pass_count` means more confidence (more independent verifier
  passes against the current hash)
- Codex uses the value directly when deciding how much to trust a dep

No separate `verified` field — `pass_count` alone conveys both binary
(verified/not) and scalar (confidence level) information.

**Codex reads `nodes/`** via bash (`ls`, `cat`, `grep`, `find`) inside
this directory with `cwd = nodes/` and `--sandbox read-only`. When
Codex encounters `\ref{lem:foo}` in a proof, the `resolve-reference`
skill teaches it to `cat nodes/lem_foo.md` (colon→underscore) to see
the dependency.

### 4.3 Access interface: `common/kb/KnowledgeBase`

Python components access the KB through a Protocol, decoupling them from
the backend.

```python
class KnowledgeBase(Protocol):
    # reads
    def get_node(self, label: str) -> Node | None: ...
    def list_nodes(self, *, kind=None) -> list[Node]: ...
    def direct_dependencies(self, label: str) -> list[str]: ...
    def dependents(self, label: str) -> list[str]: ...
    def dependency_closure(self, label: str) -> list[Node]: ...
    def detect_cycles(self) -> list[list[str]]: ...
    def latest_verdict(self, label: str, hash: str) -> Event | None: ...
    def count_repair_attempts(self, label: str, hash: str) -> int: ...

    # writes (librarian only)
    def apply_event(self, event: Event) -> list[str]: ...
    def rebuild_from_events(self, events_dir: Path) -> None: ...
```

Only Python components use this. Codex does not import KB code.

---

## 5. Node Model

### 5.1 Kinds

| Kind | Generator can create? | Initial count | Fix on wrong verdict |
| --- | --- | --- | --- |
| `definition` | ✓ | **0** (needs verify) | user (primary); generator as side-effect of a proof-requiring repair |
| `external_theorem` | ✗ (user only) | **0** | user only |
| `lemma` | ✓ | **-1** | generator (dispatched auto) |
| `theorem` | ✓ | **-1** | generator |
| `proposition` | ✓ | **-1** | generator |

**All five kinds share the same Node schema** (statement + proof +
hashes + count). They differ in:

- **Who can create them** (generator or user only)
- **Whether proof is expected** (empty for axioms, content for
  proof-requiring)
- **How verifier evaluates them** (same 3-stage pipeline but stages 2-3
  adapt based on kind)
- **Who can fix them when wrong** (generator for proof-requiring, user
  for definition / external_theorem)

`definition` and `external_theorem` are **both** "no-proof" kinds but
they are structurally **distinct** in meaning:
- `definition`: a concept introduced locally (user or generator
  authored); verifier checks well-formedness
- `external_theorem`: a result imported from external literature
  (user-only, requires citation); verifier checks the import's
  references resolve

All 5 kinds run the same DESIRED=3 audit cycle.

`kind` is immutable after node creation. Revisions replace the node's full
authored state for the same label, but must preserve `kind`. They may change
statement, proof, `remark`, and `source_note`, but never `kind`. If a different
classification is desired, create a new node under a new label.

No "goal" concept in the schema. What the user considers a goal is just
a `kind=theorem` node; dashboard / user distinguishes via naming.

### 5.2 Kuzu Node table

```cypher
CREATE NODE TABLE Node (
  label STRING PRIMARY KEY,
  kind STRING,                         -- definition | external_theorem | lemma | theorem | proposition
  statement STRING,                    -- always non-empty
  proof STRING,                        -- empty for axioms or unproved
  statement_hash STRING,
  verification_hash STRING,
  pass_count INT DEFAULT -1,   -- -1 / 0 / positive
  verification_report STRING,          -- latest verifier report (stage details + verdict)
  repair_hint STRING,                  -- accumulated hints for next repair attempt
  remark STRING,
  source_note STRING
);

CREATE REL TABLE DependsOn (FROM Node TO Node);

-- projection progress (librarian's water mark)
CREATE NODE TABLE ProjectionState (
  key STRING PRIMARY KEY,
  value STRING
);
```

**Node contents** (logical schema):

- **`statement` / `proof`**: the current text for this node. `statement` is
  always non-empty. `proof` may be empty for axioms or for proof-requiring
  nodes that currently have no proof.
- **Hashes**: for Merkle propagation and verdict matching.
- **`pass_count`**: `-1` means needs generator; `0` means needs
  verifier; `≥ 1` means verified that many times.
- **`verification_report`**: set by librarian whenever a
  `verifier.run_completed` event arrives — contains the latest
  three-stage structured report (what the verifier found). Generator
  reads this during repair.
- **`repair_hint`**: current aggregated hints for the next repair attempt.
  Sources:
  - Extracted from verifier's report on wrong verdict (e.g., "step 5 has a gap")
  - Set by `user.hint_attached` events
  - Cleared when generator emits a new statement/proof for the node
- **`remark`**: free-form human-facing note; field always present, value may be empty
- **`source_note`**: source/citation note; field always present, value may be
  empty except for `external_theorem`, where it is required to be non-empty

`DependsOn` is derived and maintained by librarian from explicit `\ref{label}`
occurrences in the current `statement` and `proof`. It is not authored
directly in truth events.

**Canonical dependency syntax:** only `\ref{label}` creates a formal
dependency edge. Mere textual mention does not.

Phase I does **not** attempt semantic duplicate detection or automatic node
merging. Node identity is the canonical `label`.

`nodes/{kind_prefix}_{label_sanitized}.md` renders the node plus derived
dependency list into one
human- and Codex-readable file.

**Not stored:**
- `status` enum
- `latest_verdict_event_id`
- `strongly_accepted`
- Any "fresh / stale" marker

### 5.3 Merkle hashes

Two recursive hashes capture node identity. The Merkle chain uses **only
`statement_hash` of deps** — never `verification_hash` of deps. Hash inputs
must use canonical JSON encoding (UTF-8, sorted keys, compact separators,
newline-normalized text).

```
statement_hash(n) = sha256(
    canonical_json({
      "schema": "rethlas-statement-v1",
      "label": n.label,
      "kind": n.kind,
      "statement": n.statement,
      "depends_on": [
        {"label": dep.label, "statement_hash": dep.statement_hash}
        for dep in sorted(n.depends_on, key=label)
      ]
    })
)

verification_hash(n) = sha256(
    canonical_json({
      "schema": "rethlas-verification-v1",
      "statement_hash": statement_hash(n),
      "proof": (n.proof or "")
    })
)
```

**Why only `dep.statement_hash` (not `verification_hash`) propagates:**

My proof references dep by `\ref{dep_label}` — I use dep's statement,
not its proof content. If dep's proof changes (same statement, new
proof), my proof is unaffected. So dep's `verification_hash` changes
must NOT invalidate my verdict. Only dep's `statement_hash` change
should cascade.

**Consequences:**
- Change own statement → both own hashes change → `statement_hash` cascades to dependents
- Change own proof → only own `verification_hash` changes → NO cascade
- Dep changes proof → dep's `statement_hash` unchanged → my hashes unchanged → my verdicts remain valid
- Dep changes statement → dep's `statement_hash` changes → my `statement_hash` changes (via this Merkle rule) → my `verification_hash` changes → my verdicts become stale

For axioms (`definition` / `external_theorem`): no proof, so
`verification_hash == statement_hash`.

**Propagation rule:** When any node's `statement_hash` changes, all
dependents' `statement_hash` recompute (BFS up dependents). Each affected
node's `verification_hash` also recomputes.

**Key property:** Changing a dep's proof (while keeping its statement
unchanged) does NOT invalidate my verdict — dep's `statement_hash` didn't
change, so my `statement_hash` and `verification_hash` didn't change.

### 5.4 `pass_count` semantics

Signed integer. Librarian maintains per event.

| Value | Meaning |
| --- | --- |
| `-1` | Needs generator: either no proof yet, or latest verdict for current hash was `gap` / `critical` |
| `0` | Has proof; no accepted verdict for current hash yet |
| `>= 1` | Accepted N times against current hash |

Higher `pass_count` = more confidence.

**Update rules:**

| Event | Effect on target Node |
| --- | --- |
| `user.node_added(kind=definition)` | Create kind=definition, `count = 0` |
| `user.node_added(kind=external_theorem)` | Create kind=external_theorem, `count = 0` |
| `user.node_added(kind ∈ {lemma, proposition, theorem})` with empty proof | Create node, `count = -1` |
| `user.node_added(kind ∈ {lemma, proposition, theorem})` with non-empty proof | Create node, `count = 0` |
| `user.node_revised` | Replace the node's full authored state (same label, same kind); hashes recompute; `count = 0` if proof non-empty else `-1`; Merkle propagates to dependents when statement changes |
| `generator.batch_committed` | Apply the committed node batch atomically; for each included node, create or replace current statement/proof/metadata, recompute hashes, and set `count = 0` if proof non-empty else `-1` |
| `verifier.run_completed(accepted, hash matches)` | `count += 1`; set `verification_report` |
| `verifier.run_completed(gap, hash matches)` | `count = -1`; set `verification_report` + `repair_hint` (local fix suggestions) |
| `verifier.run_completed(critical, hash matches)` | `count = -1`; set `verification_report` + `repair_hint` (may need statement rewrite) |
| `verifier.run_completed(hash mismatch)` | Ignored (stale verdict) |
| `user.hint_attached(target=X)` | Append to `X.repair_hint` |

### 5.5 Auditability and safety checks

#### 5.5.0 Prevention-first mechanisms for `pass_count` correctness

Before the audit catches drift, multiple mechanisms make drift unlikely
to occur in the first place:

1. **Single writer to Kuzu.** Only librarian writes `dag.kz/`. No race.

2. **Transactional event application.** Librarian wraps each event's
   application in a Kuzu transaction. Crash mid-apply → rollback, event
   not marked applied, retried on next startup.

3. **Idempotent event application.** Librarian stores
   `last_applied_event_id` in `ProjectionState`. Re-processing events
   before that id is a no-op. Safe to replay after restart.

4. **Strict event ordering.** Events processed by ascending tuple
   `(iso_ms, seq, uid)`. Never apply later event before earlier.
   Deterministic replay → reconstructed `pass_count` always matches.

5. **Hash-match gate on verdicts.** A `verifier.run_completed` event
   changes `pass_count` **only if** its `verification_hash` equals the
   current `Node.verification_hash`. Stale verdicts are silently
   discarded — cannot over-increment or corrupt count.

6. **Pre-dispatch hash revalidation** (§5.5.2). Before verifier runs,
   role layer confirms the hash is current. Catches drift *before* a
   new verdict can pollute count.

7. **Canonical hash inputs.** `statement_hash` and `verification_hash`
   use canonical JSON (UTF-8, sorted keys, compact separators,
   newline-normalized). Same state → same hash on every machine, every
   run. Deterministic.

8. **Generator batch atomicity** (§3.6.2). Multi-node generator output
   is one atomic `generator.batch_committed` event. Librarian never
   sees partial batches — count updates for all batch nodes happen
   together or not at all.

9. **Validation before apply.** Librarian validates schema + business
   rules (label format, cycle detection, reference resolution) before
   touching Kuzu. Invalid events never reach the count-update step.

10. **No retraction / no deletion.** Nodes are never removed; only
    statement / proof revised via `user.node_revised` /
    `generator.batch_committed`. Count transitions are always from
    event-driven recomputation, not from ad-hoc deletions.

With all of these in place, `pass_count` drift requires a librarian
code bug. The audit in §5.5.1 is the final safety net that catches such
bugs if they happen.

#### 5.5.1 Auditability of `pass_count`

`pass_count` stored in Node is a cache of a value that can be
**independently recomputed from the event stream**:

```
audit_count(node) =
  if node.proof is empty and node.kind in [lemma, theorem, proposition]:
    return -1  (proof-requiring node without proof ⇒ needs generator)
  
  matching_verdicts = [
    e for e in events if
    e.type == "verifier.run_completed" and
    e.target == node.label and
    e.payload.verification_hash == node.verification_hash
  ]
  
  if matching_verdicts is empty:
    return 0  (has proof but not yet verified against current hash)

  last = matching_verdicts[-1]  # by event_id ordering
  if last.verdict in ("gap", "critical"):
    return -1  (latest matching verdict rejects ⇒ needs repair)

  # latest is accepted; count the accepted verdicts against this hash
  return count(e in matching_verdicts if e.verdict == "accepted")
```

Linter's audit check: for every node, recompute `audit_count` from the
event stream and assert it equals the stored `Node.pass_count`.
Drift = librarian bug or corruption.

This makes the stored count a **verifiable** value. Anyone can reconstruct
it by reading `events/` alone. The count field is there for query speed,
not for correctness.

**Design observation — why "last verdict" and "any gap poisons" are
equivalent in practice:**

A gap/critical verdict triggers generator repair. Generator repair must
emit a batch whose `verification_hash` differs from the rejected hash
(see §6.2 — decoder enforces this). So the same hash is never
re-verified after a wrong verdict. For any unique `verification_hash`:

- Either every matching verdict is `accepted` (count up normally), OR
- The hash was judged wrong at some point and then abandoned
  (subsequent verdicts, if any, don't exist because hash changed)

No mixed-history hash exists. "Latest verdict" and "any gap" agree on
every hash. The simpler `last verdict` rule is used; the equivalence is
preserved by the repair-must-change-hash invariant.

#### 5.5.2 Pre-dispatch hash revalidation

**Before dispatching verifier on a node (or before verifier starts a
run), recompute `verification_hash` from current state.** If the freshly
computed value differs from the stored value, reset `count` to 0 and
update the stored hash.

```
fresh_hash = verification_hash(current_node_state)

if fresh_hash != node.verification_hash:
    node.verification_hash = fresh_hash
    node.pass_count = 0    # old accepted verdicts don't match new hash
```

Purpose:
- Catches any drift between stored hash and actual content (librarian bug,
  corruption, concurrent modification race)
- Ensures verifier always runs on a verified-fresh hash
- Invalidates prior accepted verdicts if state changed without proper
  hash maintenance

This is a **safety backstop**. In a bug-free librarian, hashes are always
current; this check is a no-op. But if the check ever triggers, the
system self-heals by resetting count.

### 5.6 Merkle cascade (statement changes only)

**When any node's `statement` changes**, or when librarian re-parses
explicit references from the current `statement` / `proof`, its
`statement_hash` recomputes
and Merkle propagation recomputes all dependents' hashes. For each
dependent whose `statement_hash` changed:
- If the dependent's `proof` is non-empty: `count = 0` (existing proof
  now needs re-verification against new dep state)
- If the dependent's `proof` is empty: `count = -1` (stays at -1,
  still needs generator)

**Own `proof` changes do NOT propagate downstream.** They change my
`verification_hash` but not my `statement_hash`; dependents only look at
my `statement_hash`. This captures the insight: dependents trust my
statement, not how I proved it.

**No separate `-1` propagation rule.** The strict-monotone dispatch rule
(§6.4) already prevents dependents from advancing when upstream is not
strictly ahead; no explicit BFS for -1 is needed. If an upstream goes
to -1 but recovers to its prior count with the same statement,
dependents just stay at their count waiting, no re-repair needed. If
an upstream's repair changes its statement, Merkle cascade automatically
resets dependents.

### 5.7 Counter-examples as statement revisions

A counter-example is **not a separate mechanism**. It is a special case
of statement revision: the original proposition `P` is replaced with its
correct form, typically `¬P` (the negation) or a more nuanced statement.
The proof of the revised statement serves as the counter-example.

**No dedicated `disproven` flag or `theorem_disproven` event.** The
existing statement-revision mechanism covers it:

**Event:** `generator.batch_committed` containing a revised node state for `X`
with the new statement and proof. When the revision flips truth polarity
(e.g., `P → ¬P`), the new proof demonstrates the original was false.

**Mechanical flow:**

1. Generator emits statement revision + proof events for X
2. X's `statement_hash` changes (new statement content)
3. X's `verification_hash` changes (new statement + new proof)
4. X's `pass_count` resets to 0 (hash change)
5. Via Merkle propagation, all transitive dependents of X also have their
   `statement_hash` / `verification_hash` change → their count resets to 0
6. Verifier re-runs on X (once eligible) — if new proof is valid,
   `X.count` → 1
7. Dependents with proofs reset to `count = 0` because their
   `verification_hash` changed
8. Verifier re-runs on dependents once strict-monotone conditions hold
9. If an old dependent proof no longer works, verifier returns
   `gap/critical` and only then that dependent enters `count = -1`
10. Coordinator dispatches repair on dependents
11. Generator during repair reads `nodes/{X}.md` with its new statement
   and writes dependents' proofs accordingly

**Why this design unification is natural:**
- Statement changes already propagate through the Merkle hash tree
- `pass_count` already captures the "needs re-verification" state
- No extra flag, no extra event type

**Kind assignment:** the counter-example is simply the target theorem
with its statement revised. A separate sibling "counter-example node"
(like a new `Y` distinct from `X`) is also possible if the user prefers
keeping both the original and the counter-example as separate nodes —
user adds them as two `user.node_added` events with different labels.

### 5.8 No status enum

The system does not store or define a `status` field. Queries derive
decisions directly from the atomic fields:

| Question | Query |
| --- | --- |
| Needs fresh proof? | `proof is empty` and kind is proof-requiring |
| Needs verification? | `pass_count == 0` and deps all `count > node.count` |
| Needs repair? | `pass_count == -1` and kind is proof-requiring |
| Complete? | `pass_count >= DESIRED_COUNT` (default 3) |
| Blocked (deps broken)? | Any dep is missing |

For display, dashboard composes a label string on the fly.

---

## 6. Components

### 6.1 Overview

| Component | Writes events | Writes dag.kz | Writes nodes/ | Uses MCP | Backend |
| --- | --- | --- | --- | --- | --- |
| **generator** | ✓ (`batch_committed` truth only) | ✗ | ✗ | ✓ (own MCP server) | Codex |
| **verifier** | ✓ (`run_completed` truth only) | ✗ | ✗ | ✗ | Codex |
| **coordinator** | ✗ (truth) | ✗ | ✗ | ✗ | Python |
| **librarian** | ✗ (truth) | **✓ (sole)** | **✓ (sole)** | ✗ | Python |
| **linter** | ✗ (truth) | ✗ | ✗ | ✗ | Python |
| **dashboard** | ✗ | ✗ | ✗ | ✗ | Python HTTP |

Hard invariants:
- Librarian is the only writer of `dag.kz` and `nodes/`
- Coordinator only schedules; never reads/writes derived state directly (it reads KB)
- Codex components are read-only in the filesystem (enforced by sandbox)
- Runtime orchestration data lives under `runtime/`, not `events/`

### 6.2 Generator

Produces `<node>` blocks via Codex. Two modes:
- **fresh**: produce proof for target label from scratch
- **repair**: fix a rejected proof using prior attempt + verdict as context

**Generator's allowed output kinds:**
- `definition` — creating new definitions or revising existing ones
- `lemma`, `theorem`, `proposition` — proof-requiring kinds
- **NOT** `external_theorem` — user-only (requires citation)

Generator role layer rejects any `<node>` block with `kind: external_theorem`
and reports the failure in runtime logs.

**Multi-node output is expected.** One generator attempt for a target
theorem typically produces:
- Proof of the target theorem (one `<node>` for the target)
- Several auxiliary sub-lemmas or sub-theorems (multiple `<node>` blocks
  for supporting results)
- New definitions introduced by the generator (optional)
- Possibly revisions to existing definitions the new proof re-interprets

**Decoder pipeline** (inside generator role.py):

```
Codex stdout
  ↓
decoder:
  1. scan for <node>...</node> blocks
  2. for each block: parse YAML frontmatter + markdown body
  3. validate: full node-schema fields present, non-empty statement, no duplicate labels in batch, label format, kind is allowed,
     explicit `\ref{label}` references resolve (or will resolve by end of attempt)
  4. assemble the full node batch for this run
  5. stage the batch
  6. atomically publish one `generator.batch_committed`
  ↓
events/ directory
  ↓
librarian watches, applies → dag.kz + nodes/*.md
```

After one attempt completes, the KB may have several new / revised
nodes, each entering its own verify-or-regenerate cycle.

**Decoder failure modes:**
- `<node>` block malformed → reject attempt (runtime failure; no truth event emitted)
- `kind: external_theorem` appears → reject (user-only)
- label uniqueness conflict → reject
- placeholder / local label name (`thm:main`, `lem:helper`, etc.) → reject
- unresolved `\ref{}` to non-existent label (and not produced in same attempt) → reject
- **Repair-must-change-hash** (mode=repair only): for the repair target,
  decoder computes the new `verification_hash` from the committed
  statement + proof + current dep `statement_hash`es. If this equals the
  previously rejected `verification_hash`, the batch is rejected. This
  prevents generator from re-emitting identical content and relying on
  verifier non-determinism to escape `count = -1`.

**Intra-batch ordering.** Within one generator batch, the wrapper
topologically orders the included node states by their explicit `\ref{}`
dependency edges before handing the batch to librarian. Librarian applies the
batch in that order inside one batch transaction.

**Prompt composition** (assembled by `role.py`):

1. **Generation prompt** — task description for the target label
2. **Repair context** (if mode=repair) — the target's
   `verification_report` and `repair_hint` from the latest verdict
3. **Latest batch rejection report** (if any) — runtime-only structural
   rejection summary from librarian/coordinator, such as cycle introduction
   or unresolved reference
4. **Repair history summary** — current repair round count and brief
   history summary, so generator can decide between local repair,
   statement revision, or counter-example
5. **Target's current state** — statement (if present) and previous
   proof attempt (if any)

Codex can **freely explore `nodes/` directory** via bash to see verified
dependencies and library structure. Structured prompt is the essential input;
exploration enriches context.

**Repair mode can produce three kinds of outcomes** (choice guided by
verifier's verdict category in `repair_hint` and repair history):

1. **New proof** (typically for `gap` verdicts): statement unchanged;
   fresh proof attempt patches the gap. The committed batch updates the
   target node's proof.

2. **Revised statement + new proof** (typically for `critical` verdicts
   where statement needs adjustment): generator rewrites both in the
   committed batch. The
   `statement_hash` changes → dependents cascade (their count resets).

3. **Counter-example** (extreme `critical` case where generator proves
   the original statement is false): statement revised to its negation
   (`P → ¬P`); proof is the counter-example argument. Same event flow
   as case 2 — this is just the polarity-flipping instance of statement
   revision. No dedicated `theorem_disproven` event needed.

The generator decides the outcome based on `verification_report` +
`repair_hint` passed in via the prompt (role.py injects them). The
target node is typically NOT in `nodes/` (its `pass_count = -1`), so
the prompt carries this context directly.

Internal structure follows original Rethlas (`.agents/skills/`, `.codex/`,
`mcp/`, `AGENTS.md`). Phase I adds a thin `role.py` that:

1. Reads runtime job / CLI dispatch parameters from coordinator
2. Assembles minimal Codex prompt (target label + mode + optional hints)
3. Launches Codex via `codex exec` (see §8 for args)
4. Parses Codex stdout for `<node>` blocks
5. Emits one `generator.batch_committed`

Skill output convention: Codex produces one or more `<node>` blocks:

```
<node>
---
kind: lemma
label: lem:block_form_for_x0_plus_u
remark: Block-form reduction lemma for the target proof.
source_note: ""
---
**Statement.** If $X$ then $Y$.

**Proof.** By \ref{def:primary_object}, ... $\square$
</node>
```

Inside proofs, cross-references use `\ref{label}` (Lean Blueprint
convention).

**Truth events emitted:**
- `generator.batch_committed`

Attempt lifecycle, pid, log path, timeout, and crash information are runtime
state only and live under `runtime/`.

### 6.3 Verifier

**Single `codex exec` call per verification** (matches original
Rethlas design). Codex internally uses its 3 skills via multi-agent
feature:

1. `check-referenced-statements`
2. `verify-sequential-statements`
3. `synthesize-verification-report`

Python `verifier/role.py` invokes Codex once and parses the final
verdict JSON. **No external stage orchestration.**

**Prompt composition (minimal):**

```
Run_id: ...
Target label: lem:block_form_for_x0_plus_u
Statement: <target statement>
Proof: <target proof>   (empty for definition / external_theorem)
+ instructions from AGENTS.md
```

**No dependency context injection.** Codex (verifier) has **free read
access to `knowledge_base/nodes/`** — the entire verified-knowledge
library — via bash (`ls`, `cat`, `grep`, `find`) and can consult any
relevant file on its own:
- Codex sees `\ref{lem:bar}` in the proof
- Codex runs `cat nodes/lem_bar.md` (after label-to-filename conversion)
- The skill `resolve-reference` teaches Codex this convention
- Codex may also browse siblings, search for related lemmas, or
  cross-check definitions — all via bash inside `nodes/`

**How label ↔ filename conversion works:**

| Label | Filename |
| --- | --- |
| `def:primary_object` | `def_primary_object.md` |
| `lem:block_form_for_x0_plus_u` | `lem_block_form_for_x0_plus_u.md` |
| `thm:vogan_green` | `thm_vogan_green.md` |

Colon `:` replaced with underscore `_`. Kind prefix preserved.

**Only count ≥ 1 nodes appear in `nodes/`** (see §6.5). Codex finding
a file = dep is at least once-verified. Missing file = dep not yet
verified, Codex treats as "cannot use".

**Verdict categories:**

| Verdict | Meaning | Count effect |
| --- | --- | --- |
| `accepted` | Node is well-formed / correctly proven | `count += 1` |
| `gap` | Local issue (missing justification, unclear step) — repair can patch | `count = -1` |
| `critical` | Fundamental issue (circular, type mismatch, statement wrong) — may need statement rewrite or counter-example | `count = -1` |

`gap` and `critical` both set count to `-1` but carry different
`repair_hint` semantics: generator seeing `gap` tries local patches;
seeing `critical` considers deeper rewrites or counter-example.

**For definitions:**
- Stage 1: check referenced concepts exist and are accepted
- Stage 2: vacuously passing (empty proof — no step-by-step to verify)
- Stage 3: synthesize verdict — check semantic well-formedness (Codex
  judges if the statement is coherent, not self-contradictory, domain
  terminology correct)
- Full DESIRED audits (runs 3 times like proof-requiring kinds)
- On wrong verdict: `count = -1`; user revises via
  `user.node_revised(kind=definition)` (or generator revises as side-effect of
  repairing a dependent proof-requiring node)

**For external_theorems (independent kind, distinct semantics):**
- Structurally similar to definitions (empty proof) but **semantically
  the content is imported from external literature** — the theorem is
  asserted-true on the basis of user's citation, not derived locally
- Stage 1: check referenced concepts exist
- Stage 2: vacuously passing
- Stage 3: synthesize — check well-formedness (statement coherence,
  citation referenced in the body) + sanity of what's being claimed
- Full DESIRED audits (runs 3 times)
- On wrong verdict: `count = -1`; user revises via
  `user.node_revised(kind=external_theorem)` (generator NEVER touches external
  theorems — they are strictly user-authored, require citation)

Internal structure follows original Rethlas (`.agents/skills/`,
`.codex/`, `mcp/`, `api/`, `AGENTS.md`).

**Truth events emitted:**
- `verifier.run_completed` (final verdict: accepted / gap / critical; includes `verification_hash`, `verification_report`, `repair_hint`)

A single verifier run always targets exactly one node and emits exactly one
`verifier.run_completed` truth event. Coordinator may still run many verifier
subprocesses in parallel on different nodes.

Verifier start/fail/interrupt lifecycle belongs to `runtime/`, not `events/`.

### 6.4 Coordinator

**Pure scheduling. No derived-state work. No parsing. No rendering.**

**Coordinator's state model:** based on current KB plus runtime job state,
coordinator maintains three things in memory:

1. **Generator queue** — `kind ∈ {lemma, theorem, proposition}` nodes
   with `count = -1` (either no proof yet, or latest verdict was
   gap/critical). Generator reads history to decide fresh vs repair.
   Priority: by `label` (stable).
2. **Verifier queue** — all nodes where:
   - `0 ≤ count < DESIRED_COUNT`
   - For every dep: `dep.count > node.count` (strict monotone)
   Priority: `pass_count` ascending.
3. **KB read handle** — opens `dag.kz/` read-only via `common/kb` to
   compute candidates for the two queues on each loop iteration

Queues are **ephemeral** (in-memory) and re-derived from current KB on each
loop start. Coordinator runtime job bookkeeping lives under `runtime/`; there
is no persistent scheduling truth separate from KB state.

**Process-lifetime invariant:** coordinator is the parent of all in-flight
generator and verifier subprocesses. Worker lifetime is subordinate to the
current `rethlas supervise` / coordinator lifetime. If coordinator or
supervise exits or crashes, all in-flight workers are terminated as part of
the same runtime teardown. Restart never assumes an old worker is still
alive.

Decisions (per node, per loop iteration):

```
if any dep is missing: skip (blocked)

if node.pass_count == -1:
    if node.kind in [definition, external_theorem]:
        skip (waiting for user revision; no generator auto-fix)
    else:
        → dispatch generator

elif node.pass_count >= DESIRED_COUNT:
    skip (done)

else:
    # count in [0, DESIRED-1]
    # Strict monotone rule applies to ALL deps
    for dep in node.depends_on:
        if dep.pass_count <= node.pass_count:
            skip (wait for dep to advance)
    → dispatch verifier
```

**Verifier dispatch condition** (strict monotone):

A node enters the verifier queue when:
1. `0 <= count < DESIRED_COUNT`
2. For every dep: `dep.count > node.count` (strict greater-than)
3. No broken deps

**No axiom exemption.** Definitions and external_theorems participate in
the same strict monotone rule. Their deps (typically other definitions)
must also be strictly ahead.

**Why strict monotone:** forces bottom-up, pyramid verification. Deepest
leaves advance first; dependents advance one step at a time as upstream
confirms. Every ancestor's verification rests on deps that are
strictly more confirmed.

**Verifier dispatch priority:** `pass_count` ascending — lowest
first, unblocks downstream.

**Global stop condition:** All nodes have `pass_count >= DESIRED_COUNT`.
Default `DESIRED_COUNT = 3`.

By the strict-monotone rule, when top-level theorems reach `DESIRED`,
all their ancestors (including definitions) are at `DESIRED`.

### 6.4.1 Loop prevention and stuck detection

**No concurrent dispatch on same target.** Before dispatching generator
or verifier on a node, coordinator checks current-runtime job state for that
target. If a live/in-flight job exists, skip this loop. Prevents racing
attempts producing conflicting events on the same node.

**Six safeguards against infinite loops / over-verification:**

1. **`pass_count` monotonic per hash**: only `+1` from accepted verdicts;
   only resets via hash change (→ 0) or wrong verdict (→ -1). No
   cycling 0↔1 possible on a stable hash.
2. **Upper bound**: coordinator skips nodes with
   `pass_count ≥ DESIRED_COUNT`. Over-verification impossible.
3. **Hash-match check (librarian)**: verdicts with mismatched hash are
   ignored. No stale verdict pollution.
4. **Pre-dispatch hash revalidation** (§5.5.2): catches Kuzu ↔ data
   drift before dispatch.
5. **Codex log mtime timeout (30 min)**: kills stuck Codex processes
   via `killpg`.
6. **Cycle detection (librarian)**: any truth event that would introduce
   a dependency cycle is rejected at event-application time.

Strict-monotone scheduling relies on this DAG invariant. With no cycles,
leaves can always advance first. If the system stops making progress, the
cause is a real blocked node state (for example a rejected definition,
missing dependency, or unresolved proof repair), not a scheduler-created
dependency loop.

**Stuck detection (periodic audit, every N≈60 loops):**

Coordinator periodically computes a runtime stuck set:
- definitions / external_theorems at `pass_count = -1`
- proof-requiring nodes that have accumulated many repair rounds without
  progress, for operator visibility only

Dashboard highlights these directly from KB state + runtime job status.

If coordinator finds **no dispatchable action** while unfinished nodes remain,
it must first run cycle detection against the current projected graph before
classifying the system as merely stuck. Diagnostic order:

1. dependency cycle check
2. broken / missing dependency check
3. user-blocked definition / external_theorem check
4. proof-repair-needed check
5. repeated-no-progress visibility diagnostics

**Definitions / external_theorems at pass_count=-1** are always stuck
— coordinator never auto-dispatches generator on them. Only
`user.node_revised(kind=definition)` / `user.node_revised(kind=external_theorem)` unblocks.
Dashboard must make this obvious.

Coordinator writes no truth events in Phase I. Dispatch, start, timeout, and
crash records are runtime state only.

If librarian rejects a generator batch (for example due to a dependency cycle,
unresolved reference, or malformed batch), the entire batch is discarded and
no truth event is applied from it. The rejection is recorded in runtime state
as a **batch rejection report**. This is distinct from a verifier
`verification_report`: verifier reports are mathematical judgments on a
single node/proof, while batch rejection reports are structural/runtime
diagnostics about why a proposed generator batch could not enter truth.

### 6.5 Librarian

Maintains dag.kz and nodes/. Single writer for both. Projects events into
the derived state.

Main loop:
```
watch events/ for new files
for each new event (ordered by event_id):
    validate schema + business rules
    apply to Kuzu transactionally
    recompute hashes for affected nodes (BFS through dependents)
    update pass_count per rules
    re-render nodes/*.md for each affected node
    on validation failure: record runtime validation failure
```

Startup: regenerate all `nodes/*.md` from Kuzu (ensures consistency even
after crash).

Full rebuild is triggered by `rethlas rebuild`. It deletes dag.kz and nodes/
and replays the entire truth event stream.

Librarian writes no truth events in Phase I. Validation failures and rebuild
status are reported via runtime logs / CLI output.

### 6.6 Linter

Read-only audit. Phase I scope:

- **A. Event stream integrity**: filename ↔ frontmatter consistency,
  `event_id` uniqueness, references to prior events exist
- **B. KB structural invariants**: no cycles, label uniqueness,
  kind-appropriate fields
- **C. `pass_count` audit** (§5.5.1): for every node, recompute
  `audit_count` from the event stream and assert it matches stored
  `Node.pass_count`. Catches librarian drift on the one field that drives
  all scheduling decisions.

Phase I does NOT implement:
- Full projection drift detection (replay all events into a fresh KB and
  diff against live Kuzu). Only `pass_count` is audited in Phase I.
- Clock skew detection

Linter writes no truth events in Phase I.

### 6.7 Dashboard

HTTP server, read-only. Phase I is a **linear HTML view**, no interactive
graph.

Pages:
- `GET /` — workspace overview (theorems list + health + stuck nodes + rejected events)
- `GET /api/theorems` — JSON: all `kind=theorem` nodes and their progress
- `GET /api/active` — JSON: currently in-flight runtime jobs
- `GET /api/stuck` — JSON: nodes with `pass_count=-1` that need human:
  - kind ∈ {definition, external_theorem} at count=-1 (user must revise)
  - proof-requiring kinds with many repair rounds and no visible progress
- `GET /api/rejected` — JSON: recent runtime validation failures so user sees
  which dropped files failed validation
- `GET /api/events?limit=50` — JSON: recent events, filterable by actor/type
- `GET /api/node/{label}` — JSON: full node info
- `GET /events/stream` — SSE: push new events to connected browsers

Frontend: vanilla HTML + minimal JS. No React / Vue / Cytoscape.

**Must prominently surface (so user doesn't miss):**
- Definitions / external_theorems at `pass_count=-1` (user must revise;
  no auto-recovery)
- Proof-requiring nodes with many repeated repair rounds (advisory; user
  may want to intervene)
- Recent runtime validation failures / rejected generator batches

Phase II will add interactive DAG visualization (Cytoscape.js) and Blueprint
LaTeX export.

### 6.8 Common library

```
common/
├── kb/              # KnowledgeBase interface + Kuzu backend
├── events/          # event read/write/parse/validate
├── runtime/         # codex_runner (Popen + log-mtime timeout)
└── config/          # rethlas.toml loader
```

No `common/mcp/` — generator MCP code lives in the generator tree; verifier
uses no Phase I MCP tools.

---

## 7. Codex Invocation

### 7.1 Invocation arguments

```bash
codex exec \
    -C <workspace>/knowledge_base/nodes/ \
    -m <model> \
    --config 'model_reasoning_effort="<effort>"' \
    --sandbox read-only \
    "<prompt>"
```

- `cwd` is locked to `nodes/` — Codex only sees per-node markdown
- `--sandbox read-only` — Codex cannot write files; stdout is captured by
  wrapper for parsing
- Bypass-sandbox is NOT used in Phase I (replaced by `read-only`)

### 7.2 What Codex can do

- Read `nodes/*.md` via bash (`ls`, `cat`, `grep`, `find`)
- Call MCP tools (registered in `.codex/config.toml`)
- Stream output to stdout (captured as log)

### 7.3 What Codex cannot do

- Write, modify, or delete any file (enforced by sandbox)
- Read outside `nodes/` (cwd + sandbox restrict)
- Access `events/`, `dag.kz/`, `rethlas.toml`, or anything else in workspace
  — only via MCP

### 7.4 Liveness monitoring

**Only signal:** mtime of Codex subprocess log file at
`runtime/logs/{job_id}.codex.log`.

**Rule:** If log mtime > 30 minutes old, coordinator sends SIGINT to the
process group (`os.killpg`), waits 10s, then SIGKILL and marks the runtime
job timed out.

No separate heartbeat file. No status JSON. Log file mtime is the only
signal.

### 7.5 Permissions enforced vs convention

- **Sandbox-enforced:** no writes, no reads outside nodes/
- **Convention (skill discipline):** Codex produces `<node>` blocks in
  final output; wrapper parses and emits events on Codex's behalf

Even if Codex attempts an illegal operation, the sandbox denies it.
Malformed output is caught by the wrapper's parser and rejected.

---

## 8. MCP Tools

**Only generator uses MCP in Phase I.** No other component uses MCP for any
truth-bearing purpose. Python components (coordinator, librarian, linter,
dashboard) use `common/kb` directly. Rethlas does not expose an MCP server to
external callers.

Generator's MCP server process is launched by Codex per invocation. Code
duplication is acceptable at this scale.

### 8.1 Generator-only tools

| Tool | Purpose |
| --- | --- |
| `search_arxiv_theorems(query)` | External literature search via leansearch.net (existing) |
| `memory_init` / `memory_append` / `memory_search` | Codex scratchpad (existing) |

### 8.2 Verifier-only tools

Phase I: none.

### 8.3 Semantic search (deferred)

`search_relevant(query, top_k)` using embedding-based similarity is
**Phase II**. Phase I relies on `grep` over `nodes/` and the existing
arxiv search.

---

## 9. Data Flow

### 9.1 Writing an event

```
truth-event producer:
  construct event payload
  allocate event_id ({iso_ms}-{seq}-{uid})
  compose filename per §3.2
  write .tmp file in events/{date}/
  fsync
  atomic rename to canonical filename
```

**Who writes truth events:**
- **User**: writes event files by hand or via a helper CLI (`rethlas add-definition`, etc. — future helper CLI). Phase I uses `.json` truth files in `events/{date}/`.
- **Generator / verifier**: their wrapper code (Python, NOT Codex) writes truth events. Codex subprocess is read-only sandboxed; it outputs to stdout which the wrapper captures and converts to truth events.

**Codex never writes files.** Only Python wrappers do.

### 9.2 Projecting an event

```
librarian file-watcher notices new file
load event
validate schema + business rules
apply to Kuzu (transaction):
  update atomic fields
  recompute affected nodes' hashes (BFS through dependents)
  update pass_count per rules
commit
re-render nodes/*.md for each affected node (atomic writes)
```

### 9.3 A typical verification cycle

```
1. coordinator reads KB: finds proof-requiring node with count=0 and every dep.count > node.count
2. coordinator creates a verifier runtime job for `lem:foo`
3. wrapper launches codex exec with nodes/ cwd, read-only sandbox
4. Codex reads the target statement/proof from the prompt and dep files from `nodes/`; runs 3-stage skills
5. wrapper parses verdict JSON and emits verifier.run_completed
6. librarian applies verifier.run_completed: count increments if accepted+hash matches
7. coordinator on next loop sees count=1; if DESIRED_COUNT=3 dispatches audit
```

### 9.4 Communication constraint

Truth-bearing components communicate only through `events/` and `dag.kz/`.
Runtime orchestration uses local subprocess control and files under `runtime/`.

---

## 10. Coordinator Scheduling Details

### 10.1 DESIRED_COUNT threshold

Default: 3. Configurable via `rethlas.toml` `[scheduling]
desired_pass_count`.

- Phase I intentionally keeps `DESIRED_COUNT = 3` even with a single
  verifier backend. The main reason is to exercise strict-monotone
  scheduling, repeated verifier dispatch, pass-count advancement, recovery,
  and stop-condition logic under realistic multi-round coordination.
- These three passes provide repeated LLM checks, but they are **not**
  multi-backend consensus.
- Phase II with audit backend: could be 1 primary + 2 audits across
  different backends.

### 10.2 Verification dispatch priority

Candidate nodes are filtered by:
- `proof` non-empty (or kind is definition / external_theorem)
- `pass_count in [0, DESIRED_COUNT)`
- For every dep: `dep.pass_count > node.pass_count` (strict monotone, §6.4)

Sorted by `pass_count` ascending (count=0 first, then count=1, etc.).
Ties broken by `label`.

### 10.3 Concurrency

Existing `common/codex_budget` slot mechanism caps concurrent Codex
subprocesses. Coordinator acquires slot before dispatching; releases on
completion.

### 10.4 Repair rounds

Phase I does **not** impose a hard repair budget. Proof-requiring nodes at
`count = -1` continue to be dispatched to generator.

Coordinator and dashboard still track repair round count as derived
observability data. Generator receives that history in repair mode so it can
decide whether to:

- continue local proof repair
- revise the statement
- pursue a counter-example / negation route

Repeated repair without progress is therefore advisory signal, not a hard
dispatch stop condition.

---

## 11. Recovery

### 11.1 The recovery equation

```bash
rm -rf knowledge_base/ runtime/
rethlas rebuild
```

Librarian reads all `events/` and rebuilds `dag.kz/` and `nodes/`.
`runtime/` is ephemeral and recreated on demand.

### 11.2 Crash scenarios

| Failure | Effect | Recovery |
| --- | --- | --- |
| Producer crashes mid-write | `.tmp` file lingers | Deleted on next startup |
| Librarian crashes mid-apply | Kuzu transaction rolls back | Next startup replays from truth events |
| Kuzu DB corrupt | Queries fail | `rethlas rebuild` |
| Full workspace clone | No dag.kz | `rethlas rebuild` on first use |
| Codex subprocess stuck | Log mtime stales | Coordinator timeout kills process group |
| Supervise crash | Child daemons die | Restart supervise; runtime/ cleared |

### 11.3 Schema evolution

New event types, new fields, new producer kinds are append-only changes.
Existing events stay valid. `rethlas rebuild` always uses current
projection logic.

---

## 12. Core Invariants

1. **Truth events are the sole source of mathematical truth.** `dag.kz/` and
   `nodes/` are derived.
2. **Events are immutable** after atomic rename. Compensate by adding
   events, never mutate.
3. **All content is recoverable from events.** Event files are
   self-contained.
4. **Librarian is the only writer of `dag.kz/` and `nodes/`.**
5. **Coordinator only coordinates.** Does not parse, derive, or render.
6. **Linter only reports, never repairs.**
7. **Truth-bearing components communicate via truth events + KB.**
   Runtime orchestration uses local subprocess control and `runtime/`.
8. **Python components use `common/kb` directly.** Generator uses MCP.
   Verifier has no Phase I MCP tools.
9. **Codex is read-only on filesystem.** Sandbox-enforced.
10. **Truth-event producers are intentionally small in Phase I.**
    `user`, `generator`, and `verifier` are the only truth writers.
11. **No status field stored.** All status is derived from atomic fields
    (count, hashes) at query time.
12. **`pass_count` is the only progress indicator.** Signed int
    (-1 / 0 / positive), updated by librarian per rules.

---

## 13. Design Decisions

### 13.1 Why event sourcing

- Mutable blueprint + derived caches (original Rethlas) had drift issues
- Regex-extracted dependency edges made the DAG second-class
- Event sourcing makes truth append-only and derivations refreshable

### 13.2 Why Kuzu

- Embedded (like SQLite) but native Cypher
- Graph queries (closure, cycles) are 1-3 lines in Cypher vs 10-20 in SQL
- MIT licensed, Python bindings, zero setup

### 13.3 Why per-node markdown files

- Codex is trained on bash (`ls`, `cat`, `grep`) — natural browsing
- Avoids teaching Codex how to query Kuzu
- Works with `--sandbox read-only` (Codex can read, librarian writes)
- Human-readable and grep-able

### 13.4 Why no status enum

- Status was always derivable from atomic fields
- Storing it created a cache-invalidation problem (cascade logic)
- Deriving keeps data model minimal and consistent by construction

### 13.5 Why Merkle hashes

- One hash comparison checks entire dependency chain integrity
- Changing an upstream statement automatically invalidates all downstream
  verdicts (hashes cascade)
- Dep's proof changes don't propagate (statement_hash doesn't include proof)

### 13.6 Why `pass_count` signed (-1, 0, N)

- Single field captures three states: wrong, pending, verified-N-times
- Eliminates need for `status` enum, `latest_verdict_event_id`, etc.
- Coordinator decisions reduce to numeric comparisons

### 13.7 Why Codex sandbox read-only

- Previous `--dangerously-bypass-approvals-and-sandbox` gave Codex
  unrestricted file access
- Restricting to `nodes/` read-only eliminates attack surface
- Generator and verifier only need to *read* knowledge and *emit* output to
  stdout
- Wrapper parses stdout and writes events; Codex never writes files

### 13.8 Why MCP only for Codex

- Python components don't need tool-use abstraction — they call functions
  directly
- MCP adds tool-call overhead and indirection
- Codex is agent SDK with native MCP support; we use it where it fits

### 13.9 Why 30-min Codex log timeout

- xhigh reasoning can legitimately think silently for 10+ minutes
- Official Codex docs don't specify hard upper bound
- 30 min is conservative empirical value used in existing inducedorbit runs
- Simpler than multi-tier warnings (yellow/red thresholds)

### 13.10 Why signed count vs unsigned + separate wrong flag

- Coordinator decisions all become `if count < 0 / == 0 / < DESIRED` —
  compact
- One field vs two fields (less drift opportunity)

### 13.11 What we're NOT doing

- No complex cascade field semantics — status simply derives from
  current hashes
- No `common/mcp/` shared module — each agent has its own
- No `adapters/codex/` nesting — Phase II if/when Claude is added
- No runtime heartbeat files — log mtime suffices
- No status enum in Kuzu

---

## 14. Open Items

These are resolved at implementation time without blocking design:

1. **`rethlas init` scaffolding** — what a fresh workspace contains
2. **Generator `memory_*` MCP tools** — keep as-is or migrate to event-driven notes
3. **Frontend minimal HTML/JS** — exact markup and style for Phase I
   dashboard
4. **API key management** — likely environment variables; respect existing
   Codex conventions

See `PHASE1.md` for the concrete task list.

---

## 15. Changelog

- **2026-04-23**: Initial consolidated design from session discussions.
- **2026-04-24**: Truth-vs-runtime separation; Phase I truth producers
  restricted to user/generator/verifier; `pass_count` renamed from
  `verification_count`; 10-mechanism prevention-first design for
  `pass_count` correctness; single-call verifier; no dep-context
  injection; nodes/*.md filter to pass_count>=1 only; `generator.batch_committed`
  atomic batch event; strict label naming; retraction removed; no hard
  MAX_REPAIRS; repair-must-change-hash decoder check.
