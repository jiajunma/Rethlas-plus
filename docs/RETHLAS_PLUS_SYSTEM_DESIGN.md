# Rethlas-plus System Design

Date: 2026-05-02

This document turns the Rethlas-plus discussion into a concrete system shape.
The design principle is:

```text
Rethlas is the harness. Codex is the reasoning engine.
```

Rethlas should manage durable state, graph expansion, MCTS bookkeeping,
context packing, schemas, replay, and admission. Codex should supply
mathematical judgment through bounded agent calls.

## System Boundary

Rethlas should not try to become a custom prover brain. Its durable value is
the harness around increasingly capable reasoning models.

Rethlas owns:

- node documents and library layout;
- Kuzu graph/hypergraph indexes;
- event journal and replay;
- hash canonicalization and invalidation;
- goal sets, run state, branch budgets, and stop conditions;
- legal action enumeration;
- MCTS statistics and backup;
- context packets for Codex;
- prompt/output schemas;
- deterministic admission and state transitions;
- dashboard/API observability.

Codex owns:

- mathematical strategy;
- proof generation;
- informal proof criticism;
- premise relevance judgment when retrieval is ambiguous;
- policy/value priors for MCTS;
- failure analysis and route recommendations.

## Authority Model

Do not make node documents, events, and Kuzu into three independent sources of
truth. Phase 2 should use one editing surface, one journal, and one projection:

- **Current working object**: Markdown+YAML node documents under topic
  subdirectories, `knowledge_base/nodes/<topic_path>/**/*.md`.
- **Mutation journal**: append-only events recording admitted changes,
  provenance, conflicts, and replay history.
- **Derived projection**: Kuzu graph/index, rebuilt from admitted state when
  stale or corrupted.

The librarian is the only component allowed to synchronize these views. Manual
Markdown edits should be ingested as proposed node revisions, validated, and
then reflected into the journal/projection. Generator/verifier/search outputs
also go through librarian admission. Runtime jobs never write durable
mathematical state directly.

The node library should be directory-organized by topic. Labels remain the
stable identity, but canonical storage uses `topic_path`:

```text
knowledge_base/nodes/<topic_path>/<kind_prefix>_<slug>.md
```

`topic_path` is a multi-level mathematical topic taxonomy, for example
`lie_theory/nilpotent_orbits/real_groups/induction`. It should not encode
source id, run id, agent id, date, or temporary branch identity. Source
organization belongs under `sources/` and provenance metadata; proof-search
organization belongs under `search`/`goal` metadata and runtime state.

The root of `knowledge_base/nodes/` is reserved for indexes or library-level
notes. Ordinary theorem/lemma/definition nodes should live under mathematical
topic subdirectories. Moving a node between topics is metadata-only and must
not change statement/proof/verification hashes.

During migration from Phase I, events can remain the replay root while node
documents become the user/agent-facing current snapshot. Once node-doc
normalization and hashing are stable, the system can checkpoint snapshots and
replay events after the checkpoint. The invariant is simpler than either
implementation detail: no state change is accepted unless the librarian can
make node docs, events, and Kuzu agree.

## Core Modules

Recommended Phase 2 module layout:

```text
common/
  node_docs/        parse/render md+YAML nodes
  hashing/          canonical statement/proof/dependency hashes
  search_types/     run/branch/action/value dataclasses

librarian/
  sync.py           nodes <-> Kuzu <-> events reconciliation
  admission.py      deterministic validation of node/search events
  projection.py     Kuzu graph/index updates
  import_export.py  KB snapshot manifests and merge plans
  merge.py          conflict classification and deterministic import admission

search/
  goals.py          goal sets, closure status, run_done
  frontier.py       legal action enumeration
  mcts.py           visit counts, Q values, UCT/PUCT selection, backup
  context.py        compact context packets for Codex
  scheduler.py      verifier/generator job selection under budgets
  signatures.py     normalized failure signatures
  promotion.py      deterministic promotion constraints

agents/
  selector/         Codex policy/value prompt + decoder
  generator/        branch proof/helper generation
  verifier/         local informal proof verification
  failure/          failure clustering / branch closing
  promotion/        semantic compatibility recommendation
  learner/          Phase 3 source-to-KB extraction
  referee/          Phase 3 review / citation checking

sources/
  artifacts/        original PDFs/TeX, page images, OCR/layout/TeX outputs
  spans.py          source-span records with page/bbox or TeX line/confidence
  ocr.py            scanned-PDF OCR pipeline
  tex.py            TeX project parsing, theorem envs, refs/citations/macros
  alignment.py      PDF span <-> TeX span alignment
  citations.py      external reference retrieval and evidence records

reviews/
  reports/          referee report Markdown/YAML records
  issues/           requested details, gaps, citation failures, severity
  evidence/         source spans, citation checks, extraction-quality records
  repairs/          repair attempts and verifier results, not KB truth

dashboard/
  search_state      goals, branches, MCTS stats, blocked/stuck states
```

This is conceptual. It can be implemented incrementally inside the existing
packages first, then split once boundaries stabilize.

Phase 3 adds `learner` and `referee` as top-level Codex-call roles. They are
orchestration agents over source spans, node drafts, generator, verifier, and
retrieval. Generator and verifier remain inner tools; learner/referee do not
directly mutate durable state.

Runtime-wise, `learner` and `referee` should be independent roles like
`generator` and `verifier`: each gets its own job type, prompt contract,
decoder, output schema, logs, budget policy, and scheduler lane. The difference
is the abstraction level. Generator/verifier are primitive proof workers;
learner/referee are source/review orchestration workers that may request those
primitive workers through the harness.

PDF/OCR/TeX handling belongs in `sources/`, not inside learner/referee
prompts. Learner/referee receive structured spans, page-image references, TeX
references, layout data, macro-context hashes, alignment records, and
confidence scores. Low-confidence OCR/layout/alignment spans can block
learning or review until visual/manual correction.

The two Phase 3 agents have different durable outputs:

- `learner` outputs source-backed candidate node batches, dependency edges,
  bridge-lemma requests, and extraction issues.
- `referee` outputs review reports, verdicts, citation checks, verified repair
  records, and unresolved gap reports.

They may both request generator/verifier work, but the request is scoped by the
top-level role. Learner asks "what can be learned into the KB from this
source?" Referee asks "is this claim/proof correct, and where does it fail?"
This distinction prevents review work from silently becoming a broad import
job, and prevents source ingestion from silently accepting unreviewed gaps.

Learner's long-term product is a larger, better indexed knowledge base.
Generator should consume learner output only after librarian admission and
indexing:

```text
learner batch -> librarian admission -> node docs/Kuzu/retrieval indexes -> generator context
```

Generator should not read raw source corpora or learner scratch output as its
normal premise source. It should call retrieval over admitted nodes, aliases,
proof variants, verification status, referee issues, and source provenance
summaries. This keeps generated proofs grounded in the KB and prevents
unreviewed source extraction from silently becoming proof truth.

Phase 3 should reuse the existing worker pipeline:

```text
scheduler/CLI -> runtime job -> role wrapper -> Codex -> decoder -> event -> librarian
```

but use a v2 job envelope for source-oriented work. Generator/verifier v1 jobs
are node-targeted; learner/referee jobs need role-specific `input` packets
with source ids, span ids, page-image references, layout references, KB
matches, TeX references, PDF-TeX alignment context, citation context, and
budgets.

The Phase 3 scheduler should avoid deep nested Codex calls. Learner/referee
emit structured requests such as `bridge_requested`,
`verification_requested`, `citation_requested`, and `manual_check_needed`.
The scheduler then dispatches generator, verifier, retrieval, or dashboard
manual-review work and resumes the source/review job with the result. This
keeps budgets, logs, retries, and provenance observable.

Referee should write to a separate review workspace, not directly to
`knowledge_base/nodes/`. Its default durable output is a report with issues,
requested details, evidence, repairs attempted, and recommended KB updates.
Those recommendations are proposals. They become node revisions, aliases,
proof variants, or new lemmas only after a separate librarian admission step.

Source artifacts are immutable at the original-file layer. Supported primary
inputs are PDF-only, TeX-only, and PDF+TeX. Derived artifacts such as page
images, OCR text, hOCR/layout JSON, TeX AST JSON, PDF-TeX alignment JSON,
manual corrections, annotated review PDFs, and citation evidence are stored
separately and referenced by hash from source records and source spans.

Knowledge-base merge is a Phase 2 library operation. Merge node/source
documents and snapshot manifests, then rebuild Kuzu. Do not merge Kuzu database
files directly.

Two merge modes should exist:

- **Federated index**: keep a remote KB read-only and queryable through its
  manifest. Remote nodes can inform retrieval but are not local truth.
- **Deep import**: admit selected remote nodes/sources into the local library
  through a merge plan and librarian validation.

Deep import should still be two-step:

```text
staged import / federated merge -> dedup/canonicalization -> local admission
```

The first step places remote nodes in a staging namespace or read-only
federated index. The second step decides exact aliases, proof variants,
canonical imports, skips, and user-required conflicts. This prevents duplicate
or conflicting remote nodes from being written into canonical local labels
before the system understands whether they are the same theorem.

Foreign events are audit evidence, not automatically replayed as local truth.
The local journal should record local import/admission events with remote
origin metadata. If two libraries contain the same statement with different
proofs, preserve the imported proof as an alternate proof variant or
namespaced node; do not discard verified proof routes.

Phase 4 adds formalization-aware merge on top of this. It should not replace
Phase 2 merge. Instead, it decides whether imported formal artifacts are
portable under the local pinned environment:

- Lean/Coq/Isabelle system and version;
- mathlib/library commit;
- Lake or package manifest hash;
- build options hash;
- formal module path and declaration name;
- formal dependency closure hash;
- artifact hashes such as `.lean`, `.olean`, `.ilean`, `.c`, and native
  objects.

If the formal environment matches, imported formal verification can become
local checked evidence after local validation. If it differs, the informal
node/source import can still proceed, but the formal artifact should be marked
`historically_checked`, `needs_port`, or `stale_formal_environment` until
rebuilt or ported locally.

## First-class Data Objects

### NodeDocument

One theorem/lemma/definition/candidate as one Markdown file with YAML
frontmatter.

Owns:

- label;
- kind;
- topic path and tags;
- statement/proof text;
- dependency refs;
- verification metadata;
- goal/search metadata.

### SearchRun

One goal-directed run.

Suggested fields:

```yaml
run_id: run_2026_05_02_main
goal_set: main
status: active
root_goals:
  - thm:induced_orbit_toy_problem
budgets:
  max_active_branches_per_goal: 4
  max_expansions_per_goal: 12
  max_repairs_per_branch: 2
mcts:
  exploration_c: 0.8
  use_priors: true
```

Statuses:

```text
active | done | budget_exhausted | counterexample_found |
needs_user | degraded
```

### SearchBranch

One proof direction for one goal or subgoal.

Suggested fields:

```yaml
branch_id: b002
run_id: run_2026_05_02_main
root_goal: thm:induced_orbit_toy_problem
parent_branch: b001
status: active
strategy: slice_model_open_dense_reduction
candidate_label: thm:induced_orbit_toy_problem_b002_candidate
owned_labels:
  - lem:induced_orbit_toy_problem_b002_slice_bridge
failure_signatures: []
stats:
  visits: 3
  q_value: 0.41
  prior: 0.35
```

Statuses:

```text
active | cooldown | stuck | abandoned | exhausted | promoted
```

### SearchAction

An action available to MCTS/scheduler.

```text
verify(node)
repair(branch)
expand(branch)
spawn_sibling(branch)
defer(branch)
abandon(branch)
promote(candidate)
```

Only deterministic code enumerates legal actions. Codex may score or recommend
among them, but should not invent illegal actions.

## State Machine Decomposition

Phase I effectively uses one overloaded mathematical state:

```text
pass_count = -1  -> needs generation / repair
pass_count >= 0  -> has content, can enter verifier queue
pass_count >= desired_pass_count -> treated as done
```

This was a good bootstrap state machine, but it is too coarse for Phase 2.
It mixes content existence, verification confidence, dependency closure,
repair routing, scheduler priority, and run completion. Phase 2 should split
state into separate domains with explicit owners.

### Content State

Owned by `NodeDocument` plus admitted node events.

Tracks:

- authored statement/proof/remark/source note;
- dependency refs;
- statement/proof/dependency hashes;
- topic path, tags, and library role.

Content state is mathematical material, not runtime progress. Editing topic
metadata must not invalidate proof verification. Editing a statement must
invalidate downstream verification inputs.

### Verification State

Owned by deterministic verifier admission and hash invalidation.

Recommended fields:

```yaml
verification:
  local_status: unverified | accepted | rejected | stale
  closure_status: open | closed_verified | blocked_on_dependency |
    blocked_on_unresolved_ref | stale_due_to_dependency
  verification_hash: sha256:...
  closure_hash: sha256:...
  last_report: ...
  last_rejected_hash: sha256:...
```

`local_status=accepted` only means this node's proof survived the verifier
against the dependency statements it cited. `closure_status=closed_verified`
means the whole dependency closure is accepted and current. Goal completion
must use `closed_verified`, not raw pass counts.

### Search State

Owned by the search harness, not by the node document.

Tracks:

- active goal set;
- runs, branches, and candidate labels;
- legal actions and action history;
- MCTS visit counts, priors, Q values, and virtual loss;
- branch budgets, failure signatures, and branch lifecycle.

Search state may point at node labels, but it must not decide mathematical
truth. An abandoned branch can own verified lemmas that remain reusable.

### Execution State

Owned by coordinator/runtime job files.

Tracks:

```text
starting | running | publishing | applied | apply_failed |
timed_out | crashed | orphaned
```

This is observability and recovery state. It should never be used as proof
truth. A job affects the library only by publishing an event that the
librarian admits.

### Event and Audit State

Owned by the librarian.

Tracks whether a proposed mutation was durably decided:

```text
applied | apply_failed | workspace_corruption
```

Events are the replay/audit log. They should be authoritative for replay, but
they should not be the primary human-facing mathematical object in Phase 2.
The primary object should be the node document; events record how state
changed.

### Projection State

Owned by the librarian projector/sync layer.

Kuzu stores the graph/index representation:

- node label and hashes;
- dependency edges and unresolved refs;
- reverse dependency indexes;
- run/branch/action records;
- derived dashboard read models.

Kuzu is rebuildable. If Kuzu disagrees with admitted node documents/events,
Kuzu is the object to repair or rebuild.

### Transition Ownership

The clean loop is:

```text
select legal action
-> dispatch job
-> job emits event
-> librarian admits or rejects event
-> projector updates content/projection state
-> verification/search state derives consequences
-> MCTS reward is backed up
```

Important transitions:

| Transition | Owner | Effect |
| --- | --- | --- |
| `node revised` | librarian | content hashes change; local/closure verification becomes stale |
| `verifier accepted` | librarian + verifier state reducer | local status becomes accepted; closure recomputed |
| `verifier rejected` | librarian + search reducer | local status becomes rejected; failure signature updates branch |
| `branch expanded` | search harness + librarian | new candidate/helper nodes admitted; branch owns labels |
| `candidate promoted` | promotion validator | canonical goal points to closed candidate; branch becomes promoted |
| `all goals closed` | goal reducer | run becomes done; no more goal-directed generation |
| `job crashed/timed out` | coordinator | execution failure only; no content mutation |

### State Invariants

- Codex output cannot mutate durable state until schema, hash, budget, and
  legality checks pass.
- Job status cannot make a node verified.
- Search status cannot make a node verified.
- Verification status cannot delete a verified lemma from an abandoned branch.
- `closed_verified` requires local acceptance, all dependencies closed, no
  unresolved refs, and current hashes.
- `run_done(goal_set)` is monotone until the goal set or a goal statement is
  revised.
- Promotion is monotone inside one run: once a candidate is promoted, the run
  should not oscillate among alternate candidates.
- Missing refs are indexed and block closure; they must not silently disappear
  from dependency hashes.

### Phase I Risks To Retire

Current Phase I behavior is intentionally simple, but these parts should not
become permanent architecture:

- generator selection is label-ordered over `pass_count=-1`, so it has no
  concept of branch diversity or goal value;
- verifier selection is essentially leaf/low-pass-first under a strict
  dependency-ahead rule, so it cannot choose speculative high-value checks
  unless the dependency closure is already ready;
- `repair_count` is advisory text for the generator prompt, not a hard branch
  budget;
- generator repair receives the previous proof and verifier report, which
  biases it toward local edits of one route;
- `generator.batch_committed` replaces the target node for a label, so
  competing proofs of the same statement cannot coexist as first-class
  candidates;
- the stop condition is workspace-wide unfinished count, not an explicit goal
  set;
- missing refs are allowed into content, but without a first-class unresolved
  ref index they are easy to confuse with absent dependency edges;
- `publishing` job reconciliation is correct as runtime recovery, but a stuck
  publish must only affect execution/degraded status, never mathematical truth.

Phase 2 should preserve Phase I's useful safety properties: hash-match
admission, replay determinism, Kuzu-free workers, and terminal
`apply_failed`. The replacement is at the search/state layer, not at the
event-integrity layer.

## Graph Model

The global substrate is an AND/OR hypergraph, not a plain tree.

- OR choices: alternate strategies, branch candidates, repair vs sibling
  expansion, different helper decompositions.
- AND requirements: all dependencies/subgoals needed for a candidate proof to
  be closed verified.
- Hyperedges: one branch expansion can introduce several helper nodes that all
  support one parent candidate.
- Shared nodes: verified lemmas are reused across branches and goals.

Kuzu should store the reusable graph. MCTS should receive a goal-local
hypertree projection for search control.

## Runtime Loop

One coordinator tick should conceptually do:

```text
1. Apply/reconcile pending events through librarian.
2. Rebuild stale node/Kuzu/search indexes if needed.
3. Load active SearchRun state.
4. If all required goals are closed_verified:
     mark run done; stop goal-directed work.
5. Recompute legal verifier/generator/search actions.
6. Run deterministic priority for obviously ready verification work.
7. If search choice is needed:
     build Codex context packet;
     call selector policy/value agent;
     validate JSON and legality;
     update MCTS priors/values.
8. Dispatch verifier/generator jobs within budgets.
9. Ingest outcomes, compute rewards, backup MCTS values.
10. Apply circuit breakers and branch status transitions.
11. Publish dashboard state.
```

The loop should bias toward verification when ready verification can unlock
active goals. Generation happens when the active frontier needs new proof
content.

## MCTS Harness

Rethlas owns:

- tree/hypergraph projection;
- legal action set;
- `N(s)`, `N(s,a)`, `Q(s,a)`;
- UCT/PUCT selection;
- virtual loss for concurrent dispatch;
- reward backup;
- branch/run budgets;
- no-progress circuit breakers.

Codex supplies:

- `prior(s,a)`;
- value estimates;
- strategy explanation;
- action recommendations;
- branch-closing recommendations.

MCTS should choose among legal actions, not mutate nodes directly. The action
is only a proposal until it passes deterministic validation and, if it changes
content, lands through an admitted event. This keeps search adaptive without
giving the policy model authority over replayable truth.

Recommended context packet:

```json
{
  "context_hash": "sha256:...",
  "run": {"run_id": "...", "goal_set": "main", "budgets_remaining": {}},
  "goals": [{"label": "thm:...", "closure_status": "blocked"}],
  "frontier": [
    {
      "branch_id": "b002",
      "status": "active",
      "strategy": "...",
      "candidate_label": "thm:...",
      "known_gaps": [],
      "legal_actions": ["verify", "expand", "spawn_sibling"]
    }
  ],
  "retrieved_nodes": [],
  "failure_signatures": [],
  "constraints": []
}
```

Codex output:

```json
{
  "context_hash": "sha256:...",
  "recommended_actions": [
    {
      "action": "expand",
      "branch_id": "b002",
      "prior": 0.42,
      "value": 0.31,
      "strategy": "...",
      "reason": "..."
    }
  ],
  "state_recommendations": []
}
```

The harness may ignore any recommendation that fails schema, hash, budget,
status, or legality checks.

## Search Dashboard

Yes: Phase 2 dashboard should represent search. Without this, MCTS becomes a
black box and operators will only see the old symptoms: jobs running,
`pass_count` changing, and occasional verifier reports.

The dashboard should not show a literal tree as the only truth. The durable
structure is a graph/hypergraph; the useful UI is a goal-local tree or
hypertree projection over that graph.

Recommended views:

- **Goal board**: active goal set, each goal's `closure_status`, blocking
  dependencies, winning/promoted candidate, and `run_done` reason.
- **Branch frontier**: one row per active branch with `branch_status`,
  strategy, candidate label, owned helper labels, last action, next legal
  actions, budget remaining, failure signatures, and whether the branch is
  cooling down, stuck, exhausted, abandoned, or promoted.
- **MCTS table**: `N(s,a)`, `Q(s,a)`, prior, current UCT/PUCT score, selected
  action, and short selector rationale. This answers "why did it expand this
  branch instead of that one?"
- **AND/OR graph view**: OR siblings for alternate candidates/strategies; AND
  dependency groups for helper lemmas that must all close; shared verified
  lemmas shown once and reused by multiple branches.
- **Verification queue**: ready nodes sorted by goal impact, unlock count,
  branch score, cost, and age. This should make it visible when the scheduler
  chooses verification before more generation.
- **Search timeline**: `search.action_selected`, generator batches, verifier
  verdicts, branch status changes, and promotions, grouped by run/branch.
- **Node detail**: proof text, verifier reports, unresolved refs, dependency
  closure, branch provenance, and whether this node survives branch abandon.
- **Review detail**: referee verdict, issue severity, evidence source spans,
  citation checks, extraction-quality warnings, counterexample attempts, and
  whether repairs validate the original proof or only suggest a revision.

Interactive expansion should be lazy and goal-centered:

- clicking a goal expands its current proof-search projection;
- clicking a branch expands its candidate node, owned helper nodes, legal next
  actions, and latest verifier/generator outcomes;
- clicking a node expands dependency closure, dependents, verifier reports, and
  branch provenance;
- collapsed nodes should still show badges for active jobs, blocked deps,
  verifier gaps, and unread updates.

The dashboard should also auto-expand active work, but with guardrails:

- when a generator/verifier job starts, reveal the path
  `goal -> branch -> candidate/helper -> job target`;
- when MCTS selects an action, reveal that branch and action rationale;
- when a verifier returns a gap/critical report, reveal the affected node and
  the report summary;
- when a candidate is promoted, reveal the winning branch and goal closure;
- respect user-collapsed or pinned sections unless the event is critical;
- cap automatic expansion per tick so the UI does not explode during a busy
  run.

Expansion state is UI state, not mathematical/search truth. It should live in
the browser or dashboard read model, never in node verification state.

Suggested lazy-load endpoints:

```text
GET /api/search/run/{run_id}/projection?root=goal_label&depth=1
GET /api/search/branch/{branch_id}/projection?depth=1
GET /api/search/node/{label}/neighbors?mode=deps|dependents|branch|andor
GET /api/search/updates?since=...
```

For shared verified lemmas, the UI should render one canonical node with
multiple incoming references, or render aliases that visibly point to the same
canonical label. It should not imply duplicated independent subproofs.

### Web Implementation Plan

Current Phase I dashboard is a single vanilla `index.html` that polls several
JSON endpoints every five seconds. The backend already has `/events/stream` and
`StateWatcher`, but the frontend does not consume SSE yet. Phase 2 should reuse
the existing lightweight server model, but stop doing full-page/full-table
refreshes for search state.

Recommended implementation layers:

```text
dashboard/server.py
  search read-model endpoints backed by Kuzu/query server

dashboard/state_watcher.py
  SSE envelopes for search actions, branch status, verifier outcomes, jobs

dashboard/templates/index.html
dashboard/templates/static/
  api.js            small fetch/EventSource helpers
  store.js          browser-side UI/read-model cache
  search_view.js    goal board, branch frontier, expandable projection
  node_detail.js    existing node detail moved out of index.html
  graph_view.js     later Cytoscape/Dagre/ELK integration
```

Avoid introducing a build step at first. The dashboard is an operational tool
that should keep working inside `rethlas supervise`, offline if possible. Use
plain ES modules and vendor any graph library later under
`dashboard/templates/static/`.

Frontend state should be explicit:

```js
{
  runsById,
  branchesById,
  nodesByLabel,
  projectionsByKey,
  selected: { runId, goalLabel, branchId, nodeLabel },
  expandedKeys,
  userCollapsedKeys,
  pinnedKeys,
  unreadByKey,
  lastSseId,
}
```

Only `expandedKeys`, `userCollapsedKeys`, `pinnedKeys`, and `selected` are UI
state. They may live in memory or `sessionStorage`; they must not be written to
Kuzu, node frontmatter, or events.

Initial load:

1. Fetch `/api/overview` for runtime health.
2. Fetch `/api/search/runs` for active/completed run summaries.
3. Fetch `/api/search/run/{run_id}` for the selected run's goals.
4. Fetch frontier rows with `/api/search/run/{run_id}/frontier`.
5. Connect `EventSource("/events/stream")`.

Click expansion:

```text
goal click
  -> GET /api/search/run/{run_id}/projection?root=goal&depth=1

branch click
  -> GET /api/search/branch/{branch_id}/projection?depth=1

node click
  -> GET /api/search/node/{label}/neighbors?mode=deps|dependents|branch|andor
  -> optionally GET /api/node/{label} for proof/report detail
```

Every projection response should include:

```json
{
  "snapshot_id": "sha256:...",
  "root": "...",
  "depth": 1,
  "nodes": [],
  "edges": [],
  "and_groups": [],
  "truncated": false,
  "has_more": true
}
```

Auto-expansion should be driven by reveal requests derived from SSE:

```text
job_change(target, run_id, branch_id)
  -> reveal goal -> branch -> target

search.action_selected(run_id, branch_id, action_id)
  -> reveal branch and action rationale

verifier.run_completed(verdict=gap|critical)
  -> reveal target node and verifier report

search.candidate_promoted
  -> reveal winning branch and goal closure
```

The reveal function should respect user intent:

```js
revealPath(path, { reason, critical = false }) {
  for (const key of path) {
    if (!critical && userCollapsedKeys.has(key)) continue;
    expandedKeys.add(key);
  }
}
```

Add budgets:

- max auto-expanded paths per SSE batch;
- max visible graph nodes before collapsing low-priority subtrees;
- max depth per lazy-load request;
- abort stale fetches when the user selects a different run/goal.

Backend search events and job records should carry enough context for reveal:

```yaml
run_id: run_...
branch_id: b002
action_id: a017
target_label: lem:...
goal_label: thm:...
```

Without these fields, the frontend only sees a job target label and has to
guess which branch/goal path to reveal.

Rendering order:

1. Expandable tables first: goals, frontier, MCTS actions, verifier queue.
2. Nested outline projection next.
3. Interactive graph after the API/read model is stable.

Graph view should consume the same projection JSON as the table view. That
keeps the graph optional: if rendering fails or the library is huge, the table
and outline remain usable.

Initial dashboard should remain read-only. Operator controls such as "pause
branch", "abandon branch", "promote candidate", or "mark needs_user" can come
later, and must emit admitted events rather than mutate state directly.

Suggested API surface:

```text
GET /api/search/runs
GET /api/search/run/{run_id}
GET /api/search/run/{run_id}/frontier
GET /api/search/run/{run_id}/graph
GET /api/search/branch/{branch_id}
GET /api/search/goal/{label}
GET /api/search/actions?run_id=...
```

The existing Phase I dashboard panels are still useful, but they become the
runtime/health layer underneath the search dashboard.

## Verification Scheduling

Do not use only leaf-first.

Use two verification notions:

- **local verification**: this node passed its kind-specific verifier
  obligation against cited dependency statements and provenance/source context;
- **closed verification**: this node and every dependency in its transitive
  closure are verified.

All node kinds require verification. For `definition`, local verification
means well-formedness, non-circularity, notation/type consistency, and usable
dependency context. For `external_theorem`, it means citation/source validity
and exact statement/hypothesis match. For `lemma`, `proposition`, and
`theorem`, it means the proof establishes the statement under cited
dependencies.

Scheduling priority:

```text
goal_closure_bonus
+ unlock_count_bonus
+ branch_score_bonus
+ cheap_ready_bonus
+ age_bonus
- repeated_failure_penalty
- non_goal_penalty
```

Recommended behavior:

- verify ready nodes in active goal closures first;
- allow speculative local verification of high-level candidates to fail fast;
- compute closed verification bottom-up through dependency closure;
- avoid library-wide verification unless `library_sweep` is explicitly enabled.

## Event and State Strategy

Keep events as journal/audit, not as the human-facing mathematical object.

New event families likely needed:

```text
search.run_opened
search.run_closed
search.branch_opened
search.branch_scored
search.branch_status_changed
search.action_selected
search.candidate_promoted
node.metadata_updated
```

Rules:

- every event must be replayable;
- event payloads should reference stable labels and hashes;
- Codex recommendations are not truth until admitted;
- derived MCTS statistics can be stored in Kuzu/runtime snapshots, with
  checkpoint events only when useful for replay/debug.

Open point: whether full MCTS visit/Q state should be event-sourced or rebuilt
from compact search events. Initial recommendation: persist snapshots plus
event journal; do not emit an event for every visit-count update.

Phase I compatibility note:

- keep `pass_count` temporarily as a derived/compatibility field;
- introduce explicit `local_status` and `closure_status` beside it;
- keep job files as runtime observability only;
- teach the scheduler to read the explicit statuses first, falling back to
  `pass_count` only for old workspaces;
- remove `pass_count` from stop-condition logic once goal sets and closure
  status are stable.

This avoids a risky rewrite while preventing new Phase 2 behavior from
depending on the old overloaded state.

## Context Packing

This is the main harness product.

Context packets should be:

- small enough for cheap Codex calls;
- hash-addressed for replay/debug;
- explicit about legal actions;
- explicit about constraints and budgets;
- rich enough to include relevant verified lemmas and failure signatures;
- stable across reruns when the underlying state is unchanged.

Avoid giving Codex raw workspace dumps. Give it curated mathematical state and
a strict task.

## Deterministic Validators

Every agent output needs a validator:

- schema validation;
- referenced labels exist or are intentionally new;
- action is legal in current branch/run state;
- input `context_hash` still matches;
- proposed labels are unique and prefix-valid;
- candidate promotion satisfies deterministic preconditions;
- budgets are not exceeded.

Validator failure should not be a silent retry loop. It should produce a
failure signature and eventually mark the branch `needs_user` or call the
failure analyst.

## Implementation Slices

Recommended order:

0. **State-domain compatibility layer**
   - define content, verification, search, execution, event, and projection
     state records;
   - map old `pass_count`/`repair_count` into explicit statuses;
   - make the scheduler consume the new status API;
   - keep old fields writable until migration is complete.

1. **Node docs and recursive layout**
   - parse/render Markdown+YAML;
   - canonical `nodes/<topic_path>/<kind_prefix>_<slug>.md`;
   - recursive scan for compatibility and staged imports;
   - label identity independent of path.

2. **Hashing and invalidation**
   - statement/proof/dependency/verification/closure hashes;
   - reverse dependency invalidation;
   - unresolved ref tracking.

3. **Goal set and closure status**
   - explicit goals;
   - local vs closed verification;
   - `run_done`.

4. **Search graph schema**
   - run/branch/action records;
   - branch-owned labels;
   - Kuzu indexes and dashboard read model.

5. **MCTS harness without Codex**
   - deterministic legal actions;
   - simple hand-coded priors;
   - budgets and loop guards;
   - fake verifier/generator tests.

6. **Codex policy/value agent**
   - context packet;
   - strict JSON output;
   - validator;
   - plug into MCTS priors.

7. **Branch generator and verifier integration**
   - branch-aware prompts;
   - local verification;
   - failure signatures.

8. **Promotion flow**
   - compatibility checker;
   - monotone promotion;
   - stop run on closed goal.

9. **Dashboard**
   - add search read-model endpoints before complex visualization;
   - show goal status, branch frontier, and verification queue as tables first;
   - add MCTS stats and selected-action rationale;
   - add goal-local AND/OR graph or hypertree projection;
   - surface stuck/budget states and verified reusable lemmas;
   - keep the first version read-only.

## Critical Tests

Add tests before broad Codex integration:

- replay from events yields same nodes/Kuzu/search state;
- metadata edits do not invalidate proof hashes;
- statement edits invalidate downstream verification inputs;
- missing refs block closure but do not vanish from indexes;
- no goal set means no autonomous proof search;
- all goals closed means no further goal-directed generation;
- branch repair budget marks branch stuck;
- duplicate selector action is rejected;
- stale publishing job unblocks with degraded alert;
- promoted candidate cannot oscillate within one run;
- Codex invalid JSON cannot mutate state;
- MCTS legality filtering prevents selecting exhausted branches;
- verified lemma from abandoned branch remains reusable.
- job status changes alone cannot change verification status;
- search status changes alone cannot change verification status;
- `run_done` remains true across scheduler ticks until a goal or goal set
  revision changes the closure input hash.
- dashboard search read model matches Kuzu run/branch/action state;
- dashboard graph view shows shared verified lemmas once, not duplicated as
  unrelated tree leaves.

## Locked Decisions

- Live search run/branch state lives in Kuzu plus the event journal. Node
  frontmatter only stores stable per-node metadata and branch pointers/hints for
  materialized branch/helper nodes, not live visit counts or scheduler counters.
- MCTS state is snapshotted, not fully event-sourced. Emit semantic events for
  branch open/close, action selection, failure, and promotion; take periodic
  snapshots for visit counts, priors, Q values, and virtual loss.
- The default Codex context packet is goal-local and compact: run summary,
  active goals, frontier, retrieved nodes, failure signatures, budgets, and a
  projection snapshot hash. If it does not fit in a small message, it is too
  large.
- Local verification of a top-level candidate is allowed before all
  dependencies are closed, but only closed verification can satisfy
  `run_done` or promotion to the canonical goal.
- Promotion compatibility is exact statement equivalence after canonical
  normalization / alpha-equivalence. A strictly stronger theorem becomes a
  separate node or derived goal; it is not silently promoted over the target.
- Retrieval order is deterministic structural search first (Kuzu labels,
  tags, dependency graph, goal neighborhoods), then BM25 text search, then
  embeddings only as an optional later layer.
- Manual Markdown editing is allowed only on node documents and only through
  librarian validation/canonicalization. Generated indices, runtime state, and
  event files are not manually edited.
- `statement_hash` / `proof_hash` / `closure_hash` depend on mathematical
  content and dependency statements, not on topic moves, tags, job ids, or
  heartbeat timestamps. Those metadata fields live in `metadata_hash` and
  read-model projections instead.
