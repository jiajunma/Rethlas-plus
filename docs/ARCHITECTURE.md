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
- Windows support (see platform note below)

**Supported platforms (Phase I):** Linux and macOS. The design relies
on `flock` advisory locks, POSIX `O_APPEND < PIPE_BUF` atomic writes,
process groups (`os.setsid` / `os.killpg`), and POSIX signals
(SIGTERM / SIGINT / SIGKILL). Windows equivalents exist but require
different primitives; Phase I does not invest in abstracting that
layer.

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
    │   └── {job_id}.json           # pid, kind, target, mode, dispatch_hash,
    │                               # started_at, updated_at, status
    ├── logs/
    │   ├── {job_id}.codex.log      # Codex subprocess stdout
    │   ├── supervise.log           # coordinator (incl. supervisor role) own log
    │   ├── librarian.log           # librarian daemon own log
    │   └── dashboard.log           # dashboard daemon own log
    ├── locks/
    │   └── supervise.lock          # advisory flock held by coordinator (§6.4)
    └── state/
        ├── coordinator.json              # coordinator heartbeat / queue / children
        ├── librarian.json                # librarian heartbeat / projection summary
        ├── rejected_writes.jsonl         # recent decoder/admission rejections
        ├── drift_alerts.jsonl            # rare hash/runtime drift alerts
        └── rebuild_in_progress.flag      # present only while rebuild is mid-run
                                          # (presence ⇒ librarian re-runs rebuild on startup)
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
rethlas add-node ...                       # publish a user node event
rethlas attach-hint ...                    # publish a user hint event
rethlas supervise                          # run librarian + coordinator + dashboard
rethlas dashboard --bind 127.0.0.1:8765    # dashboard only (§6.7.1)
rethlas linter --mode fast                 # one-shot audit
rethlas rebuild                            # rebuild dag.kz + nodes/ from events
```

Default workspace is cwd. **All Phase I CLIs** (`init`, `add-node`,
`revise-node`, `attach-hint`, `supervise`, `dashboard`, `linter`,
`rebuild`) accept `--workspace <path>` to override cwd; the flag
always resolves to an absolute path before use.

**Exit codes (all CLIs):**

| Code | Meaning |
| --- | --- |
| `0` | Success / clean shutdown |
| `1` | Generic error (unexpected exception; check logs) |
| `2` | Workspace `supervise.lock` already held (`supervise`, `rebuild`) OR workspace not initialized (`supervise`, `linter`, `rebuild` without prior `init`) |
| `3` | Critical child crash loop that coordinator cannot recover from (librarian restarts more than once within 3 min, §6.4) |
| `4` | Config error — malformed `rethlas.toml`, out-of-range value, unknown file/directory layout (§2.4), or workspace not writable (cannot create `runtime/locks/supervise.lock`) |
| `5` | Linter found violations (only for `rethlas linter`) |
| `6` | `rethlas init` refused because workspace already has `events/` or `rethlas.toml` |

Codes are stable across Phase I CLIs for scripting and CI use.

### 2.4 Workspace config `rethlas.toml`

Known sections in Phase I:

```toml
[scheduling]
desired_pass_count             = 3       # §10.1
generator_workers              = 2       # §10.3
verifier_workers               = 4       # §10.3
codex_silent_timeout_seconds   = 1800    # §7.4 — log mtime staleness kill threshold

[dashboard]
bind                           = "127.0.0.1:8765"    # §6.7.1
```

- **Missing file**: all fields default as shown above.
- **Missing section**: that section's fields default as shown.
- **Missing field**: defaults to the shown value.
- **Unknown field**: logged as a startup warning; value ignored.
- **Malformed TOML (parse error) or out-of-range value**: startup
  fails with a non-zero exit code and a human-readable error pointing
  at the offending line. Fail-fast rather than silently falling back
  to defaults, since a malformed config is almost always a user error
  worth surfacing.

Validation bounds:

| Field | Required shape |
| --- | --- |
| `desired_pass_count` | integer, `>= 1` |
| `generator_workers` | integer, `>= 1` |
| `verifier_workers` | integer, `>= 1` |
| `codex_silent_timeout_seconds` | integer, `>= 60` (floor prevents accidentally killing all in-flight Codex work) |
| `bind` | string matching `HOST:PORT`; `PORT` in `[1, 65535]`; `HOST` any value parseable as IPv4, IPv6, or hostname |

Reload semantics: `rethlas.toml` is read once at process startup
(supervise / dashboard / linter each load independently). Editing it
while `rethlas supervise` is running has no effect until restart.

**Timestamp convention (workspace-wide).** All timestamps Rethlas
writes to `runtime/`, `AppliedEvent`, admin JSONL logs, and CLI
stdout / dashboard display use **UTC ISO 8601 with `Z` suffix**
(e.g. `2026-04-24T14:05:12.123Z`). This covers
`coordinator.json.{started_at, updated_at}`,
`librarian.json.{started_at, updated_at, last_rebuild_at}`,
`AppliedEvent.applied_at`, `runtime/jobs/*.json.{started_at,
updated_at}`, `rejected_writes.jsonl.ts`, `drift_alerts.jsonl.ts`,
`linter_report.json.ts`, and `rebuild_in_progress.flag.started_at`.

The one exception is the truth event body's `ts` field (§3.3), which
keeps its full local-offset ISO form (e.g.
`2026-04-23T14:30:15.123+08:00`) to preserve the producing
operator's wall-clock context as historical metadata. Sorting /
ordering never reads `ts`; only `iso_ms` drives replay order, and
that's already UTC (§3.2 / E1).

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
6. **Three-layer validation (H29 revision).** Admission, projection,
   and verification each guard a strictly different concern. Earlier
   drafts conflated content judgments with admission and projection;
   the current boundary is:
   - **Admission** (pre-publish, inside each producer's role layer):
      **structural** correctness only — schema completeness, actor /
      type registration, the parsed batch can be assembled into a
      writable event payload (every `<node>` block parses, the
      dispatch target appears in `nodes[]`, no two non-identical
      blocks share a label, repair-must-change-hash is honored).
      *Content* questions — forbidden kinds, prefix-kind mismatch,
      placeholder labels, existing non-target labels, self-reference,
      unresolved `\ref{}`, batch-internal cycles — are NOT decoder
      concerns; the librarian projector and the verifier handle them.
      Structural failures never enter `events/`; they are recorded in
      `runtime/state/rejected_writes.jsonl` along with the parsed
      batch content so the next repair attempt can rebuild from the
      rejected draft.
   - **Projection** (librarian, at apply time): **physical KB
     integrity** only — label uniqueness against already-applied
     nodes (`label_conflict`), kind immutability on `user.node_revised`
     (`kind_mutation`), real DAG-cycle introduction against the
     projected graph (`cycle`), verifier-verdict hash match
     (`hash_mismatch`), `user.hint_attached` target exists
     (`hint_target_missing`), schema envelope (`schema`), and event
     tampering (`workspace_corruption`). Notably **NOT** at the
     projector: `\ref{X}` resolution, self-reference detection — those
     are content questions for the verifier. When the projector
     records `apply_failed`, the event file stays in `events/`
     (append-only); it simply does not alter KB state. This is a
     **routine outcome**, not corruption (§6.5).
   - **Verification** (verifier, at run time): **content correctness** —
     proof validity, `\ref{X}` actually resolves to a non-empty
     verified node (emits `external_reference_checks[].status =
     "missing_from_nodes"` otherwise), suspicious self / cyclic
     citation patterns, claim-vs-statement consistency, etc. The
     verifier emits `verdict ∈ {accepted, gap, critical}` plus a
     `verification_report` and `repair_hint` that the generator's
     next dispatch consumes (§5.4 / §6.3).
   - **Workspace corruption**: an event in canonical `events/` that
     violates an invariant admission should have caught (for example a
     hand-dropped file with wrong schema). Librarian halts projection
     and surfaces the event; operator must investigate.
7. **`events/` records proposals; KB records realization.** Every
   published event is preserved. `AppliedEvent` (a Kuzu table
   maintained by librarian) records the deterministic outcome
   (`applied` / `apply_failed`) for each event. `KB = f(events/)` is
   still a pure function; `f` includes the apply_failed rule and is
   deterministic under replay.
8. **Apply_failed is terminal.** An event that fails projection is
   never retried even if later events would make it applicable. The
   producer must publish a fresh event with a new `event_id`.

### 3.2 File naming

```
{iso_ms}--{event_type}--{target_or_none}--{actor}--{seq}--{uid}.json
```

- **`iso_ms`**: compact ISO with millisecond precision
  (`20260423T143015.123`), **always expressed in UTC**. The `Z`
  suffix is omitted for brevity, but the value is strictly UTC so
  that `(iso_ms, seq, uid)` lexicographic sort is a faithful global
  causal-order extension even when the event stream is shared
  across machines in different local time zones (§11.4).
- **`event_type`**: dotted name (`user.node_added`, `verifier.run_completed`)
- **`target_or_none`**: DAG node label with `:` escaped to `_`, or `none`
- **`actor`**: producer identifier `{kind}:{instance}` with `:` escaped to `_`
- **`seq`**: zero-padded monotone sequence within the producer's
  same-millisecond emission batch (scope: per-producer, per-ms). In
  Phase I each truth event is published standalone, so `seq` is almost
  always `0001`; reserved for future multi-event bursts
- **`uid`**: 16 hex characters (64-bit random) for uniqueness and
  filename ↔ event_id consistency
- **Extension**: `.json` only in Phase I

Examples:
```
20260423T143015.123--user.node_added--def_primary_object--user_alice--0001--a7b2c912d4f1e380.json
20260423T144522.999--verifier.run_completed--lem_block_form_for_x0_plus_u--verifier_codex-gpt-5.4--0001--1f8e22c0b6a94d17.json
```

Directories sharded by date: `events/{YYYY-MM-DD}/`.

In Phase I, filename metadata is informational and the JSON body is
authoritative.
Linter checks filename-body consistency.

### 3.3 Event formats

**All truth events use `.json` in Phase I.** Human-readable markdown views
belong to derived `knowledge_base/nodes/*.md`, not to `events/`.

```json
{
  "event_id": "20260423T143015.123-0001-a7b2c912d4f1e380",
  "ts": "2026-04-23T14:30:15.123+08:00",
  "type": "user.node_added",
  "actor": "user:alice",
  "target": "def:primary_object",
  "payload": {
    "kind": "definition",
    "statement": "A primary object is ...",
    "proof": "",
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
| `ts`             | ✓        | Full ISO timestamp (matches `event_id` prefix) in local time with offset (§2.4 trailer — truth event bodies keep local-offset form; all other timestamps use UTC Z) |
| `cost`           | —        | Per-event resource consumption, see §3.6     |
| `payload`        | ✓        | Structured event payload                     |

Phase I has **no `parent_event_id` / explicit causal-chain field**.
Causality is implicit from `(iso_ms, seq, uid)` replay order and from
semantic references like a verifier verdict's `verification_hash`
matching the node's current hash. Phase II may add an explicit
predecessor link if downstream tooling (provenance graphs, replay
debugging) needs one.

### 3.5 Producer openness

Truth-event producers are an intentionally small set in Phase I. New
producers are possible in later phases, but Phase I truth events are limited
to `user`, `generator`, and `verifier`. Admission rejects truth events whose
`actor` / `type` don't match registered patterns; librarian re-validates the
same rule during replay.

Phase I truth producers:
- `user:<name>` — human author
- `generator:codex-gpt-5.4-xhigh` — Codex generator
- `verifier:codex-gpt-5.4` — Codex verifier

The registry lives in `producers.toml` at the **Rethlas repo root**
(not the workspace; the registry travels with the tool version).
Phase I content:

```toml
[[producer]]
kind                 = "user"
actor_pattern        = "^user:[a-zA-Z0-9_-]+$"
allowed_event_types  = [
  "user.node_added",
  "user.node_revised",
  "user.hint_attached",
]

[[producer]]
kind                 = "generator"
actor_pattern        = "^generator:[a-zA-Z0-9_.-]+$"
allowed_event_types  = ["generator.batch_committed"]

[[producer]]
kind                 = "verifier"
actor_pattern        = "^verifier:[a-zA-Z0-9_.-]+$"
allowed_event_types  = ["verifier.run_completed"]
```

Admission and librarian both validate `(actor, type)` against this
registry. Unknown producer kinds, actor strings that don't match any
`actor_pattern`, or `(kind, type)` pairs outside the allowed list are
rejected:
- Admission: recorded in `runtime/state/rejected_writes.jsonl`; nothing
  enters `events/`.
- Librarian (defensive replay): counts as **workspace corruption**
  (§3.1.6) — projection halts.

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

Admission further requires that `target` refers to a node with
`pass_count <= 0` at admission time. Attaching a hint to a node that
is already verified (`pass_count >= 1`) is rejected at publish: the
hint has no reachable consumer, because `repair_hint` is read only
on a generator dispatch (`pass_count = -1`), and any path from
`pass_count >= 1` back to -1 goes through a `verification_hash`
change that would clear `repair_hint` before the hint could be read
(see §5.2, §5.4). Within the admitted band (`pass_count ∈ {-1, 0}`)
the hint is guaranteed reachable: at `pass_count = 0` it survives
into any subsequent gap/critical verdict (verifier events do not
change `verification_hash`), and at `pass_count = -1` it is shipped
into the generator's job file in both fresh and repair mode per
§10.2.3 — so the hint reaches the first attempt even on a
never-verified node. Post-publish, if the node is deleted (currently
impossible in Phase I) or the hash has changed between admission
and apply, librarian records
`apply_failed(reason=hint_target_missing)` or the admission-time
check against `pass_count >= 1` is re-evaluated as
`hint_target_unreachable`.

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

Each `external_reference_checks[]` item carries `location`,
`reference`, `status`, and `notes`. Phase I `status` values are
`verified_in_nodes`, `verified_external_theorem_node`,
`missing_from_nodes`, `insufficient_information`, and
`not_applicable`.

Expected consistency:
- `accepted` ⇒ `gaps = []` and `critical_errors = []`
- `gap` ⇒ `gaps` non-empty
- `critical` ⇒ `critical_errors` non-empty

Generator batch-commit events carry:

```json
{
  "attempt_id": "gen-20260424T101530.123-0001-a7b2c912d4f1e380",
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

**Prefix ↔ kind strict mapping** (enforced at admission):

| Prefix | Kind |
| --- | --- |
| `def` | `definition` |
| `ext` | `external_theorem` |
| `lem` | `lemma` |
| `prop` | `proposition` |
| `thm` | `theorem` |

The prefix must correspond to the node's `kind`. A label like
`thm:foo` with `kind: lemma` is rejected at admission (in both user
CLI and generator decoder). Since `kind` is immutable (§5.1), this
mapping is stable for the lifetime of the node and gives dashboard /
linter / Codex a reliable visual cue.

Rules:
- Label should roughly describe the mathematical content or role of the node
- Label must remain meaningful when read in isolation later
- Local / positional names are invalid
- Prefix must match kind per the table above

Invalid examples:
- `thm:main` (placeholder)
- `lem:helper` (placeholder)
- `prop:claim1` (placeholder)
- `lem:key_step` (placeholder)
- `def:object` (placeholder)
- `thm:barbasch_signed_tableau_rule` with `kind: external_theorem`
  (prefix/kind mismatch; should be `ext:...`)

Valid style examples:
- `def:primary_object` (kind=definition)
- `ext:barbasch_signed_tableau_rule` (kind=external_theorem)
- `lem:block_form_for_x0_plus_u` (kind=lemma)
- `prop:symplectic_sign_pair_for_even_block` (kind=proposition)
- `thm:maximal_orbits_equal_open_orbits` (kind=theorem)

Admission validates label syntax, placeholder rejection, and
prefix/kind correspondence in Phase I.

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

This section collects the rules for how truth events reach `events/` in
a form that librarian can replay deterministically. Canonical `events/`
contains **admitted proposals**: producers validate structural correctness
before publish (§3.1.6); librarian decides semantic realization via
`AppliedEvent` at apply time.

#### 3.7.1 Stable event ordering

Librarian replay order must be stable and deterministic. Sorting is by:

1. `iso_ms`
2. `seq`
3. `uid`

`uid` is retained for uniqueness; it has no primary ordering meaning.
Within a same-millisecond producer burst, `seq` is the canonical order.
In Phase I each truth event is published standalone, so `seq` is almost
always `0001`.

**Allocation rules for producers** (no cross-producer global
monotonicity required):

1. `iso_ms = utc_wall_clock_now()` at the moment of event
   construction. Use `datetime.datetime.now(tz=datetime.timezone.utc)`
   in Python (or equivalent) — not local time, not naive datetime.
   **Clock-backward defense**: each long-running producer (coordinator,
   librarian, generator wrapper, verifier wrapper) remembers the
   `iso_ms` of its last-emitted event in memory (`last_emitted_iso_ms`).
   At allocation, if `utc_wall_clock_now() <= last_emitted_iso_ms`
   (NTP step-back, manual clock change, VM migration, etc.), the
   producer uses `last_emitted_iso_ms + 1 ms` instead of the raw wall
   clock. This preserves per-producer strict monotonicity without
   coordination. It does **not** enforce cross-producer monotonicity
   (§3.7.1 explicitly drops that requirement); per-producer
   monotonicity is enough because `(iso_ms, seq, uid)` total sort
   still orders events deterministically for replay.
2. `seq`: per-producer, per-ms counter. Starts at `0001`; increments
   only if the same producer publishes multiple events in the same
   millisecond (rare in Phase I). Scope resets on every new ms.
3. `uid = random(64 bits, 16 hex)`. 64-bit entropy makes collision
   negligible over Phase I workspace lifetime (birthday threshold is at
   2^32 ≈ 4 billion events).
4. **Per-machine wall-clock monotonicity required.** NTP slew is fine;
   a step backwards is not. Phase I assumes a single machine; operator
   must avoid manual clock changes during workspace activity.
5. No "sort strictly after current workspace maximum" check.
   Concurrent publishers from different producers do **not**
   synchronize on event_id allocation. Their events are genuinely
   independent; replay order is decided by the `(iso_ms, seq, uid)`
   total sort, not by publication physical order.

Concurrency conflicts that survive admission (label uniqueness, cycle
introduction, `\ref{}` target missing, `hint` target missing, verdict
hash mismatch) are resolved **deterministically at apply time** by
librarian's projection rule: **first to apply in `(iso_ms, seq, uid)`
order wins; later conflicting events are marked `apply_failed`**
(§3.1.6, §6.5). Apply_failed is terminal — a failed event is never
retried.

Consequence: `events/` is append-only and self-contained; `AppliedEvent`
makes projection outcome deterministic and queryable; `KB = f(events/)`
is still a pure function of the event stream.

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
- **Sole process holding the Kuzu handle: librarian.** Librarian is
  **passive**: it applies an event to Kuzu only when coordinator sends
  it an "apply event" command (§6.5 / §6.4). Librarian itself does
  not watch `events/`. It also serves **read** queries that other
  processes need, over the same IPC command channel.
- **Readers** (coordinator, dashboard, linter, user CLI for
  `AppliedEvent` polling in §9.1) do **not** open Kuzu directly —
  they send a `QUERY` command over librarian's IPC channel and
  receive a structured reply (§6.5). Librarian is also a reader of
  its own writes.
- **Workers** (generator / verifier `role.py` wrappers) **do not
  access Kuzu at all.** Workers are pure file-I/O: they read their
  own `runtime/jobs/{job_id}.json` for pre-validated context
  (target statement / proof / `dispatch_hash` / repair context /
  dep `statement_hash`es), they read `nodes/*.md` for verified-dep
  content, and they write events to `events/` + status updates to
  their own job file. Pre-dispatch validation is coordinator's job
  (§5.5.2); AppliedEvent polling on a worker's published event is
  also coordinator's job (§6.7.1). This keeps workers trivial to
  test and keeps Kuzu connections bounded to one daemon.

**Revised concurrency model (2026-04-25).** Phase I's pre-M2 Kuzu
stress validation (PHASE1.md L106-145) discovered that Kuzu 0.11.3
uses an **exclusive file lock** on the database directory regardless
of read/write intent — see
[Kuzu concurrency docs](https://docs.kuzudb.com/concurrency). The
original "single-writer / multi-reader" assumption across OS
processes is therefore not achievable: while librarian holds the
write lock, no other OS process can open the DB even read-only.
Phase I pivots to a **single-process model**:
- Only **librarian** opens the Kuzu database. One process, one
  handle.
- Multiple **in-process connections** (`kuzu.Connection`) inside
  librarian serve concurrent read RPCs; this is the "multi-reader"
  mode that Kuzu does support.
- Every non-librarian process that needs KB state reaches it via
  librarian's IPC channel. The same channel already carries
  `APPLY(event_id, path)` writes (§6.5 K2); it now also carries
  `QUERY(...)` reads returning JSON.
- The hash-match gate in §5.5.0 remains the correctness backstop
  for any drift between coordinator's pre-dispatch snapshot and
  librarian's apply-time state.

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
statement_hash: sha256:a7b2c912d4f1e380...
verification_hash: sha256:1f8e22c0b6a94d17...
depends_on:
  - def:primary_object
  - lem:normal_form_for_x0
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
- `statement_hash`: current `statement_hash` (§5.3); workers read this
  from frontmatter when they need a dep's hash without touching Kuzu
- `verification_hash`: current `verification_hash` (§5.3); exposed for
  the same reason — workers are Kuzu-free (§4.1)
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
Codex encounters `\ref{lem:foo}` in a proof, the
`check-referenced-statements` skill teaches it to `cat nodes/lem_foo.md`
(colon→underscore) to see the dependency.

**Rendering contract (deterministic, byte-exact).** Startup
reconciliation (§6.5) and linter category E (§6.6) both check
`nodes/*.md` by comparing on-disk bytes against the output of
re-rendering the node from current Kuzu state. For that to work
without false "drift" every run, rendering must produce **exactly the
same bytes** for the same input. Rules:

- **Line endings**: Unix `\n` only. No `\r\n`.
- **Character encoding**: UTF-8, Unicode NFC normalization applied
  before write.
- **Trailing newline**: the file ends with exactly one `\n`.
- **YAML frontmatter key order (fixed)**: `label`, `kind`,
  `pass_count`, `statement_hash`, `verification_hash`, `depends_on`.
  No other keys.
- **YAML value formatting**: scalars serialized via PyYAML with
  `default_flow_style=False`, `allow_unicode=True`,
  `sort_keys=False`; no comments.
- **`depends_on` list**: sorted by label in ASCII lexicographic
  order; deduplicated. Output in YAML block-list form (`- lem:foo`
  on its own line), **not** flow form.
- **Body section order (fixed)**: `Source Note.`, `Remark.`,
  `Statement.`, `Proof.`, each introduced by `**Section Name.**` on
  its own line followed by a blank line, then the content. Sections
  with empty content are **omitted entirely** (no `**Remark.**`
  followed by nothing).
- **No timestamps, no generation version, no host-specific data** in
  the rendered file — everything comes from deterministic Kuzu
  fields.

This rendering function is the canonical one used by librarian's
per-event re-render, startup reconciliation, linter category E's
`--repair-nodes`, and `rethlas rebuild`'s final render pass. All four
must invoke the same function from `librarian/renderer.py` so that
"drift" is a signal of real disagreement, not a false positive from
two rendering paths disagreeing on whitespace.

### 4.3 Access interface: `common/kb/KnowledgeBase`

Python components access the KB through a Protocol, decoupling them from
the backend.

```python
class KnowledgeBase(Protocol):
    # reads — derived node state
    def get_node(self, label: str) -> Node | None: ...
    def list_nodes(self, *, kind=None) -> list[Node]: ...
    def direct_dependencies(self, label: str) -> list[str]: ...
    def dependents(self, label: str) -> list[str]: ...
    def dependency_closure(self, label: str) -> list[Node]: ...
    def detect_cycles(self) -> list[list[str]]: ...
    def latest_verdict(self, label: str, hash: str) -> Event | None: ...
    def repair_count(self, label: str) -> int: ...   # reads Node.repair_count

    # reads — projection decisions (§3.1.6 / §6.5)
    def applied_event_record(
        self, event_id: str
    ) -> AppliedEvent | None: ...                  # full row or None
    def applied_event_status(
        self, event_id: str
    ) -> Literal["applied", "apply_failed", "not_found"]: ...
    def list_apply_failed(
        self, *, since: str | None = None, limit: int = 100
    ) -> list[AppliedEvent]: ...

    # writes (librarian only)
    def apply_event(self, event: Event) -> ApplyOutcome: ...
    def rebuild_from_events(self, events_dir: Path) -> None: ...
```

`AppliedEvent` is a dataclass mirroring the Kuzu table: `event_id`,
`status`, `reason`, `detail`, `applied_at`.

`ApplyOutcome` carries `status` (`"applied"` / `"apply_failed"`),
`reason` (empty if applied), `detail` (human-readable context,
empty if applied), and the set of affected node labels (empty if
apply_failed). Producers / CLI poll `applied_event_status(event_id)`
for a quick yes/no; they call `applied_event_record(event_id)` when
they need the reason + detail to surface to the user. Dashboard uses
`list_apply_failed` to surface recent failures.

Only Python components use this. Codex does not import KB code.

---

## 5. Node Model

### 5.1 Kinds

| Kind | Generator can create? | Initial count | Fix on wrong verdict |
| --- | --- | --- | --- |
| `definition` | ✓ | **0** (needs verify) | user only |
| `external_theorem` | ✗ (user only) | **0** | user only |
| `lemma` | ✓ | **-1** | generator (dispatched auto) |
| `theorem` | ✓ | **-1** | generator |
| `proposition` | ✓ | **-1** | generator |

**Generator's write scope per batch** (§6.2): generator may write *only*
to its own target label (itself a proof-requiring kind — `lemma`,
`theorem`, or `proposition`) and to **brand-new labels not currently in
KB**. It may not revise any other pre-existing node, including existing
definitions. If a generator's repair path seems to require sharpening an
upstream definition, the generator conveys that through the repair
report / hint; the user then revises the definition via
`user.node_revised`.

**All five kinds share the same Node schema** (statement + proof +
hashes + `pass_count` + `repair_count` + hints/reports). They differ
in:

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

No "goal" concept is stored in Phase I. What the user considers a goal is
just a `kind=theorem` node; dashboard / user distinguish goals by naming
and context, but coordinator's stop condition still ranges over **all**
nodes in KB rather than a separately configured goal set.

### 5.2 Kuzu Node table

```cypher
CREATE NODE TABLE Node (
  label STRING PRIMARY KEY,
  kind STRING,                         -- definition | external_theorem | lemma | theorem | proposition
  statement STRING,                    -- always non-empty
  proof STRING,                        -- empty for axioms or unproved
  statement_hash STRING,
  verification_hash STRING,
  pass_count INT DEFAULT -1,           -- -1 / 0 / positive, indexed by verification_hash
  repair_count INT DEFAULT 0,          -- gap/critical verdicts since last statement_hash change
  verification_report STRING,          -- latest verifier report (stage details + verdict)
  repair_hint STRING,                  -- accumulated hints for next repair attempt
  remark STRING,
  source_note STRING
);

CREATE REL TABLE DependsOn (FROM Node TO Node);

-- projection bookkeeping (librarian only)
CREATE NODE TABLE ProjectionState (
  key STRING PRIMARY KEY,
  value STRING
);

-- one row per event librarian has decided on (§3.1.6 / §6.5).
-- makes apply idempotent and makes apply_failed observable to
-- producers, dashboard, linter, and CLI feedback loops.
CREATE NODE TABLE AppliedEvent (
  event_id STRING PRIMARY KEY,
  status STRING,        -- "applied" | "apply_failed"
  reason STRING,        -- "" for applied; failure code otherwise
  detail STRING,        -- human-readable context for apply_failed rows;
                        -- "" for applied rows
  event_sha256 STRING,  -- SHA-256 of the event file's **raw bytes**
                        -- (not canonicalized; any whitespace / formatting
                        -- change counts as tampering) at apply time; used
                        -- by linter category F to detect manual edits to
                        -- events/ (git revert, hand edit, etc.)
  applied_at STRING     -- ISO timestamp when librarian decided
);
```

Defined `reason` codes for `status = "apply_failed"` (Phase I). For
each row where `status = "apply_failed"`, librarian also populates
`detail` with a short human-readable explanation pointing at the
specific trigger (so dashboard can render it without cross-joining
to `events/`):

- `label_conflict` — an add-style operation tried to create a node
  whose label already exists. This applies to `user.node_added` and
  to any brand-new node entry inside `generator.batch_committed`.
  `detail` example: `"label thm:foo already applied by event 202604...-a7b2c912d4f1e380"`
- `target_missing` — a target-bearing event references a node that
  does not exist when existence is required. Applies to
  `user.node_revised`, `verifier.run_completed`, and
  `generator.batch_committed` in repair mode. `detail` names the
  missing target label.
- `write_scope_violation` — a generator batch attempted to revise an
  existing non-target node, omitted its required target node, or
  otherwise violated the generator write-scope invariant (§6.2).
  `detail` names the offending label or missing target.
- `cycle` — applying this event would introduce a dependency cycle.
  `detail` example: `"would close cycle: thm:a -> lem:b -> thm:a"`
- `ref_missing` — an explicit `\ref{}` target does not exist at apply
  time. `detail` example: `"\ref{lem:aux_missing} not found"`
- `hint_target_missing` — `user.hint_attached` target label not found.
  `detail` example: `"target lem:x does not exist"`
- `hint_target_unreachable` — `user.hint_attached` target exists but
  has `pass_count >= 1` at apply time (the hint is semantically
  dormant; see §5.2). Admission already rejects this at publish time;
  this apply-time reason only fires when a concurrent verdict advanced
  the target between admission and apply. `detail` carries the
  target label + pass_count at apply time.
- `hash_mismatch` — `verifier.run_completed` carries a
  `verification_hash` that no longer matches the target node's current
  hash (stale verdict). `detail` carries the stale hash prefix + the
  current hash prefix, each as **12 hex chars** (48 bits) — enough to
  distinguish colliding snapshots in practice while keeping log lines
  readable.
- `kind_mutation` — a revision attempted to change a node's `kind`.
  `detail` carries both old and new kind.
- `self_reference` — a node's own body `\ref{}`s itself (also caught at
  admission; included here for defense-in-depth). `detail` carries
  the offending label.

`detail` is capped at 512 bytes; librarian truncates longer strings
with a trailing `...`.

**Node contents** (logical schema):

- **`statement` / `proof`**: the current text for this node. `statement` is
  always non-empty. `proof` may be empty for axioms or for proof-requiring
  nodes that currently have no proof.
- **Hashes**: for Merkle propagation and verdict matching.
- **`pass_count`**: `-1` means needs generator; `0` means needs
  verifier; `≥ 1` means verified that many times against the current
  `verification_hash`.
- **`repair_count`**: number of `gap` / `critical` verdicts accumulated
  against the node's **current `statement_hash`**. Resets to 0 whenever
  `statement_hash` changes (for any reason: user revision, generator
  batch that touches this node with new statement, or Merkle cascade
  from an upstream statement change). Proof-only rewrites do **not**
  reset `repair_count` — they are successive attempts at the same
  statement. Generator reads `repair_count` during repair dispatch; if
  it is large (e.g. ≥ 3), generator should consider that the statement
  itself may be wrong and pursue a statement revision or counter-example
  instead of another local patch. Phase I coordinator imposes no hard
  threshold — `repair_count` is advisory signal to the generator
  (§6.2, §10.4).
- **`verification_report`**: set by librarian whenever a
  `verifier.run_completed` event arrives — contains the latest
  three-stage structured report (what the verifier found). Generator
  reads this during repair.
- **`repair_hint`**: a textual message generator reads at the start of
  the next repair attempt. Internally structured as one
  verifier-maintained section followed by zero or more
  user-contributed sections, separated by `---` lines:

  ```
  <verifier section — latest verifier gap/critical hint for current hash>
  ---
  [user @ 20260424T143015.123]
  <user hint body>
  ---
  [user @ 20260424T160212.004]
  <another user hint body>
  ```

  Three update rules (§5.4):
  - `verifier.run_completed(gap/critical, hash matches)` **overwrites
    the verifier section**; any user sections stay.
  - `user.hint_attached(target=X)` **appends a new user section** at
    the end (with `---` separator and `[user @ <iso_ms>]` header).
  - When a `generator.batch_committed` or `user.node_revised` changes
    the node's `verification_hash`, `repair_hint` **and**
    `verification_report` are both **cleared entirely** (all prior
    hints/reports apply to a stale hash).

  Admission rule: `user.hint_attached(target=X)` is rejected pre-publish
  if `X.pass_count >= 1` (§3.1.6). Hints are only meaningful while the
  node is awaiting generator repair or verifier check; attaching to an
  already-verified node has no effect in any future path.
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

Let `initial_count(kind, proof)` be the count assigned whenever a node's
authored state (statement / proof / kind) is freshly set by a truth event:

- `kind ∈ {definition, external_theorem}` → `0`
  (these kinds never carry a proof; count=0 means "awaiting verifier
  well-formedness check", not "needs generator")
- `kind ∈ {lemma, theorem, proposition}` and `proof` non-empty → `0`
  (has proof; awaiting verifier)
- `kind ∈ {lemma, theorem, proposition}` and `proof` empty → `-1`
  (needs generator to produce a proof)

Rationale: `-1` is reserved for "needs generator". Definitions /
external_theorems have no proof by design, so "empty proof" is not a
signal that a generator is needed; they go to `0` and wait for the
verifier.

| Event | Effect on target Node |
| --- | --- |
| `user.node_added` | Create node with the full authored state; `pass_count = initial_count(kind, proof)`; `repair_count = 0`; `repair_hint` and `verification_report` start empty |
| `user.node_revised` | Replace the node's full authored state (same label, same kind); hashes recompute; `pass_count = initial_count(kind, proof)`; if `statement_hash` changed, `repair_count = 0`; if `verification_hash` changed, clear `repair_hint` and `verification_report`; Merkle propagates to dependents when statement changes (dependents' `statement_hash` changes → their `repair_count` resets to 0 too) |
| `generator.batch_committed` | Apply the committed node batch atomically; for each included node: create or replace current statement/proof/metadata, recompute hashes, and set `pass_count = initial_count(kind, proof)`; if the node's `statement_hash` changed, `repair_count = 0`; if the node's `verification_hash` changed, clear `repair_hint` and `verification_report`; cascaded dependents reset `repair_count` when their `statement_hash` changes via Merkle propagation |
| `verifier.run_completed(accepted, hash matches)` | `pass_count += 1`; set `verification_report`; `repair_count` unchanged |
| `verifier.run_completed(gap, hash matches)` | `pass_count = initial_count(kind, proof)` (`0` for axioms, `-1` for proof-requiring); `repair_count += 1`; set `verification_report`; **overwrite the verifier section of `repair_hint`** (local fix suggestions); user sections untouched |
| `verifier.run_completed(critical, hash matches)` | `pass_count = initial_count(kind, proof)`; `repair_count += 1`; set `verification_report`; **overwrite the verifier section of `repair_hint`** (may need statement rewrite); user sections untouched |
| `verifier.run_completed(hash mismatch)` | Record `AppliedEvent(status=apply_failed, reason=hash_mismatch)`; no KB change (stale verdict) |
| `user.hint_attached(target=X)` | **Admission** rejects if `X.pass_count >= 1` (hint has no reachable effect). Otherwise at apply time: if `X` exists, **append** a new user section `---\n[user @ <iso_ms>]\n<hint body>` to `X.repair_hint`. If `X` does not exist, record `apply_failed(reason=hint_target_missing)` |

#### 5.4.1 Bugfix log: definition reject must not collapse to `-1`

**Symptom (observed 2026-04-27 on `inducedorbittoy/run`):** generator
decomposed a theorem and introduced a new `definition`
(`def:induced_orbits_from_x0_slice_closure`). Verifier returned
`verdict=gap` with a trivial repair hint ("add a citation for
\(\mathcal O_0\)"). Coordinator stopped scheduling work entirely:
`active_generator_jobs=0`, `active_verifier_jobs=0`,
`idle_reason_code=verification_dep_blocked`. The dependent theorem
`thm:induced_orbit_toy_problem` showed `blocked_on_dependency`. No
human intervention had been requested, yet both worker pools were
quiescent.

**Root cause (two interacting rules):**

1. *Projector rule (`librarian/projector.py:_apply_verifier_run`)* set
   `pass_count = -1` unconditionally on `gap` / `critical`, regardless
   of `kind`. This contradicts the rationale of §5.4 ("`-1` is reserved
   for `needs generator`; definitions ... go to `0` and wait for the
   verifier"). A rejected definition therefore landed at `-1`, the same
   sentinel used for `lemma`s that need a generator.
2. *Classifier rule (`dashboard/state.py:classify_theorem`)* mapped the
   composite (`pass_count == -1` ∧ `kind ∈ {definition, external_theorem}`)
   to `STATUS_USER_BLOCKED`. This in itself is correct *given* that the
   only way for a definition to reach `-1` would be a malformed projector
   write — but combined with rule 1 it fires on every gap verdict.

The two rules together convert any verifier complaint about a
definition into "user must intervene", even when the complaint is a
trivial citation fix the generator could repair on the next dispatch.

**Fix (this revision):**

- §5.4 now states the rejection effect as
  `pass_count = initial_count(kind, proof)` instead of literal `-1`.
  For axioms this is `0` (rejected definitions go back to "awaiting
  verifier" without escalating); for proof-requiring kinds this is
  unchanged (`-1`, "needs generator").
- `librarian/projector.py:_apply_verifier_run` is updated to call
  `initial_count(kind, proof)` rather than hard-coding `-1`.
- `dashboard/state.py:classify_theorem` updates its `user_blocked`
  trigger: a node is `user_blocked` when it is an axiom that has been
  rejected at least once *for the same statement_hash* (i.e.
  `kind ∈ {definition, external_theorem}` ∧ `pass_count == 0` ∧
  `repair_count > 0`). The user is the one who can fix the definition,
  but only after the verifier has seen it; brand-new (`repair_count == 0`)
  axioms are simply `needs_verification`.

**Migration:** existing nodes already at `pass_count == -1` with kind
∈ {definition, external_theorem} are stuck in the old representation.
The librarian rebuild (`rethlas rebuild`) re-projects from `events/`
using the corrected rule, so a one-shot rebuild migrates them in
place. No event format changes; only the projection function changed.

#### 5.4.2 Provenance: `introduced_by_actor` lets the generator repair its own helper definitions

**Symptom (observed 2026-04-27, after the §5.4.1 fix landed):** the
coordinator was no longer deadlocking, but every verifier complaint
about a generator-introduced helper definition (e.g. `def:helper_X`
brand-new label inside a `generator.batch_committed` payload)
escalated to `STATUS_USER_BLOCKED`. The user had asked to be
hands-off after the initial question, yet trivial nitpicks ("symbol
`X_0` not stated to lie in `g`") froze the autonomous loop. The
verifier skill update (`verify-sequential-statements/SKILL.md`)
helped one verifier accept, but the second verifier's rerun raised
a fresh nitpick and the cycle repeated.

**Root cause:** §5.4.1 routed *all* axiom rejections to `pass_count = 0`
on the assumption that "definitions belong to the user". That holds
for user-authored `def:` nodes admitted via `user.node_added`, but
not for generator-introduced helpers. Per §6.2 write-scope the
generator may write to brand-new labels inside its batch — those
helpers belong to the generator's repair lane, not the user's.

**Fix:** introduce a per-node `introduced_by_actor` field
(`kind:instance` form, e.g. `user:cli` / `generator:codex-default`).
The projector sets it on first introduction and preserves it across
revisions. Reject routing now branches on `(kind, introduced_by_actor)`:

| Target | `pass_count` after `gap` / `critical` |
| --- | --- |
| Proof-requiring kind (`lemma` / `theorem` / `proposition`) | `-1` (generator pool) |
| Axiom kind, `introduced_by_actor` starts with `generator:` | `-1` (generator pool — its own helper) |
| Axiom kind, `introduced_by_actor` starts with `user:` | `0` (user_blocked once `repair_count > 0`) |

Coordinator (`coordinator/main.py`) widens `gen_pool` to include
generator-introduced axioms at `pass_count == -1`; `user_blocked`
counter and `ver_pool` exclusion are restricted to user-introduced
axioms. Dashboard classifier (`dashboard/state.py:classify_theorem`)
takes `introduced_by_actor` and only flags user-introduced axioms
with `repair_count > 0` as `user_blocked`.

**Implementation:**

- `common/kb/types.py:Node` — new field `introduced_by_actor: str = "user:cli"`,
  preserved on revision; convenience property `introduced_by_generator`.
- `common/kb/kuzu_backend.py` — `Node` table gets an `introduced_by_actor`
  column; `_migrate_introduced_by_actor()` runs `ALTER TABLE Node ADD ...`
  for pre-existing DBs (silenced when the column already exists).
- `librarian/projector.py` — `_apply_node_added` / `_apply_node_revised`
  / `_apply_generator_batch` thread the event's `actor` into Node
  construction; `_apply_verifier_run` branches on provenance.
- `coordinator/main.py` — `gen_pool` adds `(axiom kind ∧
  introduced_by_generator)`; `user_blocked` counter restricted to
  `not introduced_by_generator`; `_decide_idle_reason` matches.
- `dashboard/state.py:classify_theorem` — accepts `introduced_by_actor`
  kwarg; user_blocked branches require user-introduced.

**Migration:** the schema change carries a `DEFAULT 'user:cli'` so old
rows pass through cleanly, but the field reflects the migration
default rather than the real provenance until you run `rethlas rebuild`,
which replays `events/` with the new code path and writes the actual
`event.actor` for each row. After rebuild, generator-introduced
helpers route correctly back to the generator pool on rejection.

### 5.5 Auditability and safety checks

#### 5.5.0 Prevention-first mechanisms for `pass_count` correctness

Before the audit catches drift, multiple mechanisms make drift unlikely
to occur in the first place:

1. **Single writer to Kuzu.** Only librarian writes `dag.kz/`. No race.

2. **Transactional event application.** Librarian wraps each event's
   application in a Kuzu transaction. Crash mid-apply → rollback, event
   not marked applied, retried on next startup.

3. **Idempotent event application via `AppliedEvent`.** Every event
   librarian decides on gets a row in the Kuzu `AppliedEvent` table
   (`status = applied | apply_failed`). Re-processing an already-decided
   event is a no-op. Safe to replay after restart regardless of order.

4. **Strict event ordering.** Events processed by ascending tuple
   `(iso_ms, seq, uid)`. Never apply later event before earlier.
   Deterministic replay → reconstructed `pass_count` always matches.

5. **Hash-match gate on verdicts.** A `verifier.run_completed` event
   changes `pass_count` **only if** its `verification_hash` equals the
   current `Node.verification_hash`. Stale verdicts are recorded as
   `apply_failed(reason=hash_mismatch)` and never affect count.

6. **Pre-dispatch validation** (§5.5.2). Coordinator re-reads Kuzu
   immediately before writing the job file and rejects any
   candidate whose hash / `pass_count` / dep readiness no longer
   matches its earlier selection snapshot. Catches drift *before*
   a new verdict can pollute count.

7. **Canonical hash inputs.** `statement_hash` and `verification_hash`
   use canonical JSON (UTF-8, sorted keys, compact separators,
   newline-normalized). Same state → same hash on every machine, every
   run. Deterministic.

8. **Generator batch atomicity** (§3.7.2). Multi-node generator output
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

`pass_count` stored in Node is a cache whose correctness can be
**independently verified against the event stream**:

```
audit_count(node) =
  # node.kind and node.verification_hash come from current KB state;
  # the event stream provides all facts needed to recompute the count.

  if node.proof is empty and node.kind in [lemma, theorem, proposition]:
    return -1  (proof-requiring node without proof ⇒ needs generator)

  matching_verdicts = [
    e for e in events if
    e.type == "verifier.run_completed" and
    e.target == node.label and
    e.payload.verification_hash == node.verification_hash and
    AppliedEvent[e.event_id].status == "applied"
  ]

  if matching_verdicts is empty:
    return 0  (has proof but not yet verified against current hash)

  last = matching_verdicts[-1]  # by (iso_ms, seq, uid) ordering
  if last.verdict in ("gap", "critical"):
    return -1  (latest matching verdict rejects ⇒ needs repair)

  # latest is accepted; count the accepted verdicts against this hash
  return count(e in matching_verdicts if e.verdict == "accepted")
```

**Inputs needed:** `node.kind`, `node.verification_hash`, and `node.proof`
must be read from current Kuzu state; verdict event facts come from
`events/`; and verdict realization comes from `AppliedEvent`. The audit
is therefore a join of the event stream, the current node snapshot, and
`AppliedEvent`.

Linter's audit check: for every node, recompute `audit_count` from the
event stream and assert it equals the stored `Node.pass_count`.
Drift = librarian bug or corruption.

This makes the stored count a **verifiable** value. The count field is
there for query speed, not for correctness.

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

**`repair_count` audit** (parallel definition for linter category D):

```
audit_repair_count(node) =
  let K = order_key(iso_ms, seq, uid) of the most recent event that caused node's
          statement_hash to take its current value. This is either:
    - a user.node_added / user.node_revised / generator.batch_committed
      event that directly wrote node.label with the current
      statement_hash, OR
    - the upstream event that triggered the Merkle cascade which
      brought node.statement_hash to its current value.

  return count(
    e in events where
      e.type == "verifier.run_completed" and
      e.target == node.label and
      order_key(e.iso_ms, e.seq, e.uid) > K and
      AppliedEvent[e.event_id].status == "applied" and
      e.verdict in ("gap", "critical")
  )
```

**Implementation note:** `AppliedEvent` is a Kuzu table, not part of the
event body. The audit computation must join the event stream (read from
`events/`) with the `AppliedEvent` table (read from Kuzu via
`common/kb`). Concretely: iterate events from `events/`, and for each
candidate check `kb.applied_event_status(e.event_id) == "applied"`.
The join is efficient because `AppliedEvent.event_id` is the primary key.
All event comparisons use the full replay order key `(iso_ms, seq, uid)`,
not `iso_ms` alone.

Linter asserts `audit_repair_count(node) == Node.repair_count` for
every node. Drift = librarian bug.

#### 5.5.2 Pre-dispatch validation (coordinator-owned)

Pre-dispatch validation is entirely **coordinator's** responsibility
and happens **before** the worker subprocess is spawned. Workers do
not access Kuzu (§4.1) and therefore cannot re-check anything;
coordinator validates once, then serializes the validated context
into `runtime/jobs/{job_id}.json` for the worker to trust.

**Conditions checked** by coordinator (covering both pools; specifics
depend on `job.kind`):

| Condition | Verifier | Generator |
| --- | --- | --- |
| target exists in Kuzu | ✓ | ✓ |
| `node.verification_hash == dispatch_hash` (coordinator's own snapshot from a moment earlier — self-consistency) | ✓ | ✓ |
| `pass_count` in expected band (`[0, DESIRED_COUNT)` for verifier, `-1` for generator) | ✓ | ✓ |
| strict-monotone dep condition (`dep.pass_count > node.pass_count`) | ✓ | — |
| all deps at `pass_count >= 1` (so visible in `nodes/`) | — | ✓ |
| kind is proof-requiring (`lem` / `thm` / `prop`) | — | ✓ |
| kind is any of `lem` / `thm` / `prop` / `def` / `ext_thm` (verifier handles well-formedness for `def` / `ext_thm` too — §6.3) | ✓ | — |
| no other in-flight job on the same target (check `runtime/jobs/*.json`) | ✓ | ✓ |
| (repair mode only) most recent gap/critical verdict's `verification_hash` is known and recorded as `H_rejected` | — | ✓ |

If **any** condition fails, coordinator **does not write a job file
at all** — the candidate is simply skipped. Coordinator logs the
failure reason to `runtime/logs/supervise.log` so the operator can
trace "why didn't target X get dispatched this tick". No
`precheck_failed` job record exists (that status was removed; see
§6.7.1).

If all conditions pass, coordinator writes the initial job file with
`status = "starting"` and every context a worker needs:
- `target`, `mode`, `kind` — for generator, `mode` is determined by
  the §10.2.3 rule (`fresh` iff `repair_count = 0`, else `repair`);
  for verifier, `mode = "single"` (§6.7.1)
- `dispatch_hash` (target's `verification_hash` at precheck time)
- target's `statement` and `proof` text (so worker doesn't need to
  reconstruct from Kuzu)
- `dep_statement_hashes`: a map of `{dep_label: statement_hash}`
  covering every label the target's content `\ref{}`s — lets
  generator's decoder compute post-batch `verification_hash`
  without reading Kuzu (§6.2 repair-must-change-hash)
- `repair_hint` — shipped whenever non-empty, in **both** fresh
  and repair mode (§10.2.3). In fresh mode the non-empty value
  comes only from `user.hint_attached` (no verifier section
  possible at `repair_count = 0`); in repair mode it may carry
  both verifier and user sections.
- for generator repair mode only (`repair_count ≥ 1`):
  `verification_report`, `repair_count`, `H_rejected`

After the job file is written, coordinator spawns the worker with
`RETHLAS_WORKSPACE` env + `job_id` positional arg (§6.7.1). Worker
reads the job file, trusts it, runs Codex, publishes its event,
writes `status = "publishing"`, and exits. Coordinator observes the
exit on the next tick, polls `AppliedEvent` for the published
event_id, writes the final status (`applied` / `apply_failed`) back
to the job file, and deletes it (§6.7.1).

**Residual race.** Between coordinator's precheck read and the
worker's eventual publish, librarian may apply a new event that
changes the target's hash. Coordinator's snapshot becomes stale;
the worker runs Codex against old state; the emitted verdict
carries the stale hash; librarian's apply-time hash-match gate
(§5.5.0 #5) catches it and records
`apply_failed(hash_mismatch)`. One wasted worker run per race —
accepted cost (H3), same as before the coordinator/worker
split.

**Purpose:**
- Catches drift between coordinator's precheck snapshot and the
  moment the worker is about to call Codex.
- Keeps workers file-only (no Kuzu binding, no imports of
  `common/kb`).
- Preserves the invariant that only librarian mutates `dag.kz/` and
  `pass_count`.

### 5.6 Merkle cascade (statement changes only)

**When any node's `statement` changes**, or when librarian re-parses
explicit references from the current `statement` / `proof`, its
`statement_hash` recomputes and Merkle propagation recomputes all
dependents' hashes. For each dependent whose `statement_hash`
changed:

- `pass_count = initial_count(dependent.kind, dependent.proof)` per
  §5.4 (so a definition dependent stays at 0 awaiting re-verification,
  a proof-requiring dependent with non-empty proof goes to 0, and a
  proof-requiring dependent with empty proof goes to -1).
- `repair_count = 0` (the statement context shifted; prior failed
  attempts were against the old context and don't carry over).
- Since `verification_hash` also changed (because `statement_hash`
  changed), clear `repair_hint` and `verification_report`.

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
4. X's `pass_count` resets to 0 (hash change); X's `repair_count` resets to 0
5. Via Merkle propagation, all transitive dependents of X also have their
   `statement_hash` / `verification_hash` change → their `pass_count`
   resets per `initial_count(kind, proof)` and `repair_count` resets to 0
6. Verifier re-runs on X (once eligible) — if new proof is valid,
   `X.pass_count` → 1
7. Dependents with proofs reset to `pass_count = 0` because their
   `verification_hash` changed
8. Verifier re-runs on dependents once strict-monotone conditions hold
9. If an old dependent proof no longer works, verifier returns
   `gap/critical` and only then that dependent enters `pass_count = -1`
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
| Needs verification? | `pass_count == 0` and every dep has `pass_count > node.pass_count` |
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
- Generator MCP scratch writes are the one permitted non-truth exception:
  they are performed by the generator MCP server, not by direct Codex
  filesystem writes, and are never replayed by librarian.
- Runtime orchestration data lives under `runtime/`, not `events/`

### 6.2 Generator

Produces `<node>` blocks via Codex. Two modes:
- **fresh**: produce proof for target label from scratch
- **repair**: fix a rejected proof using prior attempt + verdict as context

Coordinator decides the mode per §10.2.3 (`fresh` iff
`repair_count = 0`, else `repair`) and writes it into the job file;
generator `role.py` trusts the value and does not reclassify.

**Generator's allowed output kinds:**
- `definition` — only as a **brand-new** node (new label)
- `lemma`, `theorem`, `proposition` — proof-requiring kinds (brand-new
  or, for the batch's own target label, a revision)
- **NOT** `external_theorem` — user-only (requires citation)

Generator role layer rejects any `<node>` block with `kind: external_theorem`
and reports the failure in runtime logs.

**Generator write-scope invariant** (enforced by decoder, §6.2 failure
modes): each `<node>` block in a batch must be either
1. the batch's primary `target` label (being generated or repaired), or
2. a label that does **not currently exist** in KB (a brand-new node
   introduced by this attempt).

No `<node>` block may target an existing non-target label. Generator
cannot revise an existing definition, an existing auxiliary lemma
(even one it authored in a prior batch), or any other existing node.
If repair would benefit from sharpening an existing upstream
definition, generator surfaces the issue through its
`verification_report` / `repair_hint` so the user can revise it via
`user.node_revised`; or generator introduces a replacement node under
a fresh label.

**Multi-node output is expected.** One generator attempt for a target
theorem typically produces:
- Proof of the target theorem (one `<node>` for the target)
- Several auxiliary sub-lemmas or sub-theorems **under brand-new
  labels** (multiple `<node>` blocks for supporting results)
- New definitions introduced under brand-new labels (optional)

**Decoder pipeline** (inside generator role.py, post-H29 —
structural only per §3.1.6):

```
Codex stdout
  ↓
decoder:
  1. scan for <node>...</node> blocks
  2. for each block: parse YAML frontmatter + markdown body; the
     parser must succeed (valid YAML, required fields present,
     non-empty statement, label slug matches `LABEL_SLUG_RE`, kind
     is a recognised `NodeKind`). Anything else → `malformed_node`.
  3. check no two non-identical blocks share a label
     (byte-identical duplicates auto-collapsed; H27).
  4. confirm the dispatch target appears in the parsed labels.
  5. assemble the full node batch (cycle-tolerant topological
     order — self-loops and unresolved refs are *admitted*; the
     projector and verifier judge them).
  6. mode=repair only: compute the staged batch's post-application
     `verification_hash` for the target and compare to `H_rejected`
     from the dispatch context — equal hashes → `repair_no_change`.
  7. atomically publish one `generator.batch_committed`
  ↓
events/ directory
  ↓
librarian watches, applies → dag.kz + nodes/*.md
     (label uniqueness, kind immutability, hash chain, and full
     graph cycle detection happen **here**; failures surface as
     `apply_failed`, never as decoder rejection.)
```

After one attempt completes, the KB may have several new / revised
nodes, each entering its own verify-or-regenerate cycle.

**Decoder failure modes (post-H29 — structural only):** the decoder
records canonical reason strings to `runtime/state/rejected_writes.jsonl`
and exports them as `REASON_*` constants in `generator/decoder.py`.
Per the §3.1.6 H29 boundary the decoder no longer judges *content*
(forbidden kinds, prefix-kind mismatch, placeholder labels, existing
non-target labels, self-reference, unresolved `\ref{}`, batch-internal
cycles) — those land at the projector (physical-integrity violations)
or the verifier (content gaps). Five reasons survive:
- `no_nodes_in_batch` → stdout parses with zero `<node>` blocks
  (e.g. Codex emitted only prose with no batch). Reject attempt; no
  truth event emitted.
- `malformed_node` — `<node>` block parses but is missing required
  frontmatter, has invalid YAML, lacks a `Statement` body, has an
  unknown `kind` value (the NodeKind enum cannot be constructed),
  or the label slug regex fails. Recorded with the parsed (partial)
  fields so repair can see what the agent intended.
- `duplicate_label_in_batch` — two non-identical `<node>` blocks
  share a label (byte-identical duplicates are auto-collapsed; H27).
- `target_mismatch` — the dispatch target is absent from `nodes[]`;
  the wrapper has no way to fill `payload.target` correctly.
- `repair_no_change` (**Repair-must-change-hash**, mode=repair only):
  decoder must verify that the repair target's **post-batch**
  `verification_hash` differs from the `verification_hash` carried
  by the most recent gap/critical `verifier.run_completed` for that
  target (call it `H_rejected`). Procedure:

  1. Compute each batch node's `statement_hash` using strict
     batch-internal topological order of `\ref{}` edges. Values
     resolved as follows:
     - if the `\ref{}` target label appears in the staged batch (a
       brand-new node introduced by this batch, or the batch's own
       target), use the **batch's** `statement_hash` for that label;
     - otherwise use the **current KB** `statement_hash` for that
       label (existing deps cannot change, per the write-scope
       invariant above).
  2. Assemble the target's post-batch dependency set (resolved via
     the same rule above).
  3. Compute the target's post-batch `verification_hash = H_new`
     from its committed `statement + proof` and the resolved dep
     `statement_hash`es.
  4. If `H_new == H_rejected`, reject the entire batch.

  Rationale: `H_new` must reflect the batch's actual commit effect.
  The target's new `verification_hash` depends on its own committed
  statement/proof plus any newly-introduced dep nodes' `statement_hash`
  values (which live in the batch, not in KB yet). Using the staged
  batch's post-application view captures this correctly; it also
  still blocks genuine no-ops (generator re-emits identical content
  with no new helpers).

The above five `reason` values are the complete decoder rejection
surface (post-H29 — structural only); any new rejection mode must
add a `REASON_*` constant to `generator/decoder.py`, an entry to
this list, and a dedicated test in `tests/unit/test_m6_decoder.py`
(PHASE1 M6 acceptance criteria). Content-shaped failures (forbidden
kind, prefix-kind mismatch, placeholder labels, existing non-target
labels, self-reference, unresolved `\ref{}`, batch-internal cycles)
are admitted and surface either as `apply_failed` from the projector
(physical-integrity violations such as cross-batch cycles) or as
verifier verdicts (`gap` / `critical`) carrying `repair_hint` for
the next attempt.

**Intra-batch ordering.** Within one generator batch, the wrapper
topologically orders the included node states by their explicit `\ref{}`
dependency edges before handing the batch to librarian. Librarian applies the
batch in that order inside one batch transaction.

**Cycle detection (librarian, post-H29).** All `\ref{}`-induced
cycles — whether the batch tangles within itself or closes a cycle
through already-applied edges — are caught at **apply time** by
librarian's Kuzu-native cycle check (§3.1 projection layer, §6.5).
The result is `apply_failed(reason=cycle)` per §3.1.6; the
generator wastes one batch but no cycle enters truth. The decoder
no longer attempts batch-internal cycle detection: that was a
content judgment (the batch is well-formed bytes either way), and
the H29 boundary moves it to the projector where Kuzu can answer
the cycle question authoritatively. Workers stay Kuzu-free.

**Prompt composition** (assembled by `role.py`):

1. **Generation prompt** — task description for the target label
2. **Memory scope** — the deterministic `problem_id` value the agent
   must pass to every MCP memory call this run. Derived from the
   dispatched target via the same sanitisation rule as
   `agents/generation/mcp/server.py:sanitize_problem_id`
   (e.g. `lem:foo` → `lem_foo`), so two dispatches against the same
   target share scratch memory while different targets stay isolated.
   Sub-agents spawned via `recursive-proving` must receive this
   value verbatim too (§6.2 sub-agent invariant).
3. **Initial guidance** (if mode=fresh **and** `repair_hint` is
   non-empty) — the user-contributed sections of `repair_hint` as
   shipped by coordinator (§10.2.3). Fresh mode never has a
   verifier section (`repair_count = 0` ⇒ no gap/critical verdict
   yet), so this is always user-authored steering for the first
   attempt. Without this step the hint would be discarded unread
   when the first batch bumps `verification_hash` and §5.4 clears
   `repair_hint`.
4. **Repair context** (if mode=repair) — the target's
   `verification_report` and `repair_hint` (which may carry both
   verifier and user sections) from the latest verdict
5. **Latest batch rejection report** (if any) — runtime-only summary
   of the most recent decoder structural rejection (one of the five
   `REASON_*` codes) or projector `apply_failed` reason (cross-batch
   cycle, label collision, kind change, hash chain). Verifier-driven
   content gaps arrive via `verification_report` + `repair_hint`
   instead (item 4 above)
6. **Repair history summary** — the target's current `repair_count`
   (number of gap/critical verdicts accumulated against the current
   `statement_hash`). If `repair_count` is small (0-2), generator is
   expected to try a local proof repair; if it is larger (≥ 3),
   generator should seriously consider that the statement itself is
   wrong and pursue a revised statement or a counter-example.
   Coordinator imposes no hard threshold — `repair_count` is an
   advisory signal for the generator's own decision (§10.4).
7. **Target's current state** — statement (if present) and previous
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
`mcp/`, `AGENTS.md`). Phase I adds a thin `role.py` that is
**Kuzu-free** — it performs only file I/O and subprocess
orchestration, never imports `common/kb`:

1. Reads **only** `runtime/jobs/{job_id}.json` for the full
   pre-validated context (target label, statement, proof,
   `dispatch_hash`, `dep_statement_hashes` map, and for
   `mode=repair` the `verification_report` / `repair_hint` /
   `repair_count` / `H_rejected`) — coordinator populated these
   during pre-dispatch validation (§5.5.2).
2. Reads `knowledge_base/nodes/*.md` as needed for its own decoder
   checks and for Codex's bash-based exploration. Dep
   `statement_hash` lookups during decoder's repair-must-change-hash
   use the `dep_statement_hashes` map from the job file first,
   falling back to parsing `nodes/*.md` YAML frontmatter (§4.2)
   for deps not pre-resolved.
3. Assembles minimal Codex prompt (target label + mode + optional
   hints from the job file).
4. Launches Codex via `codex exec` (see §8 for args).
5. Parses Codex stdout for `<node>` blocks; decoder applies every
   check from this section's failure-modes list.
6. Emits one `generator.batch_committed` by atomic file write into
   `events/{date}/`, writes `status = "publishing"` to the job
   file, and **exits**. Coordinator handles the rest of the
   lifecycle (polling `AppliedEvent`, writing the final status
   back to the job file, deleting it) — see §6.7.1.

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

**What Phase I keeps from original Rethlas generator design.** Original
Rethlas used a productive mathematician-style workflow: immediate
consequences, toy examples, counterexamples, subgoal decomposition,
direct attempts, recursive exploration, external-result search, and
failure synthesis. Phase I keeps those as **generator-internal reasoning
skills**, but rewrites their contract around the event-sourced KB:

- Skills may write only to generator MCP scratch memory, never to
  workspace `events/`, `runtime/`, `knowledge_base/`, or Kuzu.
- Scratch memory channel names such as `failed_paths`,
  `counterexamples`, `subgoals`, and `scratch_events` are not
  knowledge truth and are not replayed by librarian.
- The final product of a generator run is not a blueprint file or a
  verified proof; it is one complete `<node>` batch emitted on stdout
  for the wrapper to decode and publish as one
  `generator.batch_committed` event.
- The old "failure paths are mandatory and queryable" discipline is
  retained inside scratch memory because it materially improves repair
  prompts and prevents repeating bad proof strategies.
- The old external-search discipline is retained only for generator
  reasoning: when a retrieved result influences a proposed proof, the
  generator must preserve the complete statement, source identifiers,
  definition-context notes, and applicability check in `remark`,
  `source_note`, proof text, or scratch memory as appropriate. Generator
  still may not create `external_theorem`; user imports those.
- Broad exploration from original Rethlas is retained, but bounded to
  the current coordinator-dispatched job. Recursive/sub-agent work is
  at most one internal exploration layer per generator run unless
  `rethlas.toml` later adds an explicit budget. Sub-agents, if used,
  may return candidate reasoning only; the parent generator is still
  responsible for emitting the single final batch.
- The shipped generator Codex configuration must enforce that bound in
  practice: `multi_agent = true` is allowed, but the checked-in
  `generator/.codex/config.toml` caps recursion at one child layer
  (root generator + at most one sub-agent depth). Increasing that is
  an architecture change, not a local prompt tweak.
- The old section-verification/blueprint workflow is not retained as an
  execution path. Its useful idea survives as a shaping rule: prefer
  short, named lemmas/propositions with explicit `\ref{}` dependencies
  instead of one monolithic proof.

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
Kind: lemma
Verification hash: <dispatch_hash>
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
- The skill `check-referenced-statements` teaches Codex this convention
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
- On wrong verdict: `count = -1`; **only the user** can fix by
  publishing `user.node_revised(kind=definition)`. Generator never
  touches existing definitions (§6.2 write-scope invariant). If a
  dependent proof-requiring node's repair looks like it needs a
  sharper upstream definition, generator surfaces that in its
  repair report / hint so the user can act.

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

**What Phase I keeps from original Rethlas verifier design.** Original
Rethlas verifier behavior was useful in two ways: strict acceptance and
structured failure reporting. Phase I keeps both while removing the old
HTTP/MCP/result-file service:

- A verifier accepts iff it finds zero gaps and zero critical errors.
  Uncertainty is a `gap`, not an acceptance.
- Dependency-faithfulness is mandatory: a proof may use an explicit
  `\ref{label}` only as strongly as the rendered verified statement in
  `nodes/` permits. Stronger corollaries require their own proof in the
  target proof or a separate verified node.
- Verifier must report every material issue in
  `verification_report.checked_items`, `gaps`, `critical_errors`, and
  `external_reference_checks`; it must not summarize away weak but real
  concerns to make the verdict cleaner.
- Verifier does not search externally. If an external citation is needed
  but is not represented by a verified `external_theorem` node in
  `nodes/`, verifier records `insufficient_information` or
  `missing_from_nodes` and emits `gap`/`critical` depending on
  materiality.
- `external_reference_checks.status` values in Phase I are:
  `verified_in_nodes`, `verified_external_theorem_node`,
  `missing_from_nodes`, `insufficient_information`, and
  `not_applicable`.
- `repair_hint` is not a generated repair proof. It is a concise request
  for more evidence, a missing dependency, or a statement/proof revision
  that the coordinator will pass to a later generator run.
- The shipped verifier skill pack must preserve the original Rethlas
  strictness discipline: sequential proof checking, dependency-faithful
  use of verified nodes only, explicit recording of every material gap /
  critical error / external-reference observation, and no silent
  upgrade of uncertainty into acceptance. These are contract
  requirements, not prompt-style preferences.

Verifier keeps only the original Rethlas prompt/skill shape
(`.agents/skills/`, `.codex/`, `AGENTS.md`). The old verifier `mcp/`
and `api/` service paths are not active Phase I components. Phase I's
`role.py` is
**Kuzu-free**, same discipline as the generator (§6.2): it reads
`runtime/jobs/{job_id}.json` for coordinator-provided context
(target label, statement, proof, and `dispatch_hash`), reads
`nodes/*.md` for Codex's bash-based dep browsing, invokes
`codex exec` once, parses the verdict JSON, and emits a
`verifier.run_completed` truth event whose `verification_hash`
equals the `dispatch_hash` from the job file. After writing
`status = "publishing"` to the job file, the wrapper **exits**;
coordinator polls `AppliedEvent` and writes the final status
(§6.7.1).

**Truth events emitted:**
- `verifier.run_completed` (final verdict: accepted / gap / critical; includes `verification_hash`, `verification_report`, `repair_hint`)

A single verifier run always targets exactly one node and emits exactly one
`verifier.run_completed` truth event. Coordinator may still run many verifier
subprocesses in parallel on different nodes.

Verifier start/fail/interrupt lifecycle belongs to `runtime/`, not `events/`.

### 6.4 Coordinator

Coordinator is the long-running parent process that `rethlas supervise`
launches. It has two jobs:

1. **Scheduling** — read KB + runtime job state, draw from the
   generator and verifier worker pools (§10.3), dispatch short-lived
   worker subprocesses (`generator/role.py`, `verifier/role.py`).
2. **Workspace process supervision** — launch and monitor the other
   long-running daemons (librarian, dashboard) as its own child
   subprocesses; restart on crash; graceful shutdown cascade on
   SIGTERM.

It never parses events, renders `.md` files, or writes Kuzu directly —
those belong to librarian. It also holds the workspace singleton lock;
running a second `rethlas supervise` in the same workspace is not
allowed and fails fast at lock acquisition.

**Process tree under `rethlas supervise`:**

```
rethlas supervise (CLI entry point, delegates to coordinator main)
 └── coordinator process  ← holds runtime/locks/supervise.lock
     ├── librarian subprocess        (long-lived daemon)
     ├── dashboard subprocess        (long-lived daemon)
     ├── generator worker subprocess (short-lived, one per dispatch, up to generator_workers)
     ├── generator worker subprocess ...
     ├── verifier worker subprocess  (short-lived, one per dispatch, up to verifier_workers)
     └── verifier worker subprocess  ...
```

**Singleton enforcement.** Coordinator acquires an advisory
`runtime/locks/supervise.lock` via `flock` at startup. If the lock is
already held, coordinator prints the holder's pid and exits non-zero.
Clean shutdown releases the lock; crash / kill-9 also releases
automatically (OS behavior).

**Startup runtime cleanup.** After acquiring the lock and before
launching children, coordinator sweeps stale runtime state from the
previous run (which may have been killed hard, leaving zombie
heartbeat and job files):
- Delete every file in `runtime/jobs/*.json` — any such file is
  necessarily stale (workers from the previous supervise are dead;
  the flock proves no live worker owns them).
- Delete `runtime/state/coordinator.json` and
  `runtime/state/librarian.json` (next heartbeat ticks will write
  fresh "starting" snapshots).
- **Preserve**: `runtime/state/rebuild_in_progress.flag` (if present,
  librarian handles it per §6.5),
  `runtime/state/rejected_writes.jsonl` and
  `runtime/state/drift_alerts.jsonl` (append-only history, user may
  want to see it), `runtime/logs/*.codex.log` (operator forensics).

This sweep makes dashboard's very first read after supervise restart
show a clean "starting" state, not zombie in-flight jobs from the
previous crash that would confuse the operator until the orphan
reaper cleared them 5 minutes later.

**Child daemon management** (librarian, dashboard):
- Each daemon is spawned with its own pid/pgid. IPC with coordinator
  is **via `runtime/` files only** — the same observer pattern
  dashboard uses (§6.7). No direct sockets / pipes.
- Coordinator polls each child's heartbeat file (`librarian.json`
  etc.) against staleness thresholds (§6.7.1) and inspects process
  liveness (`os.kill(pid, 0)`).

**Startup grace period.** After spawning a child subprocess, there
is a window (1–5 s typical, up to ~30 s in cold cases) before the
child finishes its own initialization (Python import, Kuzu connect,
watchdog setup, startup reconciliation) and writes its first
`runtime/state/{librarian,dashboard}.json` heartbeat. During this
window the heartbeat file does not yet exist. Coordinator **must
not** treat this as "down":

- Coordinator records each child's `spawned_at` timestamp when it
  starts the subprocess.
- While `now - spawned_at < 30 s`, coordinator only checks process
  liveness via `os.kill(pid, 0)`; heartbeat staleness is ignored.
- After the 30 s grace, staleness checks apply normally
  (§6.7.1 table).
- If a child is still alive but has not written a first heartbeat
  by 30 s, coordinator records `startup_timeout` and triggers the
  restart policy below (the child is considered to have failed to
  start).

**Restart policy** (Phase I, different per child because their
failure modes have different blast radius):

| Child | Policy on crash | On re-crash |
| --- | --- | --- |
| **librarian** | Restart **once** immediately; record restart in log | If it crashes again within 3 min → coordinator itself exits with code 3 (workspace unusable without librarian; `rethlas supervise` reports the loop to the operator, who should intervene — usually a config or corruption issue) |
| **dashboard** | Restart up to **3 times** with 30 s backoff between attempts | After 3 failures → mark `children.dashboard.status = "degraded"` in `coordinator.json`; coordinator and librarian keep running; operator can manually `rethlas dashboard` when they want UI back |

Rationale: librarian is on the critical path (no projection means no
verification progress). Dashboard is UI only; losing it is annoying
but does not affect correctness.

- On SIGTERM / SIGINT, coordinator signals children in reverse
  dependency order (dashboard → coordinator workers → librarian),
  waits for graceful exit (up to 10 s each), then SIGKILL remnants,
  then releases the lock and exits. Pressing Ctrl+C in the
  terminal that launched `rethlas supervise` sends SIGINT to the
  foreground process group and triggers exactly this cascade; a
  second Ctrl+C during the 10 s wait escalates directly to SIGKILL
  of remaining children (operator escape hatch). SIGKILL on
  coordinator itself (from another shell) skips graceful shutdown
  entirely — children die with their parent via process-group
  kill; the lock is released by the OS; `runtime/jobs/*.json` and
  `coordinator.json` / `librarian.json` are left as they were and
  get swept by D4 cleanup on the next `rethlas supervise` startup.

Coordinator's own heartbeat file is `runtime/state/coordinator.json`
(§6.4.2), which also carries the children's status so dashboard shows
the whole tree in one place.

**Interaction with one-shot CLIs:**
- `rethlas rebuild` requires the `supervise.lock` to **not** be held.
  If held, rebuild exits non-zero with a "stop supervise first"
  message (no auto-kill — too destructive). rebuild itself takes the
  lock while running.
- `rethlas linter` defaults to refusing if the lock is held
  (concurrent projection makes drift reports noisy, §6.6); pass
  `--allow-concurrent` to override, with a transient-drift warning
  in the report header.
- `rethlas init` refuses if `events/` or `rethlas.toml` already exist;
  `--force` allows overwriting `rethlas.toml` only (never `events/`).

**Startup dispatch gate.** Coordinator's scheduler loop ticks as
soon as coordinator is up, but it **suppresses all worker dispatch**
while either of the following is true (read from
`runtime/state/librarian.json`):

- `startup_phase != "ready"` — librarian is still replaying events
  and/or running the `nodes/` reconciliation pass. During this
  window Kuzu's state is in flux and a worker's pre-dispatch
  validation would either see phantom missing nodes or compute the
  wrong hash.
- `rebuild_in_progress == true` — a rebuild is actively wiping and
  re-populating Kuzu.

In both states coordinator sets `idle_reason_code =
"librarian_starting"` and keeps writing heartbeats, but no new
`runtime/jobs/*.json` are created. Existing in-flight jobs (if any
survived from before the gate engaged) are allowed to complete
normally. Dashboard surfaces this to the operator so they see
"scheduler is waiting for librarian" instead of "scheduler is idle
for no reason".

**Coordinator's state model:** based on current KB plus runtime job state,
coordinator maintains three things in memory:

1. **Generator queue** — `kind ∈ {lemma, theorem, proposition}` nodes
   with `pass_count = -1` (either no proof yet, or latest verdict was
   gap/critical), and with every explicit dependency already at
   `pass_count >= 1` so the dependency is present in `nodes/`. Generator
   reads history to decide fresh vs repair. Priority: by `label` (stable).
2. **Verifier queue** — all nodes where:
   - `0 ≤ pass_count < DESIRED_COUNT`
   - For every dep: `dep.pass_count > node.pass_count` (strict monotone)
   Priority: `pass_count` ascending.
3. **KB query client** — sends read-only `QUERY(...)` requests to
   librarian's IPC channel to compute candidates for the two queues on
   each loop iteration

Queues are **ephemeral** (in-memory) and re-derived from current KB on each
loop start. Coordinator runtime job bookkeeping lives under `runtime/`; there
is no persistent scheduling truth separate from KB state.

**Process-lifetime invariant:** coordinator is the parent of all
in-flight generator and verifier subprocesses and of the librarian /
dashboard daemons. Worker and daemon lifetimes are subordinate to
coordinator's own lifetime. If coordinator exits or crashes, all
children are terminated
as part of the same runtime teardown. Restart never assumes an old
worker is still alive.

Decisions (per node, per loop iteration):

```
if any dep is missing: skip (blocked)

if node.pass_count == -1:
    if node.kind in [definition, external_theorem]:
        skip (waiting for user revision; no generator auto-fix)
    for dep in node.depends_on:
        if dep.pass_count < 1:
            skip (generator can only read verified deps from nodes/)
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
1. `0 <= pass_count < DESIRED_COUNT`
2. For every dep: `dep.pass_count > node.pass_count` (strict greater-than)
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

**Generator and verifier run as two independent worker pools** (§10.3).
The generator pool's capacity is `rethlas.toml [scheduling]
generator_workers` (default 2); the verifier pool's capacity is
`verifier_workers` (default 4). Each pool dispatches independently
from its own queue; there is no shared slot contention between them.

Generator write scope is narrow (§6.2): a batch may touch only its own
target label plus brand-new labels. With the "no concurrent same-target
dispatch" rule, any `generator_workers >= 1` is safe — the only
cross-batch race is two or more concurrent batches independently
inventing the same brand-new label, resolved deterministically by
`(iso_ms, seq, uid)` ordering: the first to apply wins, the loser
gets `apply_failed(label_conflict)`. Higher `generator_workers` trades
slightly more wasted work (on rare label collisions) for more
parallelism; the default 2 is a conservative starting point.

**Cross-component write-set overlap is not prevented.** The "no
concurrent same-target" and the per-pool capacity rules are not
enough to guarantee that a verifier's snapshot of a target's
dependency chain remains valid for the entire verifier run. Concrete
scenario (narrower after the §6.2 write-scope invariant, but still
reachable):

- Verifier V is running on `thm:A`, which transitively depends on
  `thm:B` (via its proof `\ref{thm:B}`).
- Concurrently, generator G is working on its own target `thm:B` in
  repair mode. G's batch revises `thm:B`'s statement (counter-example
  case — G flips `thm:B` to `¬thm:B`) within its permitted write
  scope.
- G's batch is published; librarian applies it; `thm:B.statement_hash`
  changes → Merkle cascade → `thm:A.statement_hash` and
  `thm:A.verification_hash` change.
- V finishes and publishes `verifier.run_completed` carrying the
  **old** `verification_hash` of `thm:A`.
- Librarian applies V's event: hash does not match current
  `Node.verification_hash` → `AppliedEvent(status=apply_failed,
  reason=hash_mismatch)`, no change to `pass_count`.

**Correctness is preserved by the hash-match gate** (§5.5.0 #5): a
stale verdict cannot advance `pass_count`. The verifier's work for
that run is wasted, but no false positive enters KB.

**Phase I accepts the waste.** Coordinator does *not* attempt to pause
verifier dispatch while a generator is in-flight, for two reasons:

1. Generator's effective write set is unknown until decode completes,
   so a sound mutex would have to pessimistically block all verifier
   dispatch during any generator run — costing concurrency even when
   the batch touches unrelated nodes (the common case).
2. Correctness is already guaranteed by the hash-match gate +
   `apply_failed(hash_mismatch)` record. Adding an optimization-level
   mutex would trade concurrency for LLM savings; we defer that
   trade-off until real usage data justifies it.

**Expected dashboard behavior:** a small steady-state rate of
`AppliedEvent(apply_failed, reason=hash_mismatch)` during active
workspaces is **normal, not a fault**. With two concurrent generators,
occasional `label_conflict` rejections on brand-new auxiliary labels are also
possible and are treated as wasted work rather than corruption. The linter does
not treat either pattern as an anomaly by itself. If either rate becomes large
enough to hurt throughput, Phase II can revisit with an optimization pass.

**Six safeguards against infinite loops / over-verification:**

1. **`pass_count` monotonic per hash**: only `+1` from accepted verdicts;
   only resets via hash change (→ 0) or wrong verdict (→ -1). No
   cycling 0↔1 possible on a stable hash.
2. **Upper bound**: coordinator skips nodes with
   `pass_count ≥ DESIRED_COUNT`. Over-verification impossible.
3. **Hash-match check (librarian)**: verdicts with mismatched hash
   are recorded as `apply_failed(hash_mismatch)` and do not change
   `pass_count`. No stale verdict pollution.
4. **Pre-dispatch validation** (§5.5.2): coordinator re-checks every
   dispatch condition against current Kuzu immediately before
   writing the job file — catches drift between candidate selection
   and dispatch.
5. **Codex log mtime timeout (30 min)**: kills stuck Codex processes
   via `killpg`.
6. **Cycle detection (admission + replay validation)**: any candidate
   event that would introduce a dependency cycle is rejected before truth
   publication; replay validation catches corrupted history.

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

#### 6.4.2 Runtime status publication

Coordinator does **not** expose an HTTP server in Phase I. Instead it publishes
its runtime status to local files that dashboard can read. This keeps
coordination logic decoupled from UI serving while still making liveness and
queue state observable.

`runtime/state/coordinator.json` is rewritten atomically (`.tmp` + rename) on
every loop iteration and whenever in-flight job state changes. Minimum fields:

```json
{
  "schema": "rethlas-coordinator-v1",
  "pid": 12300,
  "started_at": "2026-04-24T02:03:10.000Z",
  "updated_at": "2026-04-24T06:05:12.000Z",
  "status": "running | idle | degraded | stopping",
  "loop_seq": 1829,
  "desired_pass_count": 3,
  "active_generator_jobs": 1,
  "active_verifier_jobs": 3,
  "dispatchable_generator_count": 2,
  "dispatchable_verifier_count": 5,
  "unfinished_node_count": 19,
  "idle_reason_code": "",
  "idle_reason_detail": "",
  "user_blocked_count": 1,
  "generation_blocked_on_dependency_count": 4,
  "verification_dep_blocked_count": 7,
  "repair_spinning_count": 2,
  "recent_hash_mismatch_count": 3,
  "children": {
    "librarian": {"pid": 12346, "status": "running", "updated_at": "2026-04-24T06:05:11.000Z"},
    "dashboard": {"pid": 12348, "status": "running", "updated_at": "2026-04-24T06:05:12.000Z"}
  }
}
```

Interpretation:
- `running`: loop healthy and making scheduling decisions
- `idle`: healthy, but currently no dispatchable work
- `degraded`: loop alive but runtime anomaly detected (for example stale job or
  drift alert recorded)
- `stopping`: graceful shutdown in progress

`idle_reason_code` is machine-readable and is the **source of truth** for
dashboard's loop-level explanation. Phase I codes:
- `""` — not idle
- `all_done` — no unfinished nodes remain
- `user_blocked` — unfinished work exists, but only definition /
  external_theorem revisions by the user can unblock it
- `generation_blocked_on_dependency` — proof-requiring nodes need generator, but some of
  their explicit deps are still absent from `nodes/`
- `verification_dep_blocked` — verifier candidates exist, but strict-monotone
  dependency conditions are not yet satisfied
- `in_flight_only` — no new dispatch is possible because current in-flight jobs
  are the only remaining source of progress
- `corruption_or_drift` — loop is intentionally not dispatching because runtime
  drift / projection inconsistency needs operator attention
- `librarian_starting` — librarian is still in its startup phase
  (event replay or `nodes/` reconciliation, or a rebuild is
  in-progress); coordinator holds off all dispatch until librarian
  signals `startup_phase = "ready"` and `rebuild_in_progress = false`
  (§6.4 / §6.5)

Dashboard may use `idle_reason_detail` as human-facing prose, but it
should not infer scheduler meaning from free text. Coordinator caps
`idle_reason_detail` at **512 bytes**; longer strings are truncated
with a trailing `...`.

Dashboard uses `updated_at` age to classify coordinator health. If the file is
missing or stale, dashboard marks coordinator health degraded/down but still
renders the last truth-derived KB state.

If a generator batch fails pre-publication admission (for example due to a
dependency cycle, unresolved reference, kind immutability violation, or
malformed batch), the entire batch is discarded and **no truth event is
written**. The rejection is recorded in runtime state as a **batch rejection
report**. This is distinct from a verifier `verification_report`: verifier
reports are mathematical judgments on a single node/proof, while batch
rejection reports are structural/runtime diagnostics about why a proposed
generator batch could not enter truth.

### 6.5 Librarian

Maintains `dag.kz` and `nodes/`. Single Kuzu writer (§4.1). Librarian
is **passive**: it does not watch `events/` on its own. Coordinator
owns the `events/` watchdog (§6.4 / §6.7.1) and sends librarian an
"apply this event file" command for each new file in
`(iso_ms, seq, uid)` order. Librarian replies with the apply
outcome (applied / apply_failed + reason + detail).

The command channel is a simple in-process queue (librarian is a
child subprocess of coordinator, connected via a Unix-domain
socket or stdio-pipe JSON-RPC; Phase I picks one in M4). Messages:

```
coordinator → librarian:   APPLY { event_id, path_to_event_file }
librarian   → coordinator: APPLIED { event_id } | APPLY_FAILED { event_id, reason, detail }
```

Coordinator may also command a full rebuild:

```
coordinator → librarian:   REBUILD { }       # triggers §11.2 rebuild path
librarian   → coordinator: REBUILD_DONE { } | REBUILD_FAILED { error }
```

Apply loop (inside librarian, per received APPLY command):
```
receive APPLY(event_id, path)
e = load_event(path)

if e.event_id in AppliedEvent:
    # idempotent replay — verify content hash matches; same hash
    # means same event, skip. Different hash means rare uid
    # collision or tampering — halt projection as workspace
    # corruption (§3.1.6).
    if AppliedEvent[e.event_id].event_sha256 != sha256(bytes(file)):
        reply CORRUPTION
        halt
    reply APPLIED(event_id)   # treat as idempotent no-op
    continue

begin Kuzu transaction:
    structural_ok, err = structural_check(e)
    if not structural_ok:
        abort transaction
        reply CORRUPTION
        halt

    semantic_ok, reason, detail = semantic_check(e, current_projection)
    event_hash = sha256(read_bytes(file))
    if not semantic_ok:
        insert AppliedEvent(e.event_id, status="apply_failed",
                            reason=reason, detail=detail,
                            event_sha256=event_hash,
                            applied_at=now())
        commit transaction
        reply APPLY_FAILED(event_id, reason, detail)
        continue

    apply e to Kuzu:
        update atomic fields
        recompute affected hashes (BFS through dependents)
        update pass_count per §5.4
    insert AppliedEvent(e.event_id, status="applied",
                        reason="", detail="",
                        event_sha256=event_hash, applied_at=now())
    commit transaction

    re-render nodes/*.md for each affected node (atomic writes)
    reply APPLIED(event_id)
```

**Coordinator's side of the contract** (§6.4 / §6.7.1):
- Coordinator watches `events/` via `watchdog` and, at startup,
  walks the directory tree to find any not-yet-applied events.
- For each new event file, in `(iso_ms, seq, uid)` order,
  coordinator sends `APPLY(event_id, path)` to librarian and
  awaits the reply. Serialisation is natural (one librarian
  worker); coordinator need not handle reordering.
- On `APPLIED` / `APPLY_FAILED`, coordinator updates dashboard-
  facing state (fires an SSE `applied_event` envelope through the
  dashboard's watcher — §6.7.1).
- On `CORRUPTION`, coordinator stops sending further APPLY
  commands, writes the event path into a corruption marker, and
  surfaces the condition via `coordinator.json`
  (`idle_reason_code = "corruption_or_drift"`).

Key points:

1. **Structural check**: schema completeness, actor/type registered,
   label format. If this fails on a canonical event, admission was
   bypassed → **workspace corruption**, projection halts.
2. **Semantic check** (post-H29): label uniqueness against KB, kind
   immutability, hash chain, full-graph cycle introduction (decoder
   no longer screens batch-internal cycles per §3.1.6 — projector
   sees them along with cross-batch cycles), hint target exists,
   verifier `verification_hash` matches current node hash. `\ref{}`
   targets that don't exist are **admitted** (the corresponding
   `MATCH-CREATE` silently skips the dangling edge); the verifier
   later flags them via `external_reference_checks[].status =
   "missing_from_nodes"`. Genuine integrity failures here are
   recorded as `apply_failed` with a `reason` code from §5.2.
3. **Apply and AppliedEvent are one Kuzu transaction.** Crash
   mid-apply → rollback → event not marked decided → retried on next
   startup (same deterministic outcome).
4. **Apply_failed is terminal.** Per §3.1.6, a failed event is never
   re-examined. Producer must publish a fresh event with a new
   `event_id` if it wants to try again.

Startup sequence:

1. **Event replay**: walk `events/*` in (iso_ms, seq, uid) order; for
   each file, run the main-loop body. `AppliedEvent` makes
   already-decided events no-ops, so replay is incremental.
2. **`nodes/` reconciliation**: after replay completes, for every
   `Node` in Kuzu with `pass_count >= 1`, compute the expected
   `nodes/{prefix}_{slug}.md` content from current Kuzu fields; diff
   against the on-disk file; rewrite any that differ and delete any
   `.md` file on disk whose label is not at `pass_count >= 1` in
   Kuzu. This is **idempotent** and closes the crash window between
   Kuzu commit and per-event re-render — without it, a librarian
   crash after commit but before re-render would leave stale
   `nodes/*.md`, which a later verifier could then read via Codex
   and use to form an incorrect verdict (contaminating truth via
   the hash-match-passing accept path).

Linter category E (§6.6) also audits `nodes/` ↔ Kuzu consistency on
demand; startup reconciliation is the automatic safety net, linter
is the periodic / post-incident verification.

Full rebuild is triggered by `rethlas rebuild`. Sequence:

1. Acquire `runtime/locks/supervise.lock` (fail if held by a live
   supervise).
2. Write `runtime/state/rebuild_in_progress.flag` atomically. Content:
   `{"schema": "rethlas-rebuild-flag-v1", "started_at": "..."}`.
3. Delete `dag.kz/`, `nodes/`, and the `AppliedEvent` table content.
4. Replay every event from `events/` in `(iso_ms, seq, uid)` order
   through librarian's normal apply path (each event gets an
   `AppliedEvent` row as usual).
5. Run the same `nodes/` reconciliation pass librarian does on
   startup (§6.5).
6. Delete `runtime/state/rebuild_in_progress.flag`.
7. Release lock and exit 0.

**Crash recovery**: if rebuild crashes between step 2 and step 6, the
flag stays. On the next `rethlas supervise` startup, librarian
**detects the flag** during its own startup and treats the workspace
as "mid-rebuild" — it force-re-runs the full rebuild (steps 3–6)
before accepting any normal event operations. This makes rebuild
idempotent under partial-crash scenarios.

Librarian writes no truth events in Phase I. `AppliedEvent` is a Kuzu
projection table, not a truth log.

`runtime/state/librarian.json` is rewritten atomically on startup,
after each event projection attempt, periodically while idle (every
30 s), and on entry / exit of a full rebuild. Minimum fields:

```json
{
  "schema": "rethlas-librarian-v1",
  "pid": 12346,
  "started_at": "2026-04-24T02:03:11.003Z",
  "updated_at": "2026-04-24T06:05:11.000Z",
  "status": "running | idle | degraded | rebuilding",
  "startup_phase": "replaying | reconciling | ready",
  "last_seen_event_id": "20260424T060500.000-0001-a1b2c3d4e5f6a7b8",
  "last_applied_event_id": "20260424T060500.000-0001-a1b2c3d4e5f6a7b8",
  "events_applied_total": 1284,
  "events_apply_failed_total": 7,
  "projection_backlog": 0,
  "rebuild_in_progress": false,
  "last_rebuild_at": null,
  "last_error": ""
}
```

- `startup_phase` reflects librarian's progress through its startup
  sequence (§6.5):
  - `"replaying"` — walking `events/` applying un-processed events
  - `"reconciling"` — running `nodes/` reconciliation pass
  - `"ready"` — past the startup sequence; projecting new events
    live
  Coordinator reads this field and **does not dispatch any worker
  while `startup_phase != "ready"`** (see §6.4 startup gate).
  Coordinator's own `idle_reason_code = "librarian_starting"`
  during this window.
- `projection_backlog` = number of files in `events/` without a
  matching `AppliedEvent` row. Dashboard displays as "librarian
  catching up" when > 0.
- `rebuild_in_progress` flips to `true` before librarian unlinks
  `dag.kz/` during `rethlas rebuild`, and back to `false` after the
  replay finishes. Dashboard serves 503 for KB-dependent endpoints
  while it is `true` (see §6.7.1). Coordinator also treats this as
  a dispatch gate (same treatment as `startup_phase != "ready"`).

Dashboard uses this file for librarian liveness and projection
progress. The canonical projection watermark remains in Kuzu /
`AppliedEvent`; this JSON is a read-only observability cache.

### 6.6 Linter

Read-only audit. Phase I scope:

- **A. Event stream integrity**: filename ↔ JSON body consistency,
  `event_id` uniqueness, references to prior events exist
- **B. KB structural invariants**: no cycles, label uniqueness,
  kind-appropriate fields, label prefix matches kind (§3.5.2)
- **C. `pass_count` audit** (§5.5.1): for every node, recompute
  `audit_count` from the event stream and assert it matches stored
  `Node.pass_count`. Catches librarian drift on the one field that drives
  all scheduling decisions.
- **D. `repair_count` audit**: for every node, recompute `repair_count`
  from the event stream (count `verifier.run_completed(gap|critical,
  hash matches)` events since the most recent `statement_hash`-changing
  event on that node, including Merkle cascade from upstream statement
  changes) and assert it matches stored `Node.repair_count`.
- **E. `nodes/` ↔ Kuzu consistency audit**: for every `Node` in Kuzu
  with `pass_count >= 1`, assert that `nodes/{prefix}_{slug}.md`
  exists and its content equals what librarian would render from the
  current Kuzu fields; for every file in `knowledge_base/nodes/`,
  assert the corresponding label exists in Kuzu at `pass_count >= 1`
  with matching content. Passing `--repair-nodes` lets linter rewrite
  divergent files and delete orphaned ones (idempotent re-render).
  Without the flag, linter only reports.
- **F. `events/` ↔ `AppliedEvent` inventory audit**: for every row
  in `AppliedEvent`, assert the corresponding event file exists in
  `events/` and its SHA-256 matches the hash Librarian computed when
  it applied the event (see below). For every file in `events/`,
  assert either an `AppliedEvent` row exists with matching content
  hash, or the file is newer than the most recent librarian startup
  (legitimately un-applied). Any mismatch is a sign the user
  manually edited or removed event files (git revert, manual
  deletion, etc.) and the workspace is no longer consistent. Linter
  reports; there is no `--repair` — fixing it requires `rethlas
  rebuild` after the user decides which side is correct. To make
  this audit possible, librarian records `event_sha256` on each
  `AppliedEvent` row (schema addition below).

Phase I does NOT implement:
- Full projection drift detection (replay all events into a fresh KB and
  diff against live Kuzu). Only `pass_count`, `repair_count`, and
  `nodes/` rendering are audited in Phase I.
- Clock skew detection

**Concurrency rule.** Linter by default refuses to run while a
coordinator is active in the same workspace (i.e. while
`runtime/locks/supervise.lock` is held). Categories C, D, and E
compare event stream / Kuzu / `nodes/` against each other, and a live
librarian can make these comparisons show transient drift that is not
a real bug. Operator can pass `--allow-concurrent` to run anyway; the
linter report's header then notes that drift entries may be transient.

Linter writes no truth events in Phase I.

### 6.7 Dashboard

HTTP server, read-only. Phase I is a **linear HTML view**, no interactive
graph.

The old `integration/rethlas/status_dashboard.py` got one high-level thing
right: the dashboard is an **observer**, not part of the proving loop. Phase I
keeps that separation:

- Dashboard is the **only** Phase I component that exposes HTTP to the browser
- Browser talks only to dashboard
- Coordinator and librarian expose **no** HTTP API
- Dashboard restart must not restart coordinator, librarian, generator, or verifier
- Coordinator health logic must not depend on dashboard availability
- Dashboard reads KB + runtime state only
- No control actions from the dashboard in Phase I

More precisely, dashboard is the **read-only observability layer for
coordinator**. It exists to show the scheduler's published internal state, not
to recompute or second-guess the scheduler from scratch.

**Integration contract:** dashboard does **not** sync with coordinator through
RPC. It builds its view by reading local state that other components publish
atomically:

- `knowledge_base/dag.kz` for current node / dependency / `AppliedEvent` state
- `runtime/state/coordinator.json` for coordinator liveness and queue summary
- `runtime/state/librarian.json` for librarian liveness and projection summary
- `runtime/jobs/*.json` for active generator / verifier jobs
- `runtime/logs/*.codex.log` for real-time log age / stale-job heuristics
- `runtime/state/rejected_writes.jsonl` and `runtime/state/drift_alerts.jsonl`
  for operator-visible alerts
- `events/` only for recent truth browsing / drilldown, not for primary live
  status

Dashboard then exposes its **own** read-only API (`/api/*`) and SSE stream to
the browser. SSE events are emitted by dashboard after local polling / file
watching; coordinator never pushes directly to browsers.

**Source-of-truth split:**
- **Coordinator runtime truth**: `runtime/state/coordinator.json` and
  `runtime/jobs/*.json`
- **Projection / mathematical truth**: `knowledge_base/dag.kz`
- **Dashboard's job**: present coordinator's published runtime state, then use
  Kuzu to explain which nodes / theorems that runtime state refers to

Dashboard should therefore avoid re-implementing scheduler decisions whenever a
machine-readable coordinator field already exists. For example, loop-level
"why are we idle?" comes from `idle_reason_code`, not from dashboard rerunning
the coordinator policy over Kuzu.

The old dashboard also surfaced rich proof-session statuses such as
`provisional`, `invalidated`, and per-pass section summaries. Those were useful
for the old session-local scheduler, but they should **not** be copied into the
new event-sourced model as stored state. In Phase I, dashboard status labels are
derived only from current node fields, dependency relations, and runtime jobs.

**Primary page layout (`GET /`):**
- **Coordinator Health**: coordinator status, age of `coordinator.json`,
  librarian status, active job counts, recent event timestamp
- **Current Scheduling State**: dispatchable counts, unfinished count,
  `idle_reason_code`, blocked-count summary, recent hash-mismatch rate
- **Active Jobs**: all in-flight runtime jobs with target, kind,
  mode, dispatch hash, started time, elapsed time, wrapper heartbeat
  age (`updated_at` freshness, §7.4 F4), and **color-graded Codex
  log age** relative to the configured silent timeout T (default
  1800 s):
  - **green** (fresh): log age ≤ 5 min
  - **yellow** (thinking): 5 min < log age ≤ min(T/2, 15 min)
  - **orange** (silent long): min(T/2, 15 min) < log age < T
  - **red** (will be killed shortly): log age ≥ T — coordinator
    will SIGINT on next tick
  This lets the operator distinguish "xhigh is reasoning" from
  "something is stuck" without needing to memorize the timeout.
- **Human Attention**: user-blocked nodes, repeated-no-progress repair episodes,
  drift alerts, admission failures
- **Affected Theorems**: all `kind=theorem` nodes with current derived status,
  `pass_count`, dependency counts, and latest verdict summary

**Per-node detail (`GET /api/node/{label}` and linked drilldown view):**
- current authored state: label, kind, statement, proof presence, remark,
  source note
- current derived state: `pass_count`, `statement_hash`, `verification_hash`,
  dependencies, dependents
- current runtime state: active job if any, latest log age, latest drift alert
- current repair context: latest verifier report summary, current `repair_hint`,
  recent relevant events

**Dashboard-derived status vocabulary** (display only, not stored in Kuzu):
- `done`: `pass_count >= DESIRED_COUNT`
- `verified`: `1 <= pass_count < DESIRED_COUNT`
- `needs_verification`: `pass_count = 0` and verifier-dispatchable now
- `blocked_on_dependency`: `pass_count = 0` but some dependency is not strictly ahead
- `needs_generation`: proof-requiring node with `pass_count = -1` and all
  explicit deps already visible in `nodes/`
- `generation_blocked_on_dependency`: proof-requiring node with
  `pass_count = -1`, but generator cannot yet run because some explicit dep is
  still below `pass_count = 1`
- `user_blocked`: `definition` / `external_theorem` with `pass_count = -1`
- `in_flight`: runtime job currently active on the node (rendered as an overlay,
  not a replacement for the underlying mathematical state)

This keeps the UI expressive without re-introducing a stored status enum.

**Runtime job contract for dashboard visibility:** each in-flight
`runtime/jobs/{job_id}.json` record must include at least
`job_id`, `kind`, `target`, `mode`, `dispatch_hash`, `pid`, `started_at`,
`updated_at`, `status`, and `log_path`. Coordinator creates the file at
dispatch time; the wrapper updates `updated_at` / `status` during execution; the
file is removed when the job is no longer in-flight.

Pages:
- `GET /` — workspace overview (health + progress + human attention + theorem table + active work)
- `GET /api/coordinator` — raw coordinator snapshot from
  `runtime/state/coordinator.json`
- `GET /api/overview` — JSON payload backing the main page; combines raw
  coordinator runtime state with KB enrichment
- `GET /api/theorems` — enriched view: all `kind=theorem` nodes with
  dashboard-derived status and links back to the relevant coordinator state
- `GET /api/nodes` — every kind of node (definition / proposition /
  lemma / theorem / external_theorem) with the same status
  classification as `/api/theorems`. Added so operators can see the
  helper nodes the H29 boundary (§3.1.6) routinely admits alongside
  the dispatched theorem. Sort key: `(kind, label)`.
- `GET /api/active` — JSON: currently in-flight runtime jobs
- `GET /api/attention` — JSON: nodes / runtime alerts that need human attention
- `GET /api/rejected` — JSON: recent runtime admission failures
  (`runtime/state/rejected_writes.jsonl`), recent `apply_failed`
  events from `AppliedEvent`, and drift alerts
- `GET /api/events?limit=50` — JSON: recent events, filterable by actor/type
- `GET /api/node/{label}` — JSON: full node info
- `GET /events/stream` — SSE: push new events to connected browsers

`/api/overview` carries a `kb.kind_counts` field — a flat
`{<kind>: <count>}` map — so the dashboard summary row can show "2
lemmas, 1 theorem" instead of just a flat "theorems: N" alongside
"nodes: M". The same counts drive the Phase II proof-tree section's
header chips.

Frontend: vanilla HTML + minimal JS, plus **MathJax v3** for LaTeX
typesetting in the per-node detail panel and (Phase II) the
proof-tree statement previews. MathJax was chosen over KaTeX
because Codex-generated math is unconstrained (custom `\def`,
arbitrary `\begin{...}` environments, `\overset` chains, etc.)
and MathJax has wider package coverage; equally important, MathJax
v3's `typesetPromise([root])` API maps cleanly onto the M12.D
SSE-patch model where each verdict-driven badge update only
re-typesets one node card. Phase II.5 (M13) will add Cytoscape.js
+ cytoscape-dagre for the DAG view; both bundles are vendored
under `dashboard/templates/static/` in M13.C so supervise works
air-gapped. No React / Vue.

**Must prominently surface (so user doesn't miss):**
- Definitions / external_theorems at `pass_count=-1` (user must
  revise)
- Proof-requiring nodes with high `repair_count` (e.g. ≥ 3) —
  generator has already tried multiple proofs of the same statement
  unsuccessfully; user may want to intervene (revise statement, attach
  a hint, or confirm the generator's counter-example direction)
- Recent runtime admission failures / rejected generator batches
- Any runtime drift alert raised by coordinator's pre-dispatch
  validation (§5.5.2)
- Coordinator `idle_reason_code = corruption_or_drift` (red banner —
  projection halted, operator action required)
- Librarian `status = degraded` with non-empty `last_error` (librarian
  saw a canonical event that failed structural validation, i.e.
  workspace corruption per §3.1.6)
- Targets with 3 consecutive `status = "crashed"` job outcomes —
  Codex is repeatedly producing unparseable output on that node
  (§7.5). Labelled `"<kind> unstable on <label>"`.
- Targets with 3 consecutive `status = "timed_out"` job outcomes —
  every dispatch exhausts `codex_silent_timeout_seconds` without
  Codex producing a result (§7.4 F3). Labelled `"<kind> frozen on
  <label>"`. Each timeout costs the configured silent window of
  LLM budget, so surfacing promptly lets the operator intervene
  (raise timeout, revise statement, attach hint, or pause).
- Targets with 3 consecutive `status = "apply_failed"` job outcomes
  with the **same** `AppliedEvent.reason` (e.g. repeated
  `label_conflict` because generator keeps picking the same aux
  label; repeated `cycle` because generator keeps closing the same
  dependency loop). Labelled `"<kind> stuck on <label>: 3× <reason>"`.
  Coordinator maintains this per-(target, reason) counter so L2-class
  generator wheel-spin is visible, not silent.

**Should remain useful even when services are partly down:**
- If coordinator is down but Kuzu is readable, dashboard still renders the last
  truth-derived node state and marks runtime health as degraded
- If verifier is down, dashboard still renders current KB state plus runtime
  jobs / stale-job warnings
- If only runtime state is missing, dashboard still renders theorem/node views
  from Kuzu

This follows the useful part of the old dashboard design: show both proof
progress and operational health in one place, but do it from read-only state
and without coupling scheduler correctness to UI-only concepts.

Phase II — see `docs/PHASE2.md`. M12 ships a dynamic foldable
proof-tree outline (HTML `<details>` driven by `/api/tree` + SSE
patches via MathJax `typesetPromise`); M13 / Phase II.5 then adds
the complementary Cytoscape.js + dagre DAG view sharing the same
status / kind visual tokens. Static blueprint LaTeX export is no
longer planned for Phase II — the dynamic dashboard subsumes its
read-only role; if a static export is later wanted, it becomes a
separate exporter that consumes `/api/graph` rather than a parallel
dashboard surface.

### 6.7.1 Runtime interface contract

This subsection pins down the schemas, writers, and lifecycles for
every `runtime/` artifact dashboard consumes. These are **not** truth
(§3.1) — they are observability state. Nothing here is recoverable
from events; it is recomputed as services run.

#### Coordinator and librarian state files

Schemas, writers, and cadences are defined where the writing component
is specified:

- `runtime/state/coordinator.json` — §6.4.2
- `runtime/state/librarian.json` — §6.5 (end of section)

Both files are rewritten atomically (`.tmp` + rename). Dashboard reads
them for liveness and scheduling-state display; no other component
reads them.

**Staleness thresholds (dashboard-only):**

| `now - updated_at` | Dashboard liveness label |
| --- | --- |
| `<= 60 s` | `healthy` |
| `60 s < age <= 5 min` | `degraded` (yellow) |
| `> 5 min` or file missing | `down` (red) |
| file present but JSON parse fails | `down` (red) — treated as missing; error + file path logged to `runtime/logs/dashboard.log`, dashboard continues serving other components |

These thresholds are dashboard UI classifications only; the
components themselves do not self-reap based on them.

Writers use atomic `.tmp + rename` (§9.1 convention), so under normal
operation a reader sees either the previous complete version or the
new complete version — never a partial write. A JSON parse failure
therefore indicates an abnormal event (manual tampering, filesystem
corruption, writer bug); dashboard's role is to keep itself running
and make the abnormal state visible to the operator, not to repair
it. Fixing requires `rethlas rebuild` or operator inspection of the
log.

The `status` field inside `coordinator.json` / `librarian.json` is the
component's **self-reported** runtime state (e.g. `running`,
`stopping`, `rebuilding`). It is orthogonal to dashboard's
**liveness** classification above: a component can self-report
`running` while dashboard displays `degraded` because its heartbeat
stopped arriving. Dashboard shows both — self-reported status as a
label and liveness as a color — so the operator can tell "component
thinks it's healthy, but its file is stale" (i.e. probably frozen)
from "component reported it is stopping".

#### In-flight job: `runtime/jobs/{job_id}.json`

**Writer:** coordinator creates at dispatch. Wrapper updates
`updated_at`, `status` during execution. Terminal state writer = whoever
detects termination (wrapper on clean exit, coordinator's
stuck-detection on timeout / orphan cleanup).

```json
{
  "schema": "rethlas-job-v1",
  "job_id": "ver-20260424T100420.111-a7b2c912d4f1e380",
  "kind": "verifier",
  "target": "lem:block_form_for_x0_plus_u",
  "mode": "single",
  "dispatch_hash": "sha256:...verification_hash_at_dispatch...",
  "pid": 23491,
  "pgid": 23491,
  "started_at": "2026-04-24T02:04:20.111Z",
  "updated_at": "2026-04-24T02:04:25.300Z",
  "status": "running",
  "log_path": "runtime/logs/ver-20260424T100420.111-a7b2c912d4f1e380.codex.log"
}
```

`kind ∈ {"generator", "verifier"}`. `mode`: for generator it is the
dispatch mode (`fresh` / `repair`); for verifier it is `single` in
Phase I.

`dispatch_hash` is the target node's `verification_hash` as read from
Kuzu by coordinator during pre-dispatch validation (§5.5.2). Defined
for all pools and modes:
- `generator fresh`: target exists with empty proof; `verification_hash`
  is still well-defined (hash of statement_hash + empty proof).
- `generator repair`: target's current `verification_hash` (i.e., the
  hash that the last gap/critical verdict rejected).
- `verifier single`: target's current `verification_hash`.

Verifier wrappers use this value as the `verification_hash` they put
in the emitted `verifier.run_completed` event body — they never
re-read Kuzu to recompute it. If the target's hash has drifted by
the time librarian applies the verdict, the hash-match gate
(§5.5.0 #5) yields `apply_failed(hash_mismatch)`.

`status` enumeration (Phase I):
- `starting` — coordinator wrote the file, wrapper has not yet started
  the Codex subprocess (coordinator writes this)
- `running` — Codex subprocess is live; wrapper is monitoring (wrapper writes)
- `publishing` — Codex finished; wrapper has emitted the truth event
  and is about to exit (wrapper's **final** write; from here on
  only coordinator updates the file)
- `applied` — librarian applied the published event; coordinator
  mirrors the AppliedEvent outcome here for dashboard convenience
  (coordinator writes)
- `apply_failed` — librarian recorded `apply_failed` for the
  published event; reason carried in AppliedEvent row; coordinator
  mirrors `reason` + `detail` here too (coordinator writes)
- `timed_out` — coordinator killed the process group because the
  Codex log file went stale past `codex_silent_timeout_seconds`
  (coordinator writes)
- `crashed` — wrapper subprocess exited non-zero **before** writing
  `publishing`; coordinator detects on next tick (coordinator writes)
- `orphaned` — coordinator's stuck-detection found a job file whose
  `pid` is not alive and which never reached `publishing`
  (coordinator writes)

Note: there is **no `precheck_failed` status**. Pre-dispatch
validation is entirely coordinator-owned (§5.5.2) and happens
**before** a job file is created. A candidate that fails precheck
simply never has a job file written; coordinator logs the reason
to `runtime/logs/supervise.log` instead.

`log_path` is redundant with `job_id` (always
`runtime/logs/{job_id}.codex.log`) but stored explicitly so dashboard
does not need to know the derivation rule.

**Job file lifecycle** (under the "workers are Kuzu-free" model,
§4.1):

1. **Coordinator pre-validates + creates the file** with
   `status = "starting"` (atomic `.tmp` + rename). The file carries
   every context the worker will need, since the worker cannot
   touch Kuzu:
   - `target`, `mode`, `kind`, `dispatch_hash`
   - target's `statement` and `proof` text (read from Kuzu by
     coordinator once, passed through)
   - `dep_statement_hashes`: a `{dep_label: statement_hash}` map
     for every dep the target's content references, so the
     generator decoder can compute post-batch hashes without
     consulting Kuzu (§6.2)
   - for `mode=repair`: `verification_report`, `repair_hint`,
     `repair_count`, `H_rejected`
   Coordinator then spawns the wrapper subprocess with:
   - Full environment inheritance from `rethlas supervise` (so
     `OPENAI_API_KEY` / `PATH` / `HOME` / proxy settings flow
     through to Codex).
   - Additional env `RETHLAS_WORKSPACE=<absolute path>` overlaid.
   - Positional argument `job_id`.
   Operators configure API keys by exporting them in the shell
   before invoking `rethlas supervise`; Phase I does not read API
   keys from `rethlas.toml` or any secret store.
2. **Wrapper** reads the job file, transitions `status` to
   `"running"`, runs Codex, emits the truth event via atomic file
   write into `events/`, transitions `status` to `"publishing"`,
   and **exits**. Wrapper also refreshes `updated_at` every 60 s
   while Codex is running (§7.4 F4). Wrapper never polls Kuzu or
   `AppliedEvent`.
3. **Coordinator** observes the wrapper's exit on its next tick
   (pid no longer alive, `status = "publishing"` on disk). It then
   polls `kb.applied_event_record(event_id)` for the event the
   wrapper published, writes the final `status`
   (`applied` / `apply_failed` with `reason` + `detail`) back to
   the job file, and deletes it. This is where the AppliedEvent →
   job-file mirroring happens.
4. **Timeout path:** coordinator's Codex-log-mtime monitor kills
   the process group, writes `status = "timed_out"` to the job
   file, and deletes it.
5. **Crash path:** if wrapper exits before writing `"publishing"`
   (parse error, subprocess crash, etc.), coordinator writes
   `status = "crashed"` with `detail` containing a short error
   summary and deletes the file.
6. **Orphan reaper:** each loop tick scans `runtime/jobs/*.json`
   for files whose `pid` is not alive AND whose `updated_at` is
   older than 5 minutes AND whose `status` has never progressed
   past `"starting"` or `"running"`. Writes `status = "orphaned"`
   and deletes. This is the backstop for kernel-level wrapper
   failures.

Dashboard reads job files directly; they are transient, so dashboard
never treats a job file as authoritative beyond what
`AppliedEvent` says about the corresponding event once it has been
published.

#### Append-only runtime logs

**`runtime/state/rejected_writes.jsonl`**: one line per admission
rejection (§3.1.6). Written by the rejecting producer's admission
layer. Schema per line:

```json
{
  "schema": "rethlas-rejection-v1",
  "ts": "2026-04-24T02:04:20.111Z",
  "actor": "generator:codex-gpt-5.4-xhigh",
  "event_type_attempted": "generator.batch_committed",
  "target": "thm:foo",
  "reason": "prefix_kind_mismatch",
  "detail": "label thm:bar declared kind=lemma"
}
```

**`runtime/state/drift_alerts.jsonl`**: one line per pre-dispatch
drift detection (§5.5.2) or librarian-internal sanity failure. Same
line format (reuses `schema`, `ts`, `actor`, `target`, `reason`,
`detail`).

**Line length cap.** Both JSONL files are concurrently appended to
by multiple producers (user CLI, generator wrapper, verifier wrapper).
POSIX `O_APPEND` guarantees atomic writes **only for payloads
strictly smaller than `PIPE_BUF`** (4096 bytes on Linux). To stay
safely within that bound:
- `detail` field content is capped at **1024 bytes** (truncate with
  trailing `...(truncated)` if longer).
- Each complete JSONL line is capped at **2048 bytes**; if a line
  would exceed that cap, `detail` is re-truncated until it fits.
- Writers open with `O_APPEND | O_CLOEXEC` and write in a single
  `write(2)` call — no Python buffered I/O. This preserves atomicity
  across concurrent admission writes from different processes.

**Retention (Phase I):** both JSONL files are append-only and
**never rotated automatically**. `rethlas rebuild` truncates them.
Operator may truncate manually when they grow large. Phase II can add
size-based rotation.

#### Python daemon logs (not Codex logs)

Distinct from the per-job Codex subprocess logs
(`runtime/logs/{job_id}.codex.log`), each long-running Python daemon
writes its own log file:
- `runtime/logs/supervise.log` — coordinator (incl. child supervision)
- `runtime/logs/librarian.log` — librarian daemon
- `runtime/logs/dashboard.log` — dashboard daemon

Format: standard Python `logging` output, one record per line,
prefixed with UTC ISO timestamp + log level. Use for operator triage:
when a daemon marks itself degraded or hits an unexpected exception,
the corresponding `.log` file contains the stack trace and the lead-up
context. Retention same as JSONL logs: append-only, truncated by
`rethlas rebuild`, no automatic rotation in Phase I (Phase II Open
Item §14).

#### Linter report: `runtime/state/linter_report.json`

**Writer:** `rethlas linter` on each invocation (overwrites previous).
Schema: `{ schema, ts, a: {violations}, b: {violations}, c: {violations}, d: {violations}, summary }`. Consumed by CI, dashboard (if present).

#### SSE stream `/events/stream`

Dashboard is the **only** SSE emitter. Mechanism:

1. Dashboard uses `watchdog` (or platform equivalent) to watch
   `events/**/*.json`, `runtime/jobs/*.json`,
   `runtime/state/*.json`, and `runtime/state/*.jsonl` for file
   creation / modification events. For the two `.jsonl` logs,
   dashboard also maintains a byte offset per file so it can tail
   just the newly appended lines on each modification notification
   (watchdog signals that the file changed, not which bytes are new).
2. On filesystem notification, dashboard emits an SSE message with a
   typed envelope:

```json
{
  "type": "truth_event | applied_event | job_change | coordinator_tick | librarian_tick | alert",
  "ts": "2026-04-24T02:04:25.300Z",
  "payload": { ... }
}
```

   - `truth_event`: new file appeared under `events/`; payload =
     parsed event body + event_id.
   - `applied_event`: a new row appeared in Kuzu `AppliedEvent`
     (detected via librarian's heartbeat tick and a bounded tail
     query); payload = `{event_id, status, reason}`.
   - `job_change`: a file under `runtime/jobs/` was created, updated,
     or deleted; payload = job snapshot (or `{job_id, status:
     "terminated"}` on delete).
   - `coordinator_tick` / `librarian_tick`: the corresponding state
     file changed; payload = the fresh snapshot.
   - `alert`: a new line in `rejected_writes.jsonl` or
     `drift_alerts.jsonl`; payload = the line.
3. SSE clients reconnect natively on drop (standard SSE); dashboard
   resumes from "now" and does not replay history — browser can call
   REST endpoints for backlog.

Dashboard does **not** push state changes from inside coordinator /
librarian / wrappers. All emission happens via file system → dashboard
→ SSE, so component isolation (§6.7) is preserved.

#### `/api/events` query strategy

Dashboard walks `events/` in reverse chronological order: it reads
directory entries date-shard by date-shard (newest first), sorts each
shard's filenames, and streams filenames until `limit` have been
collected. `AppliedEvent` provides the `status` enrichment via a
single Kuzu `WHERE event_id IN (...)` query after the filenames are
chosen. No separate "recent events" projection table is maintained.

**`limit` is clamped to `[1, 500]`.** Requests exceeding 500 are
silently capped; `limit` below 1 is rejected with HTTP 400. This
prevents a client from forcing dashboard to scan the entire `events/`
directory in one request.

Phase II may add an index if Phase I profiling shows this scan is
slow at realistic workspace size.

#### Dashboard binding

Default bind: `127.0.0.1:8765`. No authentication in Phase I.
Configurable via `rethlas.toml [dashboard] bind = "<host>:<port>"`
and CLI flag `--bind`. Binding to a non-loopback address is opt-in
and logs a startup warning since Phase I has no auth.

**Standalone vs supervise-spawned.** There are two ways dashboard
starts:
- As a **child of `rethlas supervise`**: coordinator spawns the
  dashboard subprocess; bind comes from `rethlas.toml [dashboard]
  bind`.
- As a **standalone `rethlas dashboard [--bind ...]`**: user invokes
  directly, typically against a quiet workspace (no active
  supervise).

Both paths bind the same port by default, so they would conflict
(EADDRINUSE on the second). To make the UX clean, standalone
`rethlas dashboard` first checks whether `runtime/locks/supervise.lock`
is held:
- **Lock held** (supervise is running) → standalone dashboard prints
  `"supervise is running on this workspace; it has already started
  a dashboard at <configured bind>. Open that URL instead."` and
  exits 0 (informational, not an error).
- **Lock free** → standalone dashboard proceeds to bind the port
  and serve normally.

This matches the "observer pattern" design: dashboard never competes
for the observer role, it defers to whoever's already observing.

#### Behavior while `rethlas rebuild` runs

Librarian writes `librarian.json` with `rebuild_in_progress = true`
before unlinking `dag.kz/`. Dashboard observes this and returns HTTP
`503 Service Unavailable` with
`Retry-After: 5` and a JSON body `{"status": "rebuild_in_progress"}`
for any endpoint that depends on Kuzu (`/api/overview`,
`/api/theorems`, `/api/node/...`, `/api/rejected`). Non-Kuzu endpoints
(`/api/coordinator`, `/api/active`, raw `/events/stream`) stay up.

#### Kuzu concurrent-read note

Librarian is the single writer; dashboard, coordinator, and linter
are readers. Kuzu embedded supports concurrent readers with a single
writer, but individual queries may observe brief latency spikes
during librarian's transaction commit. Phase I accepts this; Phase II
may adopt MVCC-style read snapshots if UI responsiveness warrants.

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

**Codex subprocess signal:** mtime of Codex log file at
`runtime/logs/{job_id}.codex.log`. Every token Codex streams updates
mtime; during long silent reasoning (xhigh can sit quiet for 10–20
minutes per §13.9), mtime stays put.

**Log file contents: stdout + stderr merged.** Wrapper launches
`codex exec` with `stderr` redirected into the same log file as
`stdout` (`subprocess.Popen(..., stdout=log_fd, stderr=STDOUT)` in
Python). Rationale: Codex and its MCP subprocesses may write
warnings, rate-limit notices, and genuine errors to stderr; losing
those on crash-diagnosis hurts triage. The wrapper's parser only
reads stdout for `<node>` blocks / verdict JSON — stderr is captured
for humans, not for the parser. A side effect: stderr writes also
update log mtime, so any Codex-side activity (including warnings)
keeps the liveness signal fresh.

**Rule:** If log mtime > `codex_silent_timeout_seconds` (default
1800 = 30 min, configurable via `rethlas.toml [scheduling]`, §2.4),
coordinator sends SIGINT to the process group (`os.killpg`), waits
10 s, then SIGKILL and marks the runtime job `timed_out`. The
default tolerates typical xhigh silent-thinking bursts; operators
running even longer reasoning workloads can raise the timeout.

**Wrapper heartbeat (separate signal).** Independently of log mtime,
the wrapper updates its own `runtime/jobs/{job_id}.json`
`updated_at` field **every 60 seconds** while Codex is running, even
if `status` hasn't changed. This distinguishes "Codex is thinking
silently, wrapper is healthy" from "wrapper itself crashed or
hung". Dashboard reads both signals:

- Fresh `updated_at` + stale log mtime ⇒ Codex is reasoning (normal
  for long reasoning budget).
- Fresh log mtime ⇒ Codex is actively producing output.
- Stale `updated_at` AND stale log mtime ⇒ wrapper itself is
  unhealthy; orphan reaper or coordinator's child-management logic
  handles it.

**Consecutive `timed_out` detection.** Parallel to the consecutive-
`crashed` rule in §7.5, coordinator tracks per-target consecutive
`timed_out` outcomes. After **3 consecutive** timeouts on the same
target (same `kind`), dashboard surfaces
`"<kind> frozen on <label>: 3 consecutive timeouts"` under Human
Attention (§6.7). Operator intervention options: raise
`codex_silent_timeout_seconds`, revise the node's statement, attach
a user hint, or manually pause dispatch on that target (Phase II).

### 7.5 Permissions enforced vs convention

- **Sandbox-enforced:** no writes, no reads outside nodes/
- **Convention (skill discipline):** Codex produces `<node>` blocks in
  final output; wrapper parses and emits events on Codex's behalf

Even if Codex attempts an illegal operation, the sandbox denies it.
Malformed output is caught by the wrapper's parser and rejected per
the following rules.

**Malformed-output handling** (generator decoder, verifier verdict
parser):

1. Wrapper's parser fails to extract the expected structured output
   from Codex's stdout (missing `<node>` blocks for generator;
   missing / malformed final verdict JSON for verifier).
2. Wrapper writes `status = "crashed"` to its
   `runtime/jobs/{job_id}.json` with `detail` containing a short
   error ("verdict parse failed: missing 'verdict' key", etc.).
3. The full stdout stays in `runtime/logs/{job_id}.codex.log` for
   post-hoc triage — Codex's reasoning trace is often essential for
   diagnosing why it returned malformed output.
4. **No automatic retry.** Wrapper exits; the job's target returns to
   whatever queue it was in (generator or verifier) and will be
   picked up again on a subsequent coordinator tick under normal
   scheduling — no special backoff, the target just competes for the
   next pool slot.
5. **Instability surfacing**: coordinator keeps a short sliding
   window (per-target) of recent job outcomes. If a target hits
   `status = "crashed"` **three times in a row** without an
   intervening success / apply_failed, dashboard surfaces that
   target under "Human Attention" as
   "`<kind> unstable on <label>: N consecutive crashes`". Operator
   can inspect `runtime/logs/*` for the stdout and decide whether
   to revise the source, attach a user hint, or adjust the
   generator / verifier skill.

---

## 8. MCP Tools

**Only generator uses MCP in Phase I.** No other component uses MCP for any
truth-bearing purpose. Python components use the librarian IPC API for KB
queries; only librarian opens Kuzu. Rethlas does not expose an MCP server to
external callers.

Generator's MCP server process is launched by Codex per invocation. Code
duplication is acceptable at this scale.

**Transport: MCP stdio** (not TCP). Codex launches the MCP server as
a subprocess and communicates over its stdin/stdout. Rationale: with
`generator_workers > 1` there may be multiple generator workers and
therefore multiple MCP server processes alive concurrently; stdio
transport requires no port allocation and has zero chance of port
clashes. Configure in `generator/.codex/config.toml` as a stdio MCP
server pointing at `./mcp/server.py`.

### 8.1 Generator-only tools

| Tool | Purpose |
| --- | --- |
| `search_arxiv_theorems(query)` | External literature search via leansearch.net (existing) |
| `memory_init` / `memory_append` / `memory_search` | Codex scratchpad (existing) |

Generator scratch memory is explicitly non-truth. The MCP server may expose
channels for original-Rethlas-style reasoning artifacts
(`immediate_conclusions`, `toy_examples`, `counterexamples`, `subgoals`,
`proof_steps`, `failed_paths`, `branch_states`, `big_decisions`,
`scratch_events`), but these files are not workspace `events/`, are not
replayed, and may be deleted without changing KB truth. Skills must call the
channel `scratch_events` rather than plain `events` to avoid confusion with
truth events.

`big_decisions` carries cross-round strategic pivots (technique switches,
target reformulations, abandonments, elevated invariants) — see
`agents/generation/.agents/skills/identify-key-failures/SKILL.md` for the
schema and the `decision_type` semantics. Producers:
`identify-key-failures` and `propose-subgoal-decomposition-plans`. Consumer:
`propose-subgoal-decomposition-plans` reads recent decisions to avoid
re-pursuing an abandoned strategy.

Codex built-in web browsing is **not** part of the Phase I generator contract.
The only external-search tool promised by Rethlas is `search_arxiv_theorems`.
If an implementation later enables web browsing as an operator option, any
results remain generator scratch context and still cannot create
`external_theorem` nodes.

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
truth-event producer (admission layer, pre-publish):
  construct event payload
  allocate event_id:
    iso_ms = utc_wall_clock_now()   # §3.2 — strictly UTC
    seq    = next same-ms seq for this producer (almost always 0001)
    uid    = random 16 hex
  run structural_check (§3.1.6) against current KB snapshot
    # "current KB snapshot" source is producer-specific:
    #   - user CLI: queries librarian IPC when supervise is running,
    #     or uses a short-lived librarian apply/query path in standalone mode
    #   - worker wrapper (gen/ver): reads nodes/*.md + job file
    #     only (Kuzu-free per §4.1); best-effort for checks that
    #     need count<1 KB state — librarian is authoritative at
    #     apply time (§6.5)
    on failure: record to runtime/state/rejected_writes.jsonl; return
                failure to caller. Nothing reaches events/.
  compose filename per §3.2
  write_bytes(tmp_fd, event_json_bytes)
  fsync(tmp_fd)                              # event bytes hit disk
  close(tmp_fd)
  rename(tmp_path, canonical_path)           # atomic rename
  dir_fd = open(events/{date}/, O_RDONLY)
  fsync(dir_fd)                              # directory entry hits disk
  close(dir_fd)


# --- User CLI path ---------------------------------------------
# `rethlas add-node` / `revise-node` / `attach-hint` are publishers
# that poll librarian for their own feedback:
user CLI polls librarian for fate:
  loop until timeout (30 s):
    status = kb.applied_event_status(event_id)
    if status != "not_found": return (status, reason)
  if supervise_lock_held():
    return "queued, librarian slow; will apply when caught up" (exit 0)
  else:
    return "queued, but supervise not running; start `rethlas supervise`
            to apply" (exit 0)

# --- Worker wrapper path ---------------------------------------
# generator / verifier `role.py` are Kuzu-free (§4.1). They never
# poll AppliedEvent. Instead:
worker wrapper after publishing:
  write status="publishing" to runtime/jobs/{job_id}.json
  exit
# Coordinator on its next tick notices the exit, polls
# `kb.applied_event_record(event_id)` itself, and writes the final
# status (applied / apply_failed with reason + detail) back into
# the job file before deleting it (§6.7.1 step 3).
```

User-CLI feedback contract (D2). The event is "truth as soon as it's
in `events/`" — the file's presence on disk is the durable outcome.
Application to KB is a separate, later, librarian-side decision. So
the CLI reports success (exit 0) in three cases:

| Poll outcome | CLI message | Exit |
| --- | --- | --- |
| `status = applied` | "applied" | 0 |
| `status = apply_failed` | "apply_failed: `<reason>` — `<detail>`" | 0 (the event is still in `events/` as truth; the projection was rejected per §3.1.6) |
| timeout, supervise running (lock held) | "queued; librarian is slow / behind — will apply when it catches up" | 0 |
| timeout, supervise not running (lock free) | "queued; run `rethlas supervise` to apply" | 0 |

None of these are errors. The event file is authoritative; the CLI
is just telling the user what the librarian decided (if anything
yet). Non-zero exits are reserved for structural admission failure
(§3.1.6) — event never reached `events/`.

Notes:
- **fsync the file and its parent directory.** Without the parent
  `fsync(dir)` after rename, on a kernel / power crash the event
  file's data is on disk but its directory entry may still be in
  page cache — the file is effectively "written but the name is
  missing". Both fsyncs are required so every event that returned
  success from `rethlas add-node` is durable as truth.
- **No cross-producer lock, no "sort after workspace max" check.**
  Concurrent publishers allocate event_ids from their own wall clock;
  replay order is decided by the `(iso_ms, seq, uid)` sort, and
  cross-publisher conflicts are resolved by librarian at apply time
  (§3.1.6, §6.5).
- **Per-machine wall-clock monotonicity is assumed.** NTP slew is
  fine; step-backwards is not.
- **Per-producer, per-ms seq counter.** In Phase I each truth event
  is published standalone, so `seq` almost always stays at `0001`.

**Who writes truth events:**
- **User**: publishes through helper CLI commands (`rethlas add-node`,
  `rethlas revise-node`, `rethlas attach-hint`, etc.). Manual drafting may
  happen outside `events/`, but direct hand-drops into canonical `events/`
  are unsupported in Phase I. The user CLI reads Kuzu (specifically
  `AppliedEvent`) for its own feedback — user CLI is a publisher, not a
  worker, and §4.1 permits direct Kuzu read access for it.
- **Generator / verifier workers**: their wrapper code (Python, NOT Codex)
  writes truth events. Codex subprocess is read-only sandboxed; it
  outputs to stdout which the wrapper captures and converts to truth
  events. Workers are **Kuzu-free** (§4.1): they do not poll
  `AppliedEvent`. After publishing, wrapper writes
  `status = "publishing"` into its job file and exits; coordinator
  takes over (§6.7.1 step 3) and mirrors the AppliedEvent outcome
  back to the job file.

**Codex never writes files.** Only Python wrappers do.

### 9.2 Projecting an event

```
librarian file-watcher notices new file (or startup scan picks it up)
load event e
if e.event_id already in AppliedEvent: skip (idempotent)

begin Kuzu transaction:
  run structural_check(e):
    on failure: halt projection; workspace corruption (§6.5)

  event_hash = sha256(read_bytes(file))
  run semantic_check(e, current_projection):
    on failure (label_conflict / cycle / ref_missing /
                hint_target_missing / hash_mismatch / ...):
      insert AppliedEvent(event_id, status="apply_failed",
                          reason=<code>, detail=<context>,
                          event_sha256=event_hash,
                          applied_at=now())
      commit; done with e (terminal)

  # semantic check passed — apply to KB
  update atomic fields
  recompute affected nodes' hashes (BFS through dependents)
  update pass_count per §5.4
  insert AppliedEvent(event_id, status="applied",
                      reason="", detail="",
                      event_sha256=event_hash, applied_at=now())
commit

re-render nodes/*.md for each affected node (atomic writes)
```

Apply_failed and applied are both durable Kuzu rows; both are visible
to producers via the KB Protocol, to dashboard via `/api/rejected` and
`/api/events`, and to linter when auditing drift.

### 9.3 A typical verification cycle

```
1. coordinator reads KB: finds proof-requiring node with pass_count=0 and every dep.pass_count > node.pass_count
2. coordinator creates a verifier runtime job for `lem:foo`
3. wrapper launches codex exec with nodes/ cwd, read-only sandbox
4. Codex reads the target statement/proof from the prompt and dep files from `nodes/`; runs 3-stage skills
5. wrapper parses verdict JSON and emits verifier.run_completed
6. librarian applies verifier.run_completed: pass_count increments if accepted+hash matches (AppliedEvent records the outcome)
7. coordinator on next loop sees pass_count=1; if DESIRED_COUNT=3 dispatches audit
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

### 10.2 Dispatch priority

Each pool has its own candidate filter and tie-break rule. Both are
evaluated by coordinator from Kuzu once per tick; selection is
deterministic so two supervisors that read the same Kuzu snapshot
would pick the same targets.

#### 10.2.1 Verifier candidates

Filter:
- `pass_count in [0, DESIRED_COUNT)` — at 0 the node is awaiting its
  first verdict; at `DESIRED_COUNT` it is done.
- `kind ∈ {lem, thm, prop, def, ext_thm}` — verifier handles
  well-formedness for `def` and `ext_thm` too (§6.3), not just
  proof-requiring kinds. For proof-requiring kinds, `proof` is
  guaranteed non-empty at `pass_count = 0` by the `initial_count`
  rule (§5.4) — an empty proof would force `pass_count = -1`.
- For every dep: `dep.pass_count > node.pass_count` (strict-monotone
  scheduling, §6.4.1).

Priority ordering:
1. `pass_count` ascending (nodes with fewer passes first — count=0
   before count=1 before count=2). This ensures every node gets its
   first verdict before any node gets its second, keeping `nodes/`
   visibility broad early.
2. `label` ascending (deterministic tiebreak).

#### 10.2.2 Generator candidates

Filter:
- `pass_count = -1` — "needs generator" is the only generator band
  (§5.4). Nodes at `pass_count ≥ 0` never enter the generator pool.
- `kind ∈ {lem, thm, prop}` — proof-requiring only. `def` and
  `ext_thm` at `pass_count = -1` are user-blocked (§6.4.1 L2246–2248)
  and coordinator **never** auto-dispatches generator on them; they
  only unblock via `user.node_revised`.
- All deps at `pass_count ≥ 1` (so they are rendered in `nodes/` and
  reachable via bash, §6.2). Deps below that threshold are still
  being verified; generator must wait.

Priority ordering:
1. `label` ascending (deterministic, single-key sort).

All eligible candidates share the same band (`pass_count = -1`),
so — unlike verifier (§10.2.1) — there is no pass-count axis to
spread on. `repair_count` is **not** used here: §10.4 pins the
"when to give up" decision on the generator (which reads
`repair_count` in its prompt, §6.2) and the user (dashboard Human
Attention surfaces `repair_count ≥ 3`, §6.7), not on the
coordinator. Using `repair_count` as a priority key would starve
stuck candidates whenever fresh work keeps arriving, contradicting
§10.4's "proof-requiring nodes at `pass_count = -1` continue to
be dispatched."

#### 10.2.3 Mode selection (generator only)

For each generator candidate the coordinator picks `mode` by
`repair_count` only:
- `mode = "fresh"` iff `repair_count = 0` — no prior gap/critical
  verdict exists against the current `statement_hash`, so there is
  no rejected proof to fix. This covers (a) a freshly
  `user.node_added` node with empty proof, and (b) a node whose
  statement just changed (via `user.node_revised` or Merkle cascade
  from a dep) and whose `repair_count` was therefore reset to 0
  per §5.4.
- `mode = "repair"` iff `repair_count ≥ 1` — there is at least one
  stored gap/critical verdict against the current `statement_hash`,
  so a rejected proof plus `verification_report` exist. Coordinator
  sets `H_rejected` = the most-recent rejected `verification_hash`
  (§5.5.2).

**Hint shipping is orthogonal to mode.** `user.hint_attached` may
append a user section to `repair_hint` any time `pass_count ≤ 0`
(§3.1.6 admission rule), including on a fresh never-attempted node.
In that case `repair_count` is still 0 so mode = fresh, but the
hint must reach the generator before its first try — otherwise the
batch it produces will bump `verification_hash` and §5.4 will clear
`repair_hint`, discarding the hint unread.

Therefore: **coordinator ships `repair_hint` into the job file
whenever it is non-empty, in both modes.** `verification_report`
and `H_rejected` are still repair-mode-only (they have no meaning
without a prior verdict). Generator's prompt composition (§6.2)
uses the shipped `repair_hint` as initial guidance in fresh mode
and as repair context in repair mode.

A candidate with `pass_count = -1` and `repair_count ≥ 1` but no
resolvable `H_rejected` is a precheck failure (§5.5.2 last row);
it is skipped and logged, not dispatched.

### 10.3 Concurrency — worker pools

Phase I schedules generator and verifier as **two independent worker
pools**. Each pool has its own queue, its own fixed capacity, and
draws work independently; there is no cross-pool precedence and no
shared slot contention.

**Configuration** (`rethlas.toml [scheduling]`):

| Field | Default | Meaning |
| --- | --- | --- |
| `generator_workers` | `2` | Max concurrent generator jobs per workspace |
| `verifier_workers` | `4` | Max concurrent verifier jobs per workspace |
| `desired_pass_count` | `3` | Per-node pass_count goal (§10.1) |

Coordinator's per-tick dispatch is **two independent loops**:

1. For each empty generator slot (`generator_workers - in_flight_generators`):
   pick the highest-priority generator candidate whose target is not
   already in flight, dispatch.
2. For each empty verifier slot (`verifier_workers - in_flight_verifiers`):
   pick the highest-priority verifier candidate whose target is not
   already in flight, dispatch.

The "no concurrent same-target dispatch" rule (§6.4.1) still applies
across both pools: if a target is in flight in pool A, pool B must skip
it. Otherwise the two pools run independently and their combined
concurrency is simply `generator_workers + verifier_workers`.

Both defaults are conservative starting points for Phase I single-backend
Codex; operators tune via `rethlas.toml` as they gain throughput data.
There is no separate Codex-wide budget mechanism in Phase I — the
`common/codex_budget` shared-slot concept from the original Rethlas is
superseded by the per-pool caps above.

### 10.4 Repair rounds

Phase I does **not** impose a hard repair budget. Proof-requiring
nodes at `pass_count = -1` continue to be dispatched to generator.

`Node.repair_count` tracks gap/critical verdicts against the current
`statement_hash` (§5.2, §5.4) — i.e. how many different proofs of the
same statement have already failed. It is the canonical "how stuck is
this statement?" signal. Generator reads `repair_count` in its repair
prompt (§6.2) and decides between:

- continue local proof repair (typical when `repair_count` is small
  and verdicts have been `gap` rather than `critical`)
- revise the statement (when `repair_count` is growing and the
  verifier keeps flagging semantic/structural issues)
- pursue a counter-example / negation route (when the pattern
  suggests the original statement may be false)

Coordinator imposes no hard threshold on `repair_count` in Phase I.
Dashboard surfaces high-`repair_count` nodes under "Human attention"
(§6.7) so the user can intervene if generator keeps spinning without
progress. The decision of "when to give up on a proof path" belongs
to the generator (and, as a backstop, the user), not to the
coordinator.

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
| Librarian crashes mid-apply | Kuzu transaction rolls back (both KB mutations and the `AppliedEvent` row roll back together); per-event `nodes/*.md` render may have been partial | Next startup re-processes the event deterministically; librarian's startup `nodes/` reconciliation (§6.5) repairs any stale or partial `.md` files |
| `AppliedEvent` out-of-sync with KB | Only possible via manual Kuzu tampering | `rethlas rebuild` restores both from events |
| Kuzu DB corrupt | Queries fail | `rethlas rebuild` |
| Full workspace clone | No `dag.kz` | `rethlas rebuild` on first use |
| Codex subprocess stuck | Log mtime stales | Coordinator timeout kills process group |
| Supervise (coordinator) crash | All children die | Restart `rethlas supervise`; runtime/ cleared |
| `rethlas rebuild` itself crashes mid-run | `knowledge_base/` in partial state (half-deleted or half-rebuilt); `AppliedEvent` possibly partial | On next `rethlas supervise` startup, librarian sees `runtime/state/rebuild_in_progress.flag` (written by rebuild before the destructive step) and force-restarts the rebuild from scratch (wipe + full replay) before entering normal operation |

### 11.3 Schema evolution

New event types, new fields, new producer kinds are append-only changes.
Existing events stay valid. `rethlas rebuild` always uses current
projection logic.

### 11.4 Git collaboration (multi-clone)

Phase I assumes the active scheduler — coordinator + children — runs
on a **single machine against a single clone**. `events/` is
git-tracked so that multiple people can share truth offline through
commits, but the runtime is **not** multi-master:

- Operator A commits new events and pushes.
- Operator B `git pull` brings new event files into `events/`.
- If B's `rethlas supervise` is running during the pull, B's
  librarian's watchdog will see new files and try to apply them.
  Events from A carry UTC `iso_ms` (§3.2), so B's librarian sorts
  them correctly against B's own local events even if A and B are
  in different time zones. Everything works **as long as A and B
  did not both author conflicting events while disconnected** (label
  collision, cycle-closing edges, etc.) — those race to first-apply
  and the loser gets `apply_failed`.
- For cleanness, Phase I recommends: when pulling in externally
  committed events, stop B's supervise first, run `rethlas rebuild`
  on the merged `events/`, then restart supervise. Rebuild is
  idempotent (§6.5 / §11.2) so this is safe to do routinely.
- Phase II may add native multi-writer conflict resolution. In
  Phase I, treating the event log as "shared via git pull, apply via
  rebuild" keeps the operational model simple.

---

## 12. Core Invariants

1. **Truth events are the sole source of mathematical truth.** `dag.kz/` and
   `nodes/` are derived.
2. **Events are immutable** after atomic rename. Compensate by adding
   events, never mutate.
3. **All content is recoverable from events.** Event files are
   self-contained.
4. **Librarian is the only writer of `dag.kz/` and `nodes/`.**
5. **Coordinator is singleton per workspace.** `rethlas supervise`
   launches exactly one coordinator, which holds
   `runtime/locks/supervise.lock`, scheduler-dispatches workers, and
   parents librarian / dashboard. It never parses events, derives
   state, or renders — librarian owns all of that.
6. **Linter only reports, never repairs.**
7. **Truth-bearing components communicate via truth events + KB.**
   Runtime orchestration uses local subprocess control and `runtime/`.
8. **Only librarian opens Kuzu.** Other Python components query KB state
   through librarian IPC. Generator uses MCP; verifier has no Phase I MCP
   tools.
9. **Codex is read-only on filesystem.** Sandbox-enforced.
10. **Truth-event producers are intentionally small in Phase I.**
    `user`, `generator`, and `verifier` are the only truth writers.
11. **No status field stored.** All status is derived from atomic fields
    (count, hashes) at query time.
12. **`pass_count` is the single progress indicator for scheduling.**
    Signed int (-1 / 0 / positive), updated by librarian per rules.
    Coordinator's dispatch decisions read only `pass_count` (plus
    dependency `pass_count`s). `repair_count` exists alongside but
    only as an advisory signal to the generator (§5.2, invariant 19).
13. **Two-layer validation (§3.1.6).** Admission (user CLI or worker
    role.py) checks structural correctness before publish; coordinator
    does a semantic pre-dispatch check against Kuzu before spawning a
    worker (§5.5.2); librarian applies the event on coordinator's
    command and records each decision in `AppliedEvent`. Workers
    themselves do no validation — they trust coordinator's job file.
14. **`events/` records proposals; KB records realized projection.**
    Every published event is preserved. `AppliedEvent(status)` tells
    which events actually mutated KB. `KB = f(events/)` is still a
    pure deterministic function.
15. **Apply_failed is terminal.** A failed event is never retried;
    producers must publish a fresh event with a new `event_id`.
16. **No cross-producer event_id synchronization.** Concurrent
    publishers allocate independently; replay order is decided by the
    `(iso_ms, seq, uid)` total sort; conflicts resolve deterministically
    at apply time.
17. **Generator write-scope invariant.** A generator batch may only
    write to (i) its own target label and (ii) brand-new labels not
    currently in KB. It may not revise any other existing node,
    including existing definitions and auxiliary lemmas (§6.2).
18. **Label prefix ↔ kind strict mapping (§3.5.2).**
    `def → definition`, `ext → external_theorem`, `lem → lemma`,
    `prop → proposition`, `thm → theorem`. Admission rejects
    prefix/kind mismatch.
19. **Two counts per node.** `pass_count` tracks accepted verdicts
    against current `verification_hash` (scheduler dispatch signal).
    `repair_count` tracks gap/critical verdicts against current
    `statement_hash` (generator decision signal: "is this statement
    itself stuck?"). Both audited by linter.
20. **Concurrency is two independent worker pools** (§10.3):
    `generator_workers` and `verifier_workers` from `rethlas.toml`,
    each with its own queue and dispatch loop, no cross-pool
    contention. The "no concurrent same-target dispatch" rule
    applies across pools.

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

### 13.9 Why 30-min Codex log timeout (default, configurable)

- xhigh reasoning can legitimately think silently for 10+ minutes
- Official Codex docs don't specify hard upper bound
- 30 min is conservative empirical value used in existing
  inducedorbit runs and is the Phase I **default**; operators with
  longer reasoning budgets raise it via `rethlas.toml [scheduling]
  codex_silent_timeout_seconds` (§2.4)
- Phase I UI: dashboard color-grades log age so the user can watch
  a long-thinking job progress without guessing when it will be
  killed (§6.7)

### 13.10 What original Rethlas keeps, and what changes

Phase I intentionally keeps the parts of original Rethlas that improved
mathematical search quality:

- adaptive proof-search skills rather than a single monolithic prompt;
- toy examples and counterexamples as first-class reasoning artifacts;
- multiple decomposition plans and failure synthesis before retrying;
- external-result search that records complete statements, source ids,
  proof ideas, local definitions, and applicability checks;
- strict verifier reporting with no silent acceptance of gaps;
- single-call verifier execution, so coordinator sees one run and one
  verdict event per target.

Phase I deliberately removes the parts that conflicted with an
event-sourced KB:

- mutable blueprint files as truth;
- generator self-verification via a verifier HTTP/MCP service;
- verifier MCP memory/result files;
- dependency context injection assembled by another process;
- unlimited recursive worker spawning inside one generator job;
- any Codex write path into truth, runtime, Kuzu, or rendered nodes.

### 13.11 Why signed count vs unsigned + separate wrong flag

- Coordinator decisions all become `if count < 0 / == 0 / < DESIRED` —
  compact
- One field vs two fields (less drift opportunity)

### 13.12 What we're NOT doing

- No complex cascade field semantics — status simply derives from
  current hashes
- No `common/mcp/` shared module — generator MCP stays under generator;
  verifier has no Phase I MCP server
- No `adapters/codex/` nesting — Phase II if/when Claude is added
- No runtime heartbeat files — log mtime suffices
- No status enum in Kuzu

---

## 14. Open Items

These are resolved at implementation time without blocking design:

1. **`rethlas init` scaffolding** — what a fresh workspace contains
2. **Frontend minimal HTML/JS** — exact markup and style for Phase I
   dashboard
3. **API key management** — environment variables inherited from
   the `rethlas supervise` shell into Codex wrappers (§6.7.1 job
   lifecycle step 1). Phase I does not add Rethlas-specific key
   storage; operators use whatever Codex expects (`OPENAI_API_KEY`
   etc.).
4. **Windows support** — Phase II. Requires replacing `flock`,
   process groups, and POSIX signals with Windows equivalents
   (named mutexes, Job Objects, `signal.CTRL_C_EVENT`).
5. **`runtime/logs/` retention** — Phase I has no rotation; logs
   accumulate until `rethlas rebuild` wipes them. Phase II may add
   size-based or age-based rotation, or a `rethlas logs prune` CLI.
6. **`external_theorem.source_note` structured parsing** — Phase I
   treats it as free-form text (operator convention, BibTeX key or
   DOI or plain citation). Phase II can add structured parsing if
   downstream tooling (importers, citation checks) needs it.
7. **Orphan aux-node GC** — a generator aux lemma that no live
   node transitively depends on (e.g. the target that introduced it
   was later revised to a counter-example with a different proof
   structure) stays in KB forever. Phase I accepts this as harmless
   noise; Phase II may add a "reachability from top-level theorems"
   garbage-collection sweep (report-only or opt-in delete via a
   compaction event).
8. **Filesystem event delivery on network / container FS** —
   Phase I's dashboard and librarian rely on `watchdog` (Linux
   inotify / macOS FSEvents). These work on local ext4/APFS but
   **may silently drop events on NFS, docker bind mounts with
   inconsistent eventing, or certain remote filesystems**.
   Symptom: new events land on disk but dashboard doesn't fire SSE,
   librarian doesn't pick them up until next full scan. Phase I
   recommends running the workspace on local disk; Phase II can add
   a periodic polling fallback for network / container FS.

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
- **2026-04-24 (evening review, H1–H2)**:
  - H1: `pass_count` init rule no longer drops definitions /
    external_theorems to -1 on revision — they always go to 0 awaiting
    verifier. Introduced `initial_count(kind, proof)` helper used by
    all three node-rewriting events (§5.4).
  - H2: dropped cross-producer event_id global monotonicity. `uid`
    bumped from 8 hex to 16 hex. Introduced two-layer validation
    (admission structural + librarian semantic, §3.1.6) and the
    `AppliedEvent` Kuzu table (§5.2) which deterministically records
    `applied` / `apply_failed(reason)` per event. "Workspace
    corruption" now narrowly means a structural failure on a canonical
    event (admission escape). Apply_failed is terminal — failed events
    are never retried. Producers poll `AppliedEvent` to learn their
    proposal's fate. No publish-time lock.
- **2026-04-24 (evening review, H3–H5)**:
  - H3: explicit statement that verifier/generator write-set overlap
    is not prevented (§6.4.1). Correctness is preserved by the
    hash-match gate + `apply_failed(hash_mismatch)`; Phase I accepts
    the LLM waste and does not pause verifier dispatch during
    generator runs.
  - H4: `repair_hint` internal format pinned (verifier section +
    user-appended sections separated by `---`). Rules: verifier
    verdict overwrites verifier section only; user hint appends a new
    user section; hash change clears everything. Admission rejects
    `user.hint_attached` targeting a node with `pass_count >= 1`.
    New reason code `hint_target_unreachable` for the narrow race.
  - H5: repair-must-change-hash computes the target's post-batch
    verification_hash with staged-batch resolution for batch-internal
    deps; existing deps (unchanged per write-scope) use KB values.
- **2026-04-24 (evening review, H7–H9)**:
  - H7: generator write-scope invariant (§6.2) — every batch label
    must be either the batch's target or absent from current KB.
    Generator cannot revise existing definitions, aux lemmas, or any
    other existing node. "Definition fix on wrong verdict" reduces to
    user-only (§5.1).
  - H8: label prefix ↔ kind strict mapping enforced at admission
    (§3.5.2 table). Prevents misleading labels and gives dashboard /
    Codex / linter a reliable visual cue on kind.
  - H9: new `Node.repair_count` field (§5.2, §5.4). Tracks gap/critical
    verdicts against current `statement_hash`. Resets to 0 on any
    `statement_hash` change (including Merkle cascade). Generator
    reads `repair_count` in its repair prompt to decide between local
    patch vs statement revision vs counter-example. `KnowledgeBase`
    Protocol's `count_repair_attempts(label, hash)` renamed to
    `repair_count(label)`. Linter category D audits this field
    against event-stream replay.
- **2026-04-24 (evening review, dashboard D1–D8)**:
  - New §6.7.1 "Runtime interface contract" consolidates all
    runtime-file schemas, writers, and lifecycles that dashboard
    depends on.
  - Extended §6.5 librarian.json schema: adds `pid`, `started_at`,
    `events_applied_total`, `events_apply_failed_total`,
    `projection_backlog`, `rebuild_in_progress`, `last_rebuild_at`.
  - Pinned down `runtime/jobs/{job_id}.json` schema (schema, job_id,
    kind, target, mode, dispatch_hash, pid, pgid, started_at,
    updated_at, status, log_path) and `status` enum (starting /
    running / publishing / applied / apply_failed / timed_out /
    crashed / orphaned).
  - Job file lifecycle pinned: coordinator creates, wrapper updates,
    wrapper/coordinator writes terminal status then deletes;
    coordinator runs a per-tick orphan reaper for wrappers that
    crashed before writing a terminal state.
  - SSE envelope schema (`type` + `ts` + `payload`) and event types
    (truth_event / applied_event / job_change / coordinator_tick /
    librarian_tick / alert). Dashboard is the sole SSE emitter; all
    emissions come from its own file-watching, preserving component
    isolation.
  - `/api/events` query strategy: reverse-chronological shard walk
    in `events/`, no separate index maintained in Phase I.
  - Dashboard bind: default `127.0.0.1:8765`; configurable via
    `rethlas.toml [dashboard] bind` and `--bind`; non-loopback binds
    log a startup warning (Phase I has no auth).
  - `rethlas rebuild` behavior: librarian sets
    `rebuild_in_progress = true` before unlinking `dag.kz/`;
    dashboard returns 503 `Retry-After: 5` for KB-dependent
    endpoints while it is `true`; runtime-only endpoints stay up.
  - Kuzu concurrent-read note: librarian is the only process that
    opens Kuzu; other components read via librarian IPC. Brief query
    latency spikes during commit are accepted in Phase I.
  - Staleness thresholds (dashboard UI only): `<= 60 s` healthy,
    `60 s < age <= 5 min` degraded, `> 5 min` / missing down. Same
    thresholds for coordinator.json and librarian.json.
  - Phase I M6.3 (new) and M7.1-7.5 updated to cover the contract.
- **2026-04-24 (evening review, generator concurrency 1 → N, default 2)**:
  - §6.4.1 / §10.3: raised generator in-flight cap from **1** to
    **`N`**, configurable via `rethlas.toml [scheduling]
    max_concurrent_generators` with default **2**. Justified by the
    §6.2 write-scope invariant (H7): a generator batch only touches
    its own target + brand-new labels, so any number of concurrent
    batches on distinct targets cannot corrupt each other's write
    sets. The remaining cross-batch race is two (or more) batches
    independently inventing the same brand-new label; resolved
    deterministically by `(iso_ms, seq, uid)` ordering +
    `apply_failed(label_conflict)` for the losers (wasted work, not
    corruption). Dashboard's "expected normal events" list extended
    to include occasional `label_conflict` alongside `hash_mismatch`.
    Default 2 is a conservative starting point; operators can raise
    `N` when they have LLM budget headroom.
  - §5.1 "goal concept" paragraph sharpened: Phase I stop condition
    ranges over **all** nodes, not a separately configured goal set;
    "goal" is just user-side naming convention for `kind=theorem`.
  - Phase I M6.1 updated to spell out the new concurrency rules
    (no concurrent same-target, at most `N` generators from config,
    verifiers share the remaining `codex_budget` slots).
- **2026-04-24 (evening review, worker pools + review nits R2–R6)**:
  - §10.3 rewritten: generator and verifier run as **two independent
    worker pools** with separate configurable capacities
    (`generator_workers`, default 2; `verifier_workers`, default 4).
    The old `common/codex_budget` shared-slot mechanism is superseded
    — each pool has its own queue and no cross-pool precedence
    contention.
  - §6.4.1 concurrency intro rewritten to match the worker-pool model.
  - R2: `AppliedEvent` gains a `detail STRING` column (capped 512
    bytes). Librarian populates a short human-readable explanation
    for every apply_failed row so dashboard can surface concrete
    failure context without cross-joining to `events/` content.
    Semantic-check signature updated in §6.5 / §9.2 pseudocode.
  - R3: `runtime/jobs/*.json` `dispatch_hash` semantics pinned for
    all three modes (generator fresh / generator repair / verifier
    single) — always the target's `verification_hash` read from Kuzu
    at dispatch time.
  - R4: `/api/events?limit=N` is clamped to `[1, 500]` — requests
    above 500 are silently capped; < 1 returns HTTP 400. Prevents a
    single request from scanning the entire events directory.
  - R5 + new §2.4: `rethlas.toml` parse and validation behavior
    spelled out — missing file / section / field all fall back to
    defaults; malformed TOML or out-of-range values fail-fast at
    startup. Config is read once at process start (no hot reload in
    Phase I).
  - R6: `idle_reason_detail` capped at 512 bytes (truncated with
    `...`).
- **2026-04-24 (supervisor merged into coordinator + A1/A4/A5 + linter E)**:
  - No separate "supervisor" role. Coordinator is the workspace
    singleton: it holds `runtime/locks/supervise.lock`, launches
    librarian + dashboard as its own subprocesses, parents all
    generator / verifier worker subprocesses, and runs the scheduler
    loop. One process, one lock, one heartbeat file. §6.4 rewritten
    to document this merged role (process tree, singleton lock,
    child daemon management, graceful shutdown, interaction with
    one-shot CLIs). Invariant 5 updated to capture coordinator
    singleton.
  - `coordinator.json` schema gains `children` field (librarian /
    dashboard pid + status) so dashboard surfaces the whole tree in
    one place. `supervisor.json` removed from runtime/state.
  - A1 (real bug): librarian startup now does a `nodes/`
    reconciliation pass after event replay — for every Kuzu node at
    `pass_count >= 1`, diff on-disk `.md` vs expected render and
    rewrite / delete as needed. Closes the crash window between Kuzu
    commit and per-event re-render that previously left stale
    `nodes/` a verifier could read via Codex to form a wrong accept
    verdict.
  - Linter category E: `nodes/` ↔ Kuzu consistency audit, with
    `--repair-nodes` for explicit fix. Startup reconciliation +
    linter E form a two-layer safety net.
  - A4: linter refuses by default while `supervise.lock` is held;
    `--allow-concurrent` overrides with a transient-drift
    disclaimer in the report header.
  - A5: `rethlas init` refuses when `events/` or `rethlas.toml`
    already exist; `--force` allows overwriting `rethlas.toml` only
    (never `events/`).
  - `rethlas rebuild` takes the same supervise lock (refuses if
    supervise is running; holds the lock itself while rebuilding).
  - PHASE1 M2.2 / M2.3 / M3.4 / M6.4 / M6.5 / M8.1 / M8.3 updated to
    cover these CLI-level contracts.
- **2026-04-24 (B1–B6 review)**:
  - **B2 (broadened)** _(superseded by the workers-are-Kuzu-free
    review below; the wrapper no longer validates against Kuzu and
    the `precheck_failed` status no longer exists — see §5.5.2 and
    §6.7.1 for the current model)_: §5.5.2 renamed "Pre-dispatch
    hash revalidation" → "Pre-dispatch validation". At the time of
    this B2 decision, the gate was the wrapper (not coordinator):
    on worker startup, before calling Codex, wrapper re-checked
    **every** dispatch condition against current Kuzu (target
    exists, hash matches `dispatch_hash` from job file, pass_count
    in expected band, strict-monotone deps, deps visible in
    `nodes/` for generator, no other in-flight job on same target,
    repair-mode `H_rejected` still current). Failure →
    `status = "precheck_failed"` with detail naming the condition;
    exit without Codex call. Coordinator still writes the snapshot
    `dispatch_hash` for forensic anchoring. New job status
    `precheck_failed` added to §6.7.1 enum.
  - **B1**: per-child restart policy pinned (§6.4):
    librarian restarts once; re-crash → coordinator exits with
    code 3 (workspace unusable). Dashboard restarts up to 3×
    with 30 s backoff; further failures mark degraded but leave
    librarian/coordinator running.
  - **B3**: `rethlas rebuild` writes
    `runtime/state/rebuild_in_progress.flag` before the destructive
    step, deletes it on successful completion. Crash mid-rebuild
    leaves the flag; librarian detects it on next startup and
    force-reruns rebuild before normal operation. §2.2 tree +
    §6.5 rebuild sequence + §11.2 recovery table updated.
  - **B4**: `AppliedEvent.detail` uses 12-hex-char (48-bit)
    prefixes for hash values.
  - **B5**: `rethlas init` writes a fully-annotated `rethlas.toml`
    listing every known field with its default and a one-line
    comment (PHASE1 M2.2 updated with template).
  - **B6**: §2.3 adds an exit-code table shared across all CLIs
    (0 success, 1 generic, 2 lock/uninitialized, 3 critical child
    crash loop, 4 config error, 5 linter violations, 6 init refused).
- **2026-04-24 (C1–C7 review)**:
  - **C1**: new linter category **F** — `events/` ↔ `AppliedEvent`
    inventory audit detects manual edits / deletions / git reverts
    against `events/` content. `AppliedEvent` gains a
    `event_sha256 STRING` column (librarian records the file hash
    at apply time). No auto-repair; fixing requires `rethlas
    rebuild` after operator decides which side is correct.
  - **C2**: §4.2 adds a **rendering contract** for `nodes/*.md`:
    Unix `\n`, UTF-8 NFC, fixed YAML key order, `depends_on`
    sorted, fixed section order, no timestamps / host data.
    Rendering is byte-deterministic so startup reconciliation and
    linter category E use content-equality checks without false
    positives. All four rendering paths (per-event, startup,
    `--repair-nodes`, rebuild) invoke the same
    `librarian/renderer.py` function.
  - **C3**: §7.5 pins malformed-output handling — wrapper writes
    `status = "crashed"` with `detail = "verdict parse failed: ..."`;
    no auto-retry; coordinator surfaces targets that hit 3
    consecutive `crashed` outcomes as `"<kind> unstable on
    <label>"` under Dashboard → Human Attention.
  - **C4**: coordinator passes workspace path to wrappers via
    `RETHLAS_WORKSPACE` env var; wrappers locate Kuzu / runtime
    files without relying on inherited cwd (§6.7.1 job lifecycle
    step 1).
  - **C5**: `rejected_writes.jsonl` / `drift_alerts.jsonl` enforce
    line-length caps (`detail` ≤ 1024 B, line ≤ 2048 B, truncated
    with `...(truncated)`) plus `O_APPEND | O_CLOEXEC` single-write
    discipline to guarantee atomic concurrent append under POSIX
    `PIPE_BUF`.
  - **C6**: §8 pins **MCP stdio transport** (not TCP) for
    generator's MCP server — with `generator_workers > 1` there are
    multiple concurrent MCP server processes, and stdio transport
    eliminates port-clash scenarios entirely.
  - **C7**: new §11.4 documents the git-collaboration operational
    model — Phase I assumes single-machine-single-clone runtime;
    bringing in externally committed events from `git pull` should
    be followed by `rethlas rebuild` for cleanliness. Multi-writer
    native conflict resolution is Phase II.
- **2026-04-24 (D1–D8 review)**:
  - **D1**: §1 pins **Linux + macOS** as supported platforms for
    Phase I (flock / POSIX O_APPEND / process groups / POSIX
    signals are assumed). Windows support added to §14 Open Items
    as Phase II.
  - **D2**: §9.1 pins the **user CLI feedback contract**. CLI
    polls `AppliedEvent` for 30 s. Four outcomes: `applied` / 
    `apply_failed` (both exit 0 — event is truth), or timeout with
    `supervise.lock` held ("librarian slow; queued") or free
    ("supervise not running; queued"). Non-zero exits are reserved
    for structural admission failures — the event didn't reach
    `events/`.
  - **D3**: `rethlas dashboard` standalone now checks
    `supervise.lock` first. If held, prints "supervise is running
    and has already started a dashboard at `<bind>`" and exits 0;
    otherwise proceeds to bind normally. Eliminates EADDRINUSE
    surprise when operator forgets supervise already has a dashboard.
  - **D4**: **Supervise startup runtime cleanup** — coordinator,
    after acquiring the lock and before spawning children, deletes
    stale `runtime/jobs/*.json`, `runtime/state/coordinator.json`,
    and `runtime/state/librarian.json`. Preserves
    `rebuild_in_progress.flag`, both JSONL logs, and
    `runtime/logs/*.codex.log`. Dashboard's first post-restart read
    shows a clean "starting" state instead of zombie jobs from the
    previous crash.
  - **D5**: §2.3 spells out that **every Phase I CLI** accepts
    `--workspace <path>`; default is cwd.
  - **D6**: §14 Open Items adds **`runtime/logs/` retention** as
    Phase II (no rotation in Phase I; `rethlas rebuild` wipes).
  - **D7**: `AppliedEvent.event_sha256` pinned to SHA-256 of the
    event file's **raw bytes** (no canonicalization) so any
    whitespace / formatting change counts as tampering.
  - **D8**: §14 Open Items adds **structured `source_note` for
    external_theorem** as Phase II (Phase I treats it as free-form
    operator-convention text — BibTeX key / DOI / plain citation).
- **2026-04-24 (E1–E6 review)**:
  - **E1**: `iso_ms` pinned to **UTC** (§3.2) — no local-time
    ambiguity, so cross-timezone git collaboration (§11.4) sorts
    events correctly.
  - **E2**: 30-second **startup grace period** (§6.4) — coordinator
    only checks `os.kill(pid, 0)` liveness for the first 30 s after
    spawning a child; heartbeat staleness is ignored until then.
    Missing first heartbeat after 30 s → `startup_timeout` →
    restart policy kicks in.
  - **E3**: batch **cycle detection algorithm pinned** (§6.2) —
    admission builds post-batch graph in memory (KB `DependsOn`
    edges + batch `\ref` edges), runs DFS / Tarjan, reports the
    cycle path in `detail`. Librarian's apply-time check is Kuzu
    native cycle query as defense-in-depth.
  - **E4**: exit code 4 now explicitly covers **workspace not
    writable** (cannot create `runtime/locks/`).
  - **E5**: §4.1 documents the **Kuzu concurrency assumption** —
    snapshot isolation is assumed for readers; if weaker, the
    pre-dispatch hash gate + verdict hash-match gate catch any
    inconsistency before it corrupts truth.
  - **E6**: §14 Open Items adds **orphan aux-node GC** as Phase II
    (accepted noise in Phase I).
- **2026-04-24 (F1–F4, long Codex think time)**:
  - **F1**: `codex_silent_timeout_seconds` (default 1800, floor 60)
    added to `rethlas.toml [scheduling]` (§2.4). §7.4 rule
    references the configured value, not hardcoded 30 min.
  - **F2**: Dashboard Active Jobs section color-grades Codex log
    age against the configured timeout T:
    green (≤5 min) / yellow (≤ min(T/2, 15 min)) / orange (< T) /
    red (≥ T). Operator can distinguish "thinking" from "stuck"
    without memorizing the timeout.
  - **F3**: Coordinator tracks consecutive `timed_out` outcomes
    per target (parallel to consecutive `crashed` in C3). After 3
    consecutive timeouts, surfaces `"<kind> frozen on <label>"`
    under Dashboard Human Attention so the operator can intervene
    before burning more LLM budget.
  - **F4**: Wrapper refreshes `runtime/jobs/{job_id}.json`
    `updated_at` **every 60 s** during the Codex run (status
    stays `running`). Dashboard uses both signals together:
    fresh wrapper heartbeat + stale log mtime ⇒ Codex is reasoning
    silently (normal); stale wrapper heartbeat ⇒ wrapper itself
    is unhealthy. PHASE1 M4.5 / M5.5 / M6.2 updated accordingly.
- **2026-04-24 (G1–G6 review)**:
  - **G1**: Workspace-wide timestamp convention pinned in §2.4
    trailer: all runtime timestamps (coordinator.json, librarian.json,
    AppliedEvent.applied_at, runtime/jobs/*.json, jsonl `ts`, linter
    report, rebuild flag) are UTC ISO 8601 with `Z` suffix. Truth
    event body's `ts` keeps its local-offset form for operator
    context; sorting never reads it.
  - **G2**: Librarian startup is now a tracked phase —
    `librarian.json.startup_phase ∈ {replaying, reconciling, ready}`.
    Coordinator's scheduler loop suppresses all worker dispatch
    while `startup_phase != "ready"` or `rebuild_in_progress`;
    `idle_reason_code = "librarian_starting"`. Closes the "worker
    pre-dispatch reads half-projected Kuzu" hole on large workspaces.
  - **G3**: §9.1 write pseudocode pinned — `fsync(tmp_fd)` then
    rename, then `fsync(parent_dir_fd)`. Without the parent dir
    fsync, a crash between rename and cache flush can leave the
    event file's bytes on disk but its name invisible. Both fsyncs
    are required for durability of the event-as-truth contract.
  - **G4**: Wrapper spawn now explicitly inherits the full env from
    `rethlas supervise` (API keys, PATH, etc.) plus
    `RETHLAS_WORKSPACE` overlay. §14 Open Items API-key-management
    entry updated accordingly.
  - **G5**: §6.7.1 adds Python daemon log files
    (`runtime/logs/{supervise,librarian,dashboard}.log`) distinct
    from per-job Codex logs. Same no-rotation policy; `rethlas
    rebuild` truncates.
  - **G6**: §6.4 spells out Ctrl+C handling — SIGINT to process
    group triggers the graceful shutdown cascade; second Ctrl+C
    during the 10 s wait escalates to SIGKILL; external SIGKILL of
    coordinator is handled by OS (children die via process group)
    plus D4 startup cleanup on next supervise.
- **2026-04-24 (Kuzu-access boundaries — workers file-only,
  librarian sole writer and passive, coordinator owns event→Kuzu
  command flow)**:
  - **K1 — Workers are Kuzu-free (§4.1, §6.2, §6.3)**: generator
    and verifier `role.py` (and the `common/runtime/` modules they
    transitively import) **must not link Kuzu**. They read input
    only from (a) the coordinator-authored job file under
    `runtime/jobs/{job_id}.json` and (b) `nodes/*.md` files on
    disk. Dep `statement_hash`es reach the generator's decoder via
    the job file's `dep_statement_hashes` map (populated by
    coordinator at precheck) with `nodes/*.md` frontmatter as the
    fallback source; the verifier uses `dispatch_hash` from the
    job file as the verdict's `verification_hash`. `nodes/*.md`
    frontmatter now carries both `statement_hash` and
    `verification_hash` so this read path is self-sufficient
    (§4.2 rendering contract). Enforced by a static test that
    greps worker-reachable modules for `common/kb` imports
    (PHASE1 M5 / M6 / M7).
  - **K2 — Librarian is the sole Kuzu writer and is passive
    (§6.5)**: librarian no longer watches `events/`. It runs a
    startup replay + `nodes/` reconciliation, then waits for
    `APPLY(event_id, path)` commands from coordinator over the
    command channel and replies `APPLIED` / `APPLY_FAILED` /
    `CORRUPTION`. APPLY commands received before
    `startup_phase = ready` are queued and drained exactly once
    after ready (PHASE1 M4 "APPLY-during-startup queuing" test).
    Kuzu concurrency model is single-writer (librarian) +
    multi-reader (coordinator, dashboard, user CLI).
  - **K3 — Coordinator owns the event→Kuzu command flow
    (§5.5.2, §6.5, §6.7.1)**: coordinator (not wrapper) runs the
    full pre-dispatch validation against Kuzu before spawning a
    worker. On precheck failure it writes no job file — the
    candidate is skipped and the reason is logged to
    `runtime/logs/supervise.log`. The `precheck_failed` job
    status introduced by the earlier B2 review is **gone**
    (§6.7.1). Coordinator also owns the `events/` watchdog: new
    event files dropped via atomic rename are detected by
    coordinator, which sends an APPLY command to librarian; the
    outcome is reflected back into coordinator's dashboard state
    (SSE `applied_event` envelope). After a wrapper exits with
    job status `publishing`, coordinator polls
    `AppliedEvent(event_id)` on its next tick and writes the
    terminal status (`applied` / `apply_failed` + reason +
    detail) into the job file before deleting it.
- **2026-04-25 (pre-M2 Kuzu stress validation — single-process
  Kuzu)**:
  - **L1**: PHASE1 pre-M2 stress tests against Kuzu 0.11.3 showed
    Kuzu uses an exclusive file lock on the database directory
    regardless of read/write intent. The §4.1 "single-writer /
    multi-reader across OS processes" assumption therefore cannot
    be met — while librarian holds the write lock, no other OS
    process can open the DB even read-only.
  - **L2**: §4.1 pivoted to a **single-process Kuzu model**.
    Librarian is the only process that opens `dag.kz/`. Multiple
    `kuzu.Connection` handles inside the librarian process serve
    concurrent read RPCs. Every other process (coordinator,
    dashboard, linter, user CLI) reaches KB state via librarian's
    IPC command channel — the same channel that already carries
    `APPLY(event_id, path)` writes (K2). A `QUERY(...)` command
    was added to the channel for reads.
  - **L3**: The hash-match gate in §5.5.0 remains the correctness
    backstop for coordinator-snapshot vs apply-time drift, exactly
    as before.
