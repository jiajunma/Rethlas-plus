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

- Event-sourced knowledge base with append-only events as single truth
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
│   ├── runtime/         # codex_runner + heartbeat
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
├── events/                         # ⭐ only truth, append-only, git-tracked
│   └── {date}/*.{md,json}
├── knowledge_base/                 # derived, gitignored
│   ├── dag.kz/                     # Kuzu graph DB (Python components use this)
│   └── nodes/                      # per-node markdown (Codex reads this)
│       └── {kind_prefix}_{label}.md
└── runtime/                        # ephemeral, gitignored
    └── logs/
        └── {dispatch_event_id}.codex.log
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
rethlas supervise                          # run projector + coordinator + dashboard
rethlas dashboard --port 8765              # dashboard only
rethlas linter --mode fast                 # one-shot audit
rethlas rebuild                            # rebuild dag.kz + nodes/ from events
```

Default workspace is cwd. `--workspace <path>` overrides.

---

## 3. Truth Layer: `events/`

Events are the **only** source of truth. Every state change is an event file.
All derived artifacts (Kuzu DB, nodes/*.md files) can be reconstructed from
the event stream.

### 3.1 Core invariants

1. **Append-only.** Once a file is committed to its canonical path, it is
   never modified, moved, or deleted.
2. **Self-contained.** Each event file contains every piece of information
   needed to interpret it. Event bodies inline content directly. No external
   blob storage.
3. **Events may reference other events** by `event_id`. Since the referenced
   event is in `events/`, truth stays self-contained.
4. **Atomic publication.** Write `.tmp` + rename atomically. No partial
   events visible.

### 3.2 File naming

```
{iso_ms}--{event_type}--{target_or_none}--{actor}--{uid}.{md|json}
```

- **`iso_ms`**: compact ISO with millisecond precision (`20260423T143015.123`)
- **`event_type`**: dotted name (`user.definition_added`, `verifier.run_completed`)
- **`target_or_none`**: DAG node label with `:` escaped to `_`, or `none`
- **`actor`**: producer identifier `{kind}:{instance}` with `:` escaped to `_`
- **`uid`**: 8 hex characters for same-millisecond collision safety
- **Extension**: `.md` for events with markdown body, `.json` for purely structured

Examples:
```
20260423T143015.123--user.definition_added--def_primary_object--user_alice--a7b2c912.md
20260423T144522.999--verifier.run_completed--lem_key_step--verifier_codex-gpt-5.4--1f8e22c0.json
20260423T150000.000--coordinator.phase_changed--none--coordinator_local--77bb3344.json
```

Directories sharded by date: `events/{YYYY-MM-DD}/`.

Filename metadata is informational; frontmatter / JSON body is authoritative.
Linter checks filename-body consistency.

### 3.3 Event formats

**`.md` (YAML frontmatter + markdown body)** — used when event carries
substantial text (statements, proofs):

```markdown
---
event_id: 20260423T143015.123
type: user.definition_added
actor: user:alice
target: def:primary_object
depends_on: [def:base_structure, def:supporting_concept]
kind: definition
---

A *primary object* is ...
```

**`.json`** — used for purely structured events (verdicts, dispatches):

```json
{
  "event_id": "20260423T144522.999",
  "type": "verifier.run_completed",
  "actor": "verifier:codex-gpt-5.4",
  "target": "lem:key_step",
  "parent_event_id": "20260423T143830.111",
  "payload": {
    "verification_hash": "sha256:...",
    "verdict": "accepted",
    "report": { ... }
  }
}
```

### 3.4 Core event schema fields

| Field            | Required | Description                                  |
| ---------------- | -------- | -------------------------------------------- |
| `event_id`       | ✓        | ISO + ms timestamp, unique                   |
| `type`           | ✓        | `{producer_kind}.{action}` dotted name       |
| `actor`          | ✓        | `{kind}:{instance}` string                   |
| `target`         | —        | DAG node label if applicable                 |
| `parent_event_id`| —        | Causal predecessor                           |
| `ts`             | ✓        | ISO timestamp (matches `event_id` prefix)    |
| `cost`           | —        | Per-event resource consumption, see §3.6     |
| `payload`        | varies   | Structured data or markdown body             |

### 3.6 Per-event cost tracking

Events that consume LLM resources (primarily `generator.attempt_produced`,
`verifier.run_stage_completed`, `verifier.run_completed`) record
consumption inline in the event frontmatter or JSON:

```yaml
cost:
  input_tokens: 12345
  output_tokens: 6789
  reasoning_tokens: 234567
  cost_usd: 0.042
  duration_seconds: 890
```

Dashboard / linter aggregate these by querying the event stream. There is
no separate cost-tracking store. No budget enforcement in Phase I — just
observability.

### 3.5 Producer openness

Producers are an open set. `actor` and `type` are free strings. New
producers register in `producers.toml`. The projector rejects events whose
`actor` / `type` don't match registered patterns (unless in permissive
mode).

Phase I producers:
- `user:<name>` — human author
- `generator:codex-gpt-5.4-xhigh` — Codex generator
- `verifier:codex-gpt-5.4` — Codex verifier
- `coordinator:local` — scheduling
- `librarian:local` — projection
- `linter:local` — audit

---

## 4. Derived Layer

The DAG is derived from events. Two representations are kept in sync by
librarian:

- **`dag.kz/`** — Kuzu graph database (used by Python components)
- **`nodes/*.md`** — per-node markdown files (used by Codex via bash)

Both are gitignored and rebuildable.

### 4.1 KuzuDB

- Embedded (no server process)
- Native Cypher query language
- Good for graph queries (closure, cycles, recursion)
- Python binding is first-class
- Sole writer: librarian
- Readers: coordinator, linter, dashboard, librarian

### 4.2 Per-node markdown files

`knowledge_base/nodes/{kind_prefix}_{label_sanitized}.md`. Flat directory.
Kind prefixes: `def_`, `xthm_`, `lem_`, `thm_`, `prop_`, `problem_`.

Contents: label, kind, statement, current proof, dependencies, verdict
history — everything humans or Codex want to see about the node in one
file.

Codex browses via bash (`ls`, `cat`, `grep`, `find`) inside this directory
with `cwd = nodes/` and `--sandbox read-only`.

### 4.3 Access interface: `common/kb/KnowledgeBase`

Python components access the KB through a Protocol, decoupling them from
the backend.

```python
class KnowledgeBase(Protocol):
    # reads
    def get_node(self, label: str) -> Node | None: ...
    def list_nodes(self, *, kind=None, goal=None, retracted=None) -> list[Node]: ...
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

| Kind | Has proof? | User-attested? | Initial verification_count |
| --- | --- | --- | --- |
| `definition` | no | yes | **1** |
| `external_theorem` | no | yes | **1** |
| `open_problem` | yes | no | 0 |
| `lemma` | yes | no | 0 |
| `theorem` | yes | no | 0 |
| `proposition` | yes | no | 0 |

### 5.2 Kuzu Node table

```cypher
CREATE NODE TABLE Node (
  label STRING PRIMARY KEY,
  kind STRING,
  goal BOOLEAN DEFAULT false,
  statement_event_id STRING,
  current_proof_event_id STRING,      -- NULL for axioms
  statement_hash STRING,
  verification_hash STRING,
  verification_count INT DEFAULT 0,   -- signed: -1 / 0 / positive
  retracted BOOLEAN DEFAULT false,
  disproven BOOLEAN DEFAULT false,
  disproved_by STRING,                -- label of counter-example when disproven
  updated_at STRING
);

CREATE REL TABLE DependsOn (FROM Node TO Node, event_id STRING);
```

**Not stored:**
- `status` enum
- `latest_verdict_event_id`
- `strongly_accepted`
- Any "fresh / stale" marker

### 5.3 Merkle hashes

Two recursive hashes capture node identity:

```
statement_hash(n) = sha256(
    n.statement
    + ";".join(dep.statement_hash for dep in sorted(n.depends_on))
)

verification_hash(n) = sha256(
    statement_hash(n)
    + (n.proof or "")
)
```

- `statement_hash` is what deps transmit upward: **only statement, not proof**
- `verification_hash` adds the local proof on top of `statement_hash`
- For axioms (`definition` / `external_theorem`): `verification_hash == statement_hash` (no proof)

**Propagation rule:** When any node's `statement_hash` changes, all
dependents' `statement_hash` recompute (BFS up dependents). Each affected
node's `verification_hash` also recomputes.

**Key property:** Changing a dep's proof (while keeping its statement
unchanged) does NOT invalidate my verdict — dep's `statement_hash` didn't
change, so my `statement_hash` and `verification_hash` didn't change.

### 5.4 `verification_count` semantics

Signed integer. Librarian maintains per event.

| Value | Meaning |
| --- | --- |
| `-1` | Verifier judged current hash as wrong |
| `0` | Has proof, awaiting first successful verification (or just post-repair) |
| `>= 1` | Verified N times (independent runs) against current hash |

**Update rules:**

| Event | Effect |
| --- | --- |
| `user.definition_added` | Create node, `count = 1` |
| `user.external_theorem_added` | Create node, `count = 1` |
| `user.open_problem_created` | Create node, `count = 0` |
| `generator.node_statement_added` (for lemma/etc. without proof yet) | Create node, `count = 0` |
| `generator.node_proof_produced` | Update proof; hash changes; `count = 0` (for proof-requiring kinds; axioms unaffected) |
| `user.definition_revised` | Update statement; hash changes; propagate to dependents (their hash changes → `count = 0`) |
| `verifier.run_completed(verdict=accepted, hash matches)` | `count += 1` |
| `verifier.run_completed(verdict=wrong, hash matches)` | `count = -1` |
| `verifier.run_completed(hash mismatch)` | Ignored (stale verdict) |
| Any event causing `verification_hash` change on proof-requiring node | `count = 0` (takes precedence over accepted / wrong semantics) |
| `user.node_retracted` | `retracted = true` |
| `user.theorem_disproven(target=X, by=Y)` or `generator.theorem_disproven(...)` | See §5.6 Counter-examples |

### 5.6 Counter-examples and disproven theorems

A theorem can be proven **wrong in statement** (not just its proof). When
this happens, a **counter-example** (another theorem whose statement
contradicts the target) is added to the KB and linked to the disproven
target.

**Node fields:**
- `disproven` BOOLEAN DEFAULT false — set when a counter-example invalidates
  the theorem
- `disproved_by` STRING — label of the counter-example theorem

**Event:** `user.theorem_disproven(target=X, by=Y)` (or
`generator.theorem_disproven(...)` emitted by generator after failed
repairs + counter-example discovery).

**Librarian's action on this event:**

1. Ensure Y exists as a node (counter-example theorem, kind=theorem)
2. Set `X.disproven = true` and `X.disproved_by = Y`
3. For every transitive dependent D of X:
   a. In D's `depends_on` edges: replace reference to X with reference to Y
   b. Recompute D's `statement_hash` and `verification_hash`
   c. Set `D.verification_count = -1` (forces repair, not just re-verification)
4. BFS through dependents applying (a) (b) (c)

**Effect:** All dependents' proofs are flagged as needing rewrite. The
structural dependency graph is updated so they now depend on the
counter-example Y. Coordinator sees count=-1 and dispatches generator in
repair mode. Generator reads updated `nodes/*.md` (which shows D depends
on Y now) and rewrites proofs accordingly.

**Distinction from retraction:**
- `retracted = true` (node gone) → dependents become blocked (can't proceed)
- `disproven = true` (node present but statement is wrong) → dependents
  get count=-1 (repair dispatched, with counter-example as replacement dep)

### 5.7 No status enum

The system does not store or define a `status` field. Queries derive
decisions directly from the atomic fields:

| Question | Query |
| --- | --- |
| Needs fresh proof? | `current_proof_event_id is None` and kind is proof-requiring |
| Needs verification? | `verification_count == 0` and deps all `count >= 1` |
| Needs repair? | `verification_count == -1` and repair attempts `< MAX_REPAIRS` |
| Complete? | `verification_count >= DESIRED_COUNT` (default 3) |
| Blocked (deps broken)? | Any dep is retracted or missing |

For display, dashboard composes a label string on the fly.

---

## 6. Components

### 6.1 Overview

| Component | Writes events | Writes dag.kz | Writes nodes/ | Uses MCP | Backend |
| --- | --- | --- | --- | --- | --- |
| **generator** | ✓ (via wrapper) | ✗ | ✗ | ✓ (own MCP server) | Codex |
| **verifier** | ✓ (via wrapper) | ✗ | ✗ | ✓ (own MCP server) | Codex |
| **coordinator** | ✓ (own events) | ✗ | ✗ | ✗ | Python |
| **librarian** | ✓ (meta events) | **✓ (sole)** | **✓ (sole)** | ✗ | Python |
| **linter** | ✓ (drift events) | ✗ | ✗ | ✗ | Python |
| **dashboard** | ✗ | ✗ | ✗ | ✗ | Python HTTP |

Hard invariants:
- Librarian is the only writer of `dag.kz` and `nodes/`
- Coordinator only schedules; never reads/writes derived state directly (it reads KB)
- Codex components are read-only in the filesystem (enforced by sandbox)

### 6.2 Generator

Produces proof `<node>` blocks via Codex. Two modes:
- **fresh**: produce proof for target label from scratch
- **repair**: fix a rejected proof using prior attempt + verdict as context

Internal structure follows original Rethlas (`.agents/skills/`, `.codex/`,
`mcp/`, `AGENTS.md`). Phase I adds a thin `role.py` that:

1. Reads dispatch event from coordinator
2. Assembles minimal Codex prompt (target label + mode + optional hints)
3. Launches Codex via `codex exec` (see §8 for args)
4. Parses Codex stdout for `<node>` blocks
5. Emits fine-grained events per node

Skill output convention: Codex produces one or more `<node>` blocks:

```
<node>
---
kind: lemma
label: lem:helper
depends_on: [def:primary_object]
---
**Statement.** If $X$ then $Y$.

**Proof.** By \ref{def:primary_object}, ... $\square$
</node>
```

Inside proofs, cross-references use `\ref{label}` (Lean Blueprint
convention).

**Events emitted:**
- `generator.attempt_started` (with pid, codex_log_path)
- `generator.node_statement_added`
- `generator.node_proof_produced`
- `generator.attempt_produced` (session marker)
- `generator.attempt_failed` (parse error, Codex timeout, etc.)
- `generator.attempt_interrupted` (SIGTERM during run)

### 6.3 Verifier

Runs a 3-stage pipeline via Codex, orchestrated by `role.py`:

1. `check-referenced-statements` — all `\ref{}` in proof exist and are usable
2. `verify-sequential-statements` — each proof step logically follows
3. `synthesize-verification-report` — consolidate into verdict

Each stage is a separate Codex call. The role layer chains them.

Internal structure follows original Rethlas (`.agents/skills/`, `.codex/`,
`mcp/`, `api/`, `AGENTS.md`).

**Events emitted:**
- `verifier.run_dispatched` (by coordinator; verifier picks it up)
- `verifier.run_stage_completed` (×3)
- `verifier.run_completed` (final verdict, includes `verification_hash`)
- `verifier.run_failed` / `verifier.run_interrupted`

### 6.4 Coordinator

**Pure scheduling. No derived-state work. No parsing. No rendering.**

Reads KB, decides dispatches, emits coordinator events.

Decisions (per node, per loop iteration):

```
if node.kind in [definition, external_theorem]: skip (axiom)
if node.retracted: skip
if any dep is missing or retracted: skip (blocked)
if node.current_proof_event_id is None:
    → dispatch generator (fresh)
elif node.verification_count == -1:
    if repair_attempts < MAX_REPAIRS: → dispatch generator (repair)
    else: skip (exhausted)
elif node.verification_count == 0:
    if all deps have count >= 1: → dispatch verifier
    else: skip (wait)
elif 1 <= node.verification_count < DESIRED_COUNT:
    → dispatch verifier (audit)
else:  # count >= DESIRED_COUNT
    skip (done)
```

Verifier dispatch priority: `verification_count` ascending (lowest first).

**Global stop condition:** All proof-requiring nodes have
`verification_count >= DESIRED_COUNT`. Default `DESIRED_COUNT = 3`.

**Events emitted:**
- `coordinator.phase_changed`
- `coordinator.dispatch_generator`
- `coordinator.dispatch_verifier`
- `coordinator.attempt_timed_out` (Codex log stale)
- `coordinator.attempt_crashed` (process dead)
- `coordinator.full_rebuild_requested`
- `coordinator.rate_limit_backoff`

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
    update verification_count per rules
    re-render nodes/*.md for each affected node
    on validation failure: emit librarian.event_rejected
```

Startup: regenerate all `nodes/*.md` from Kuzu (ensures consistency even
after crash).

Full rebuild: triggered by `coordinator.full_rebuild_requested` event or
`rethlas rebuild` command. Deletes dag.kz and nodes/, replays entire event
stream.

**Events emitted:**
- `librarian.event_rejected` (schema or business rule violation)
- `librarian.rebuild_started`
- `librarian.rebuild_completed`

### 6.6 Linter

Read-only audit. Phase I scope:

- **A. Event stream integrity**: filename ↔ frontmatter consistency,
  `event_id` uniqueness, references to prior events exist
- **B. KB structural invariants**: no cycles, label uniqueness,
  kind-appropriate fields

Phase I does NOT implement:
- C. Projection drift detection (full replay vs current Kuzu)
- Clock skew detection

**Events emitted:**
- `linter.run_completed` (with counts per check)
- `linter.invariant_violated`

### 6.7 Dashboard

HTTP server, read-only. Phase I is a **linear HTML view**, no interactive
graph.

Pages:
- `GET /` — workspace overview (goals list + health summary)
- `GET /api/goals` — JSON: all `kind=open_problem` nodes with progress
- `GET /api/active` — JSON: currently in-flight dispatches (from recent events)
- `GET /api/events?limit=50` — JSON: recent events, filterable by actor/type
- `GET /api/node/{label}` — JSON: full node info (from Kuzu + event refs)
- `GET /events/stream` — SSE: push new events to connected browsers

Frontend: vanilla HTML + minimal JS. No React / Vue / Cytoscape.

Phase II will add interactive DAG visualization (Cytoscape.js) and Blueprint
LaTeX export.

### 6.8 Common library

```
common/
├── kb/              # KnowledgeBase interface + Kuzu backend
├── events/          # event read/write/parse/validate
├── runtime/         # codex_runner (Popen + heartbeat + timeout)
└── config/          # rethlas.toml loader
```

No `common/mcp/` — MCP server code lives in each agent's own `mcp/`
directory.

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
`runtime/logs/{dispatch_event_id}.codex.log`.

**Rule:** If log mtime > 30 minutes old, coordinator sends SIGINT to the
process group (`os.killpg`), waits 10s, then SIGKILL. Emits
`coordinator.attempt_timed_out`.

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

**Only generator and verifier use MCP.** No other component uses MCP for
any purpose. Python components (coordinator, librarian, linter, dashboard)
use `common/kb` directly. Rethlas does not expose an MCP server to external
callers.

Each of generator and verifier has its own MCP server process, launched by
Codex per invocation. They share no code (code duplication is acceptable
at this scale).

### 8.1 Shared MCP tools (both generator and verifier)

These tools address the fact that Codex can't read events/ directly
(sandbox limits it to nodes/).

| Tool | Purpose |
| --- | --- |
| `get_event(event_id)` | Read a specific event's body (for repair mode to see prior proof + verdict) |
| `closure(label, direction, depth)` | Graph closure query (cheaper than bash recursion) |

### 8.2 Generator-only tools

| Tool | Purpose |
| --- | --- |
| `search_arxiv_theorems(query)` | External literature search via leansearch.net (existing) |
| `verify_proof_service(statement, proof)` | Self-check by invoking verifier (existing) |
| `memory_init` / `memory_append` / `memory_search` | Codex scratchpad (existing) |

### 8.3 Verifier-only tools

Phase I: no extra tools beyond shared.

### 8.4 Semantic search (deferred)

`search_relevant(query, top_k)` using embedding-based similarity is
**Phase II**. Phase I relies on `grep` over `nodes/` and the existing
arxiv search.

---

## 9. Data Flow

### 9.1 Writing an event

```
producer:
  construct event payload
  allocate event_id (iso + ms) + uid
  compose filename per §3.2
  write .tmp file in events/{date}/
  fsync
  atomic rename to canonical filename
```

### 9.2 Projecting an event

```
librarian file-watcher notices new file
load event
validate schema + business rules
apply to Kuzu (transaction):
  update atomic fields
  recompute affected nodes' hashes (BFS through dependents)
  update verification_count per rules
commit
re-render nodes/*.md for each affected node (atomic writes)
```

### 9.3 A typical verification cycle

```
1. coordinator reads KB: finds proof-requiring node with count=0, deps all count>=1
2. coordinator emits: coordinator.dispatch_verifier(target=lem:foo)
3. verifier wrapper picks up dispatch, emits verifier.run_dispatched
4. wrapper launches codex exec with nodes/ cwd, read-only sandbox
5. Codex reads nodes/lem_foo.md and dep files; runs 3-stage skills
6. wrapper emits verifier.run_stage_completed (×3), then verifier.run_completed
7. librarian applies verifier.run_completed: count increments if accepted+hash matches
8. coordinator on next loop sees count=1; if DESIRED_COUNT=3 dispatches audit
```

### 9.4 Communication constraint

Components communicate only through `events/` and `dag.kz/`. No RPC, no
shared memory, no direct function calls across components. This keeps them
loosely coupled and independently restartable.

---

## 10. Coordinator Scheduling Details

### 10.1 DESIRED_COUNT threshold

Default: 3. Configurable via `rethlas.toml` `[scheduling]
desired_verification_count`.

- Phase I with single backend: all 3 verifications run on same backend.
  Effectively triple-check.
- Phase II with audit backend: could be 1 primary + 2 audits across
  different backends.

### 10.2 Verification dispatch priority

Candidate nodes are filtered by:
- Proof-requiring kind
- Not retracted
- All direct deps have `verification_count >= 1`
- Has `current_proof_event_id`
- `verification_count < DESIRED_COUNT`
- `verification_count != -1` (those go to repair)

Sorted by `verification_count` ascending (count=0 first, then count=1, etc.).
Ties broken by `label`.

### 10.3 Concurrency

Existing `common/codex_budget` slot mechanism caps concurrent Codex
subprocesses. Coordinator acquires slot before dispatching; releases on
completion.

### 10.4 Repair exhaustion

When `count == -1` and `count_repair_attempts_for_current_hash(label) >=
MAX_REPAIRS` (default 3), coordinator stops dispatching repair. Emits
`coordinator.repair_exhausted`. Node stays at `count = -1` until:
- User adds a hint
- User revises a dependency
- User retracts the node

User intervention effectively clears the repair count (implementation: only
count repair attempts since the last user-produced event targeting this
node).

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
| Librarian crashes mid-apply | Kuzu transaction rolls back | Next startup replays from `last_applied_event_id` |
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

1. **Events are the sole source of truth.** `dag.kz/` and `nodes/` are
   derived.
2. **Events are immutable** after atomic rename. Compensate by adding
   events, never mutate.
3. **All content is recoverable from events.** Event files are
   self-contained.
4. **Librarian is the only writer of `dag.kz/` and `nodes/`.**
5. **Coordinator only coordinates.** Does not parse, derive, or render.
6. **Linter only reports, never repairs.**
7. **Components communicate only via events + KB.** No direct RPC.
8. **Python components use `common/kb` directly.** Codex components use
   MCP. No cross-language direct calls.
9. **Codex is read-only on filesystem.** Sandbox-enforced.
10. **Producer identity is open.** `actor` and `type` are strings;
    producers register in `producers.toml`.
11. **No status field stored.** All status is derived from atomic fields
    (count, hashes, retracted) at query time.
12. **`verification_count` is the only progress indicator.** Signed int
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

### 13.6 Why `verification_count` signed (-1, 0, N)

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
- No runtime/heartbeat files — log mtime suffices
- No status enum in Kuzu

---

## 14. Open Items

These are resolved at implementation time without blocking design:

1. **`rethlas init` scaffolding** — what a fresh workspace contains
2. **`memory_*` MCP tools** — keep as-is or migrate to event-driven notes
3. **Frontend minimal HTML/JS** — exact markup and style for Phase I
   dashboard
4. **API key management** — likely environment variables; respect existing
   Codex conventions

See `PHASE1.md` for the concrete task list.

---

## 15. Changelog

- **2026-04-23**: Initial consolidated design from session discussions.
