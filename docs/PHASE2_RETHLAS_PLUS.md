# Phase 2: Node-first Theorem Library and Search

Date: 2026-05-02

This is a proposed Rethlas-plus Phase 2 direction. It excludes the blockchain,
token, and decentralized artifact-cache topics, which are parked in
`docs/PHASE5.md`.

## Goal

Turn Rethlas from a single proof-slot repair workflow into a theorem/lemma
library with explicit search branches.

The center of the system should be:

```text
knowledge_base/nodes/<topic_path>/<kind_prefix>_<slug>.md
```

Each file is a node document: one theorem, lemma, definition, proposition,
candidate theorem, or branch helper. Kuzu is the graph/index representation of
the same library. Events are a journal and audit trail.

## Diagnosis

The current workflow is too path-dependent:

- `pass_count = -1` sends a node to the generator pool.
- A failed proof increments `repair_count`.
- The next generator prompt receives the previous proof, verifier report, and
  repair hint.
- This biases the generator toward local repair of the same proof direction.
- The coordinator prevents concurrent same-label work.
- `generator.batch_committed` replaces the target label's current proof.

So the system cannot safely race multiple materially different proofs of the
same statement inside one label.

## First-class object

Rethlas-plus should be node-first:

```text
NodeDocument = one md file with YAML frontmatter and Markdown body
```

Recommended layers:

```text
nodes/<topic_path>/**/*.md  current theorem/lemma/definition library
dag.kz / Kuzu               graph/index representation and scheduler index
events/                     append-only journal / audit / replay record
runtime/                    temporary process state
```

The mathematical object exposed to users and agents is the node document, not
a JSON event.

## Authority and Sync Model

Do not keep node documents, events, and Kuzu as competing truth stores.

Phase 2 should define their roles this way:

- `nodes/<topic_path>/**/*.md` is the current human/agent-facing library
  snapshot.
- `events/` is the append-only journal of admitted mutations and conflicts.
- Kuzu is the rebuildable graph/index and scheduler read model.
- `runtime/` is temporary process state and never mathematical truth.

The librarian is the only synchronizer. A manual Markdown edit is not silently
trusted; it is parsed as a proposed node revision, validated, admitted, and
then reflected into the journal and Kuzu. A generator/verifier/search result
follows the same path. If Kuzu disagrees with admitted node state, rebuild
Kuzu. If a node file is stale relative to admitted state, normalize or
re-render it through the librarian.

Migration recommendation:

1. Keep Phase I events as the replay root during the transition.
2. Render/normalize node documents as the primary working surface.
3. Add snapshot hashes so librarian can detect drift between node docs,
   events, and Kuzu.
4. After node-doc canonicalization is stable, allow checkpointed node-library
   snapshots plus replay of later events.

## Node Document Format

Keep metadata and mathematical content together in one file. Do not split
`meta.yaml` from `proof.md`.

Example:

```markdown
---
label: lem:induced_orbit_slice_bridge
kind: lemma
topic_path: lie_theory/nilpotent_orbits
tags:
  - real_groups
  - nilpotent_orbits
library_role: helper

verification:
  pass_count: 3
  status: verified
  closure_status: closed_verified
  statement_hash: sha256:...
  proof_hash: sha256:...
  dependency_statement_root: sha256:...
  verification_input_hash: sha256:...
  verification_hash: sha256:...
  closure_hash: sha256:...

goal:
  goal_sets:
    - main
  required: true

search:
  root_target: thm:induced_orbit_toy_problem
  branch_id: b002
  branch_status: promoted
  strategy: slice_model_open_dense_reduction

provenance:
  source_spans:
    - span:hartshorne_ch2:section_2_4:p17_l12_l28
  source_artifacts:
    - src:hartshorne_ch2:pdf
    - src:hartshorne_ch2:tex
  extraction_agent: learner
  extraction_run: learn_2026_05_03_hartshorne_ch2
  source_confidence: medium

depends_on:
  - def:induced_orbits_from_x0_slice_closure
  - lem:maximal_orbits_vs_open_subsets
---

**Statement.**

...

**Proof.**

...

**Notes.**

...
```

Stable frontmatter fields:

- `label`
- `kind`
- `topic_path`
- `tags`
- `library_role`
- `verification`
- `goal`
- `search`
- `provenance`
- `depends_on`

Ephemeral runtime state stays out of frontmatter:

- process ids;
- active job ids;
- heartbeat timestamps;
- temporary Codex logs.

Phase 2 must define the provenance fields even before Phase 3 implements
source ingestion. Learner-created nodes need stable places to point back to
source spans and source artifacts. Source provenance is metadata: adding or
moving source references should not change `statement_hash`, `proof_hash`,
`verification_input_hash`, or `closure_hash` unless the mathematical statement,
proof text, or dependencies changed.

Phase 2 should accept both PDF and TeX provenance ids:

```yaml
provenance:
  source_spans:
    - span:paper_x:v1:pdf:p05_b12
    - span:paper_x:v1:tex:thm-main
  source_artifacts:
    - src:paper_x:v1:pdf
    - src:paper_x:v1:tex
  extraction_agent: learner
```

The existence and hash validity of those source spans is a Phase 3 concern,
but the node schema and Kuzu index should already allow them.

## Topic Organization

The knowledge base should be organized by subdirectories. This is not merely
allowed; it is the default canonical layout:

```text
knowledge_base/nodes/
  _index.md
  lie_theory/
    nilpotent_orbits/
      real_groups/
        induction/
          thm_induced_orbit_toy_problem.md
          lem_induced_orbit_slice_bridge.md
      complex_groups/
        orbit_closures/
          prop_richardson_orbit_closure.md
  algebraic_geometry/
    schemes/
      morphisms/
        def_proper_morphism.md
  analysis/
    convexity/
      inequalities/
        lem_vandermonde_bound.md
```

Rules:

- `label` is the unique identity.
- Node files live under `knowledge_base/nodes/<topic_path>/`.
- `topic_path` is a mathematical topic taxonomy, not a source, run, agent, or
  timestamp taxonomy.
- Multiple directory levels are allowed and expected.
- The root of `knowledge_base/nodes/` is reserved for indexes or library-level
  notes; ordinary theorem/lemma/definition nodes should not live there.
- Path is not identity.
- `\ref{...}` always points to labels, not paths.
- `topic_path` determines the rendered path.
- Choose the most useful mathematical home for repeated retrieval and human
  browsing, for example `lie_theory/nilpotent_orbits/real_groups/induction`.
- Moving a node between topics is metadata-only.
- Moving a node must not change statement/proof hashes.
- Prefer one primary `topic_path` plus multiple `tags`; do not duplicate one
  node into multiple topic folders.
- Source-specific organization belongs in `sources/` and `provenance`, not in
  canonical `topic_path`.
- Project/run/agent-specific organization belongs in `search`, `goal`, or
  runtime state, not in canonical `topic_path`.
- Import/merge may stage nodes outside the canonical tree temporarily, but
  librarian admission must place admitted nodes under their canonical
  `topic_path`.
- If a node has no good topic yet, use a controlled staging topic such as
  `unclassified/<source_or_run_id>/`, not the node root.

Recommended filename convention:

```text
knowledge_base/nodes/<topic_path>/<kind_prefix>_<slug>.md
```

where `kind_prefix` follows the label prefix (`def`, `lem`, `prop`, `thm`,
`ex`, `rem`, etc.) and `slug` is a stable filesystem-safe rendering of the
label slug. Filename changes caused by topic moves are metadata-only.

## Kuzu Role

Kuzu is the knowledge base's graph/index representation. It should index:

- labels;
- kind;
- topic path;
- rendered file path;
- tags;
- library role;
- goal sets;
- verification status and hashes;
- reverse dependency indexes for invalidation;
- dependency edges;
- search branch metadata;
- branch-owned labels.

Kuzu is not the editing interface. It should be rebuildable from node
documents and/or events.

## Merging Knowledge Bases

Two Rethlas knowledge bases can be combined, but the merge target should be
the node/source library, not the Kuzu database file.

Basic KB merge belongs in Phase 2 because search needs reusable imported
nodes, aliases, proof variants, and source provenance before formalization is
available. Phase 4 later adds formalization-aware merge for Lean/Coq/Isabelle
artifacts and pinned toolchains.

Recommended rule:

```text
merge Markdown/YAML node docs + source artifacts + manifests
rebuild Kuzu from admitted merged state
do not merge dag.kz files directly
```

There should be two merge modes.

### Federated Index Mode

Federated index mode keeps another KB external but queryable:

```text
local Kuzu indexes remote manifest metadata
remote nodes are read-only candidates
no local truth mutation happens
```

Use this when the operator wants retrieval help from another library without
trusting or importing its nodes. Remote nodes may be used as premises only
after explicit import or citation as external source.

### Deep Import Mode

Deep import mode admits selected remote nodes into the local library:

```text
remote manifest -> merge plan -> librarian admission -> local node docs/events -> Kuzu rebuild
```

The local librarian must validate every admitted node. Foreign events should
not be replayed directly as local truth. They are audit evidence attached to
the import record. The local event journal should contain local import/admit
events that record the remote origin.

### Two-step Merge and Dedup

Merge should be a two-step operation:

```text
Step 1: staged import / federated merge
Step 2: dedup, canonicalization, and promotion
```

Step 1 should not destructively write remote nodes into canonical local labels.
It should import or index them in an isolated state:

```text
remote KB -> staged namespace / federated index
          -> source/manifest/hash validation
          -> dependency remapping candidates
          -> no canonical overwrite
```

Step 2 compares staged nodes against the local library and decides:

```text
same theorem      -> alias or no-op
same statement + different proof -> proof variant
near duplicate    -> curator/referee review
different theorem -> import under canonical topic path
conflict          -> require user or namespace
```

This avoids polluting the main dependency graph before duplicate detection.
It also lets search use remote candidates read-only while the merge plan is
still unresolved.

Recommended states:

```text
federated          remote node is indexed but external
staged             copied into local staging namespace, not canonical truth
dedup_pending      candidate needs duplicate/canonicalization review
alias_pending      same statement likely; waiting for alias decision
proof_variant      same statement, different useful proof
canonical_imported admitted as a local canonical node
skipped            intentionally not imported
requires_user      conflict or ambiguous mapping
```

Staging paths should not live in the canonical topic tree:

```text
knowledge_base/imports/<library_id>/<snapshot_id>/nodes/...
```

After dedup/canonicalization, admitted nodes are rendered under:

```text
knowledge_base/nodes/<topic_path>/<kind_prefix>_<slug>.md
```

The dedup pass should use several levels, in order:

1. exact `statement_hash`;
2. normalized statement equivalence after label/namespace remapping;
3. dependency-aware equivalence;
4. semantic duplicate candidate suggested by curator/referee;
5. user decision for ambiguous cases.

Only exact or verified-equivalent matches should create aliases automatically.
Near duplicates should become review items, not silent merges.

### Snapshot Manifest

Every exportable KB should have a manifest:

```yaml
schema: rethlas-kb-snapshot-v1
library_id: kb:lie_theory_lab
snapshot_id: sha256:...
created_at: 2026-05-03T...
hash_profile: rethlas-hash-v2
verifier_profile: informal-verifier-v1
node_root: sha256:...
source_root: sha256:...
event_checkpoint: sha256:...
nodes:
  - label: thm:...
    path: knowledge_base/nodes/...
    kind: theorem
    statement_hash: sha256:...
    proof_hash: sha256:...
    closure_hash: sha256:...
sources:
  - source_id: src:...
    artifact_hash: sha256:...
```

The manifest is the object that is imported, indexed, notarized, or shared.
Kuzu can cache it, but Kuzu is not the manifest.

### Merge Plan

Import should be a planned operation, not an automatic overwrite. The librarian
should generate a merge plan with explicit decisions:

```yaml
schema: rethlas-merge-plan-v1
source_library_id: kb:remote
source_snapshot_id: sha256:...
target_library_id: kb:local
mode: federated_index | deep_import
label_map:
  remote:thm:main: thm:main_remote_v1
aliases:
  thm:local_main:
    - remote:thm:main
conflicts:
  - type: same_label_different_statement
    local_label: thm:foo
    remote_label: thm:foo
actions:
  - import_node
  - add_alias
  - keep_as_proof_variant
  - skip
  - require_user
```

The plan can be suggested by a curator agent, but applying it is deterministic
librarian work.

### Conflict Classes

The merge layer should distinguish:

- `same_label_same_statement`: idempotent or alias-only import.
- `same_label_different_statement`: hard conflict; require namespace or manual
  mapping.
- `different_label_same_statement`: possible duplicate; add alias or keep as
  alternate proof.
- `same_statement_different_proof`: preserve both proofs; do not discard a
  verified route.
- `dependency_mapping_conflict`: remote dependency labels cannot be mapped
  safely.
- `hash_profile_mismatch`: recanonicalize before trusting hashes.
- `verifier_profile_mismatch`: keep remote verdict as evidence, but local
  verification must be rerun before `closed_verified`.
- `source_artifact_conflict`: same source id but different artifact hash;
  version or namespace the source.
- `cycle_after_mapping`: reject or require manual remapping.

### Alternate Proofs

Merging must not throw away already verified theorems or proofs.

If two nodes have the same normalized statement but different proofs, keep the
imported proof as either:

- an alternate proof variant linked to the canonical node; or
- a namespaced theorem node with `same_statement_as` metadata.

Only one label needs to be the canonical reference target. Other verified
proofs remain useful as branch evidence, redundancy, and future formalization
material.

Suggested metadata:

```yaml
provenance:
  imported_from:
    library_id: kb:remote
    snapshot_id: sha256:...
    original_label: thm:...
    original_statement_hash: sha256:...
    original_closure_hash: sha256:...

aliases:
  - remote:thm:...

same_statement_as: thm:canonical_label
proof_variant_of: thm:canonical_label
```

### Source Artifact Merge

Source artifacts should be content-addressed:

- identical PDF/TeX/OCR/layout artifacts are deduplicated by hash;
- same `source_id` with different hash becomes a versioned source;
- source spans keep their original locator and text hash;
- imported node provenance points to remapped local source span ids.

### Verification Portability

Imported verification status is not automatically local verification.

Rules:

- If hash profile, verifier profile, dependency statement hashes, and closure
  hashes match local policy, an imported closed verification can be accepted as
  portable evidence.
- If any profile differs, import the node and verdict evidence, but mark local
  `verification.status` as stale or unverified until local verifier reruns.
- Formal Phase IV/Lean attestations, when present, are stronger portability
  evidence but still depend on pinned toolchain/library hashes.

## Events Role

Events should be treated as journal entries, not as the main user-facing
object.

Useful events:

- `user.node_added`
- `user.node_revised`
- `user.node_metadata_updated`
- `generator.batch_committed`
- `verifier.run_completed`
- `search.branch_opened`
- `search.branch_scored`
- `search.branch_closed`
- `search.candidate_promoted`

Events remain valuable for:

- audit history;
- replay/debug;
- timestamped provenance;
- conflict records.

## Librarian Responsibilities

The librarian synchronizes and validates:

```text
nodes/<topic_path>/**/*.md <-> Kuzu graph/index <-> events journal
```

Responsibilities:

1. Parse node markdown/frontmatter.
2. Validate unique labels, kind-prefix rules, refs, topic paths, and schema.
3. Compute `depends_on`, `statement_hash`, `proof_hash`,
   `dependency_statement_root`, `verification_input_hash`,
   `verification_hash`, and `closure_hash`.
4. Update Kuzu.
5. Normalize derived fields back into frontmatter.
6. Append or consume journal events.
7. Reconcile on startup.

Reconciliation policy:

- If Kuzu is missing or stale, rebuild it from nodes and/or events.
- If a rendered node file is stale, normalize it.
- If a node file was manually edited, ingest it through librarian checks.
- If an orphan file is present, surface it clearly instead of silently treating
  it as verified knowledge.

## Hashing and Invalidation

Phase 2 needs hashes for two separate reasons:

1. decide whether a verifier result is still valid;
2. decide which downstream nodes become stale when a statement changes.

Do not use one monolithic node hash for everything. Topic moves and tag edits
should not reset mathematical verification.

Recommended hash fields:

| Field | Input | Use |
| --- | --- | --- |
| `statement_hash` | normalized statement block, mathematical assumptions, and kind | compare statements; detect dependency statement changes |
| `proof_hash` | normalized proof block plus cited labels | detect local proof changes |
| `dependency_statement_root` | sorted direct dependency labels and their `statement_hash` values | detect when a cited theorem/lemma changed meaning |
| `verification_input_hash` | label, kind, `statement_hash`, `proof_hash`, `dependency_statement_root`, verifier profile, prompt/schema version | decide whether a stored verifier verdict is still applicable |
| `verification_hash` | `verification_input_hash` plus verdict, report digest, and verifier identity/profile | decide whether a local accepted verdict is the same verdict object |
| `closure_hash` | local `verification_hash` plus sorted direct dependency `closure_hash` values | prove that the full dependency closure was verified |
| `metadata_hash` | topic path, tags, library role, goal/search metadata | detect library organization changes without invalidating proof verification |

Canonicalization should be explicit:

- parse YAML frontmatter rather than hashing raw bytes;
- normalize Markdown section boundaries and line endings;
- sort lists that are mathematically unordered, such as `tags`;
- preserve ordered text where order matters, such as proof paragraphs;
- exclude runtime state from every stable hash.
- keep wall-clock timestamps, event ids, job ids, and process ids out of
  `verification_hash` and `closure_hash`; store them as audit metadata.

Invalidation rules:

- Editing a node's statement invalidates its local verification and all
  downstream nodes whose `dependency_statement_root` changes.
- Editing a node's proof invalidates that node's local verification and
  `closure_hash`. Downstream local proofs do not need re-verification if the
  statement is unchanged, but their closure hashes must be recomputed.
- Editing topic path, tags, library role, goal metadata, or search metadata
  updates Kuzu/indexes but does not reset proof verification.
- Changing verifier prompt/schema/profile invalidates stored verdicts whose
  `verification_input_hash` used the old profile.
- If a dependency loses verified status, any goal depending on it becomes
  blocked even if its own local proof text has not changed.
- A dependency closure becoming stale should schedule dependency
  verification/recomputation first. It should not send the ancestor to the
  generator unless the ancestor's own proof or statement changed.
- Unresolved `\ref{...}` labels should be indexed explicitly, not silently
  dropped from Kuzu. They block closed verification until either the missing
  node is created or the reference is removed.

Distinguish local and closed verification:

- `verification.status = verified` means the node passed the verifier for its
  kind-specific local obligation under its cited dependency statements.
- `verification.closure_status = closed_verified` means the node is locally
  verified and every dependency in its transitive closure is also closed
  verified.

Goal completion must use `closure_status`, not merely local `status`.

All node kinds need verification. The obligation differs by kind:

| Kind | Local verification obligation |
| --- | --- |
| `definition` | Well-formedness, non-circularity, notation/type consistency, dependency existence, and conservative introduction relative to prior definitions |
| `external_theorem` | Source/citation validity, exact statement match, hypotheses/conventions recorded, and dependency/source provenance present |
| `lemma` / `proposition` / `theorem` | Statement/proof/dependency check; proof establishes the stated claim under cited dependency statements |
| generated bridge/helper | Same as lemma/proposition/theorem, plus provenance and branch/search linkage |

Definitions and external theorems may have empty proof text, but they are not
automatically verified. They should enter a ready verifier queue with a
kind-specific verifier profile. A definition can be `verified` only after the
verifier checks that it is a coherent and usable definition. An external theorem
can be `verified` only after the verifier checks that the cited source actually
supports the recorded statement and hypotheses.

## Goal Sets and Stop Conditions

Phase 2 should support one or more explicit goals. The system should not need
to expand or verify every node in the library before stopping.

Goals may be persistent node metadata:

```yaml
goal:
  goal_sets:
    - main
  required: true
```

or run configuration:

```toml
[goals.main]
labels = ["thm:induced_orbit_toy_problem", "thm:second_goal"]
```

Recommended semantics:

- A goal label names a canonical target node.
- A branch candidate may satisfy the goal only after promotion or after an
  explicit compatibility check proves it implies the canonical target.
- `goal_done(label)` is true when the canonical target, or its promoted
  compatible candidate, has `closure_status = closed_verified`.
- `run_done(goal_set)` is true when every required goal in the set is done.
- When `run_done` is true, the coordinator stops goal-directed generation and
  verification for that run.
- Verified non-goal nodes remain in the library.
- Unverified non-goal exploratory nodes may remain unexpanded; that is not a
  failure.

There can be a separate `library_sweep` mode that continues verifying
non-goal nodes after all goals are done, but it should be opt-in. The default
proof-search run should stop at the goal condition.

If no goal set is configured, autonomous proof search should not start by
default. The operator can either declare goals or explicitly run
`library_sweep`.

## Search Tree

The search tree should not become a separate knowledge base. It should be
expressed through ordinary node documents plus search metadata.

Example labels:

```text
thm:induced_orbit_toy_problem
thm:induced_orbit_toy_problem_b001_candidate
thm:induced_orbit_toy_problem_b002_candidate
lem:induced_orbit_toy_problem_b002_slice_bridge
lem:induced_orbit_toy_problem_b002_open_dense
```

Branch candidates and helpers are ordinary nodes. If verifier accepts them,
they enter the theorem/lemma library permanently.

Hard rule:

```text
Abandoning a branch never deletes verified theorem/lemma assets.
```

Abandoning means:

- stop allocating generator/verifier budget to unverified nodes in that branch;
- keep verified nodes as reusable library assets;
- record why the route was abandoned.

## Promotion

When a branch candidate verifies, it may be promoted to the canonical target.

Promotion can revise the canonical proof to cite the winning candidate:

```markdown
**Proof.** This follows from \ref{thm:target_b002_candidate}. \square
```

Other verified candidates remain as alternate theorems/proofs.

Promotion must check compatibility between the candidate and canonical target.
Open design point: exact statement equality vs stronger theorem implying the
target.

Promotion should be monotone within one goal-directed run:

- once a candidate is promoted and the goal is closed verified, the run is
  done;
- the selector must not automatically demote or replace the promoted candidate
  during the same run;
- replacing a promoted proof later requires an explicit new revision or a new
  goal/run version;
- alternate verified candidates remain library assets, not competing live
  canonical targets.

## Branch Expansion

Do not let repair mode be the only response to failure. A search branch should
be a fresh proof attempt with a different strategy unless the selector
explicitly chooses local repair.

Branch actions:

- direct proof;
- prove a stronger theorem;
- prove a bridge lemma;
- reduce to a known library theorem;
- split into subgoals;
- construct counterexample or boundary obstruction;
- change invariant or model;
- formalize a key definition.

Selector actions:

- `promote`
- `repair`
- `expand`
- `spawn_sibling`
- `abandon`
- `defer`

Selector output should be strict JSON:

```json
{
  "action": "expand",
  "parent_branch": "b002",
  "reason": "The verifier gap is local; the slice bridge is accepted, but orbit-closure maximality still needs a separate lemma.",
  "new_branches": [
    {
      "strategy": "Prove maximality using an open dense intersection criterion.",
      "candidate_label": "lem:induced_orbit_toy_problem_b004_open_dense_bridge",
      "avoid": ["signed diagram induction"]
    }
  ]
}
```

## External Proof-search Patterns

Other proof-search systems suggest that Rethlas-plus should not model search
as a plain tree only.

- Lean Aesop is explicitly tree-based: it applies rules to goals, creates
  subgoals, and explores the resulting search tree using a configurable
  strategy, defaulting to best-first. It also distinguishes safe and unsafe
  rules, prunes irrelevant branches, and enforces rule/depth limits.
  Reference: <https://reservoir.lean-lang.org/@leanprover-community/aesop>.
- HyperTree Proof Search (HTPS / Evariste) models proof search as a
  hypergraph: goals are nodes, tactics are hyperedges, and one tactic may
  create several subgoals that all have to be solved. A successful proof is a
  hypertree inside that hypergraph. It uses MCTS-inspired PUCT/RP selection and
  backs up values through all children of AND-style tactic edges. Reference:
  <https://arxiv.org/abs/2205.11491>.
- DeepSeek-Prover-V1.5 uses Monte-Carlo tree search with a
  truncate-and-resume abstraction in Lean 4. It stores successful proof prefixes
  as tree nodes, expands from selected nodes, can expand non-leaf nodes, uses
  proof assistant feedback as sparse reward, and adds intrinsic reward to
  encourage diverse states. Reference: <https://arxiv.org/abs/2408.08152>.
- LeanDojo/ReProver shows that premise retrieval is a separate bottleneck from
  search. The prover needs the right accessible lemmas before tactic/branch
  search is useful. Reference: <https://arxiv.org/abs/2306.15626>.
- Isabelle Sledgehammer and CoqHammer are closer to "premise selection +
  external solver portfolio + proof reconstruction" than to MCTS. They run
  multiple selected-fact/solver strategies and reconstruct accepted proofs
  inside the proof assistant. References:
  <https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.ITP.2025.26>
  and <https://link.springer.com/article/10.1007/s10817-018-9458-4>.

Design implication:

```text
MCTS should operate over OR choices.
The knowledge/search substrate should be an AND/OR dependency graph.
```

For Rethlas-plus:

- OR nodes: alternate strategies, candidate proofs, branch actions, different
  helper-lemma decompositions.
- AND nodes: a proof candidate's required dependencies/subgoals; all must be
  closed verified.
- Hyperedges: one branch action can introduce several helper nodes that must
  all be proved for the parent candidate to close.
- Shared graph nodes: verified lemmas can be reused by multiple branches and
  goals, so the global structure is not a tree.

So "tree search" is the controller view; "graph expansion" is the library
view. The implementation should store a graph in Kuzu and expose a tree or
hypertree projection to MCTS for one active goal set.

## Search Dashboard

The dashboard should be upgraded to reflect search. Otherwise MCTS will be
hard to trust: users will see workers and verifier reports, but not why the
system is spending budget on one branch instead of another.

Do not make the dashboard present a pure tree as the mathematical reality. The
real substrate is the node graph/hypergraph; the dashboard should expose a
goal-local tree/hypertree projection for search control.

Minimum useful panels:

- active goal set and per-goal closure status;
- branch frontier grouped by goal;
- branch strategy, candidate label, owned helper labels, status, and budget;
- legal next actions for each branch;
- MCTS `visits`, `prior`, `q_value`, current selection score, and selector
  rationale;
- verification queue with goal-impact and unlock-count reasons;
- AND/OR graph view showing alternate candidates, required dependencies, and
  shared verified lemmas;
- search timeline of selected actions, generator commits, verifier verdicts,
  branch status changes, and promotions.

Interactive behavior:

- A goal row should expand on click into a goal-local proof-search projection.
- A branch row should expand on click into its candidate, helper labels,
  verifier reports, failure signatures, legal actions, and MCTS stats.
- A node row should expand on click into dependencies, dependents, unresolved
  refs, branch provenance, and verification history.
- The UI should lazy-load deeper neighborhoods instead of sending the whole
  library graph on page load.
- Collapsed rows should still show badges for active jobs, blocked dependencies,
  unread verifier reports, and newly verified helper lemmas.

Auto-expansion behavior:

- Expand the path to the current generator/verifier job target.
- Expand the branch selected by the latest MCTS action.
- Expand nodes that just received `gap` or `critical` verifier reports.
- Expand the promoted candidate path when a goal closes.
- Do not repeatedly reopen a section the user explicitly collapsed unless the
  event is critical.
- Apply a visible-node and auto-expansion budget so busy runs remain readable.

The expanded/collapsed state is dashboard UI state only. It must not affect
search selection, verification status, or replay.

Web implementation:

- Keep the first Phase 2 version in the existing no-build dashboard stack.
- Split the current large `index.html` script into small static ES modules:
  `api.js`, `store.js`, `search_view.js`, `node_detail.js`, and later
  `graph_view.js`.
- Use the existing `/events/stream` SSE backend instead of only polling every
  five seconds.
- Maintain a small browser-side store for run summaries, branches, cached node
  details, projection snapshots, expanded keys, pinned keys, user-collapsed
  keys, unread badges, and current selection.
- Fetch graph neighborhoods lazily; do not send the whole library graph at page
  load.
- Make table/outline rendering consume the same projection JSON that the later
  graph view will consume.
- Vendor any graph library under `dashboard/templates/static/` later; avoid a
  build pipeline until the read model is stable.

SSE-driven reveal rules should require backend payloads to include `run_id`,
`branch_id`, `goal_label`, `target_label`, and optionally `action_id`. Without
those fields, the frontend cannot reliably reveal the active path.

Recommended rollout:

1. Add read-model API endpoints for runs, branches, actions, and goal-local
   graph snapshots.
2. Add lazy expansion endpoints for goals, branches, and node neighborhoods.
3. Render expandable tables first: goals, frontier, MCTS stats, verifier queue.
4. Add automatic reveal of active work.
5. Add an interactive graph after the read model is stable.
6. Keep the first version read-only.
7. Later add operator commands such as pause/abandon/promote, but only through
   admitted events.

## Search Policy

Introduce Monte Carlo Tree Search as the selector policy, but do not make MCTS
the owner of the whole system. The persistent truth state remains the node
library plus Kuzu; MCTS owns only the search-control problem:

```text
given the active goal set and current branch frontier, choose the next
branch/action to spend generator or verifier budget on.
```

Proof expansions are expensive and rewards are sparse, so rollout should be
shallow and verifier-shaped. This is closer to MCTS-guided best-first search
than to game-style random playouts.

Recommended first policy:

```text
MCTS-guided best-first beam search + verifier-shaped reward
```

Tree mapping:

| MCTS concept | Rethlas-plus meaning |
| --- | --- |
| root | active goal set or one goal target |
| state | branch node plus verified/stale/blocked dependency context |
| action | `repair`, `expand`, `spawn_sibling`, `defer`, `abandon`, `promote` |
| expansion | create one candidate theorem/helper lemma or schedule one verifier job |
| simulation | cheap selector/value estimate from library retrieval, verifier reports, and failure signatures |
| rollout result | verifier outcome, closed-goal progress, verified helper progress, or stuck/counterexample state |
| backup | update branch/action value statistics and failure signatures |

Use UCT/PUCT-style selection:

```text
selection_score =
  Q(s, a)
+ C * prior(s, a) * sqrt(log(1 + N(s)) / (1 + N(s, a)))
- penalties(s, a)
```

`prior(s, a)` can come from the selector agent's judgment, retrieval
similarity, known useful tactics for the topic, or user hints. If no prior is
available, use a uniform prior over legal actions.

Score:

```text
score =
  mcts_selection_score
+ verifier_signal
+ verified_helper_progress
+ library_reuse_bonus
+ novelty_bonus
- complexity_penalty
- repeated_failure_penalty
```

Reward shaping:

- candidate theorem accepted: `+1.0`
- useful helper lemma accepted: `+0.4`
- local verifier gap: `+0.2`
- critical verifier report: `-0.5`
- repeated failure signature: `-0.4`
- new reusable lemma from an abandoned branch: positive library credit
- large dependency growth without accepted results: negative complexity credit

Initial parameters:

```yaml
mcts:
  beam_width: 3
  max_active_branches_per_goal: 4
  max_depth: 3
  max_expansions_per_goal: 12
  exploration_c: 0.8
  use_priors: true
  virtual_loss: 0.2
```

Search scores are conditioned on the active goal set. A branch that is useful
for a required goal should outrank a mathematically interesting branch that
does not help any active goal, unless the run is explicitly in `library_sweep`
mode.

MCTS must respect scheduler legality:

- do not select abandoned/exhausted/promoted/`needs_user` branches;
- do not expand past branch/run budgets;
- do not select an action whose dependencies are not ready unless the action is
  to resolve those dependencies;
- do not continue goal-directed search after all goals are closed verified.

## Harness-first Architecture

Rethlas-plus should be designed as a harness around Codex, not as an attempt to
out-reason Codex with hand-written agent logic.

The harness owns:

- durable node/library state;
- Kuzu graph and search/hypergraph indexes;
- event journal, replay, and audit;
- hash canonicalization and invalidation;
- goal sets, stop conditions, and branch/run budgets;
- MCTS bookkeeping and legal-action enumeration;
- context packing for Codex calls;
- prompt contracts and output schemas;
- verifier/generator job lifecycle;
- loop guards and failure signatures;
- deterministic admission of all proposed changes.

Codex owns:

- mathematical strategy judgment;
- proof generation;
- informal proof criticism;
- premise relevance judgment when retrieval is ambiguous;
- policy/value estimates for MCTS;
- explanations for why a branch/action is promising or stuck.

The main product surface is therefore not a custom prover brain. It is a
high-quality harness that repeatedly gives Codex the right local mathematical
context, the right legal choices, and the right output contract.

This design should make Codex upgrades useful immediately: better Codex models
improve selector priors, proof drafts, verifier critiques, and failure analysis
without changing the deterministic state machine.

### Who Runs MCTS?

Rethlas should own the MCTS engine. Codex should be the policy/value oracle
inside that engine.

Recommended architecture:

```text
Rethlas deterministic MCTS controller
  - builds compact search context from Kuzu/node docs/events
  - enumerates legal actions and filters illegal branches
  - stores visit counts, Q values, priors, virtual loss, failure signatures
  - enforces goal stop conditions, budgets, cooldowns, and hash legality
  - calls Codex policy/value agent when judgment is needed
  - applies only schema-valid, legality-checked decisions

Codex policy/value agent
  - reads the prepared context packet
  - evaluates strategies and branch promise
  - proposes priors/value/action rationales
  - may recommend expand/repair/spawn/abandon/promote
  - does not mutate Kuzu, node docs, MCTS stats, or run state directly
```

This gives the system the benefit of Codex improvements while keeping
replayability and safety in Rethlas. If Codex gets stronger, the policy/value
quality improves without changing the deterministic controller.

Codex-facing context should be curated, not raw workspace state:

- active goal set and stop condition;
- current branch frontier with statuses and budgets;
- dependency closure summary;
- relevant verified nodes retrieved from Kuzu/vector/BM25;
- recent verifier reports and normalized failure signatures;
- legal action list;
- constraints: strategies to avoid, budget remaining, promotion rules.

Codex output should be strict JSON:

```json
{
  "context_hash": "sha256:...",
  "recommended_actions": [
    {
      "action": "expand",
      "branch_id": "b002",
      "prior": 0.42,
      "value": 0.31,
      "reason": "The slice bridge is verified; the remaining gap is local to open-dense maximality.",
      "strategy": "Prove a bridge lemma using an open dense intersection criterion.",
      "avoid": ["signed diagram induction"]
    }
  ],
  "state_recommendations": [
    {
      "branch_id": "b001",
      "status": "stuck",
      "reason": "Same missing-reference failure signature repeated twice."
    }
  ]
}
```

Rethlas may ignore any recommendation that violates scheduler legality,
budget, hashes, branch status, or goal stop conditions.

## Agentized Workflows

Not every component should be an LLM agent. Agentize workflows that need
mathematical judgment, strategy choice, or semantic comparison. Keep
correctness-critical bookkeeping deterministic.

Recommended split:

| Component | Agent? | Reason |
| --- | --- | --- |
| Librarian projection, hash canonicalization, Kuzu sync | no | Must be deterministic, replayable, and testable |
| Goal stop condition and branch/run budgets | no | Must be hard scheduler constraints, not model judgment |
| MCTS statistics, visit counts, value backup, legality filtering | no | Deterministic search bookkeeping |
| Retrieval index over nodes/papers | mostly no | Embeddings/BM25/Kuzu queries should be deterministic tools |
| Retrieval/premise selection summary | yes, bounded | Needs semantic judgment about which lemmas are relevant |
| Codex MCTS policy/value oracle | yes, bounded | Needs strategy judgment; output is prior/value/action JSON |
| Branch generator/prover | yes | Creates candidate proofs and helper lemmas |
| Informal verifier/critic | yes | Checks natural-language proof gaps and produces repair signals |
| Failure analyst | yes, bounded | Clusters repeated failures and decides whether a route is genuinely new |
| Promotion compatibility checker | yes, bounded | Judges exact equality vs stronger theorem implying the canonical target |
| Formalizer | later | Converts valuable informal nodes to Lean/Coq/Isabelle artifacts |

Minimum Phase 2 agent set:

1. **Selector / policy-value agent**
   - Reads active goal state, branch frontier, verifier reports, library
     retrieval, and failure signatures.
   - Produces strict JSON with action candidates, priors, value estimates, and
     reasons.
   - Does not directly mutate nodes, Kuzu, MCTS stats, or run state.

2. **Branch generator agent**
   - Given a selected action, writes candidate theorem/helper node drafts.
   - Modes: direct proof, stronger theorem, bridge lemma, subgoal split,
     counterexample/obstruction, local repair.
   - Must use branch labels and search metadata.

3. **Informal verifier agent**
   - Verifies one node locally against cited dependency statements.
   - Emits verdict, gap/critical report, repair hint, and structured failure
     features.
   - Does not decide closed verification; librarian/scheduler computes closure.

4. **Failure analyst / branch closer**
   - Normalizes failure signatures.
   - Detects duplicate selector actions, repeated verifier gaps, repeated
     missing refs, and strategy paraphrases.
   - Recommends `stuck`, `abandoned`, `exhausted`, `needs_user`, or
     `spawn_sibling`.

5. **Promotion checker**
   - Runs when a candidate is locally/closed verified.
   - Checks whether the candidate exactly matches the canonical target or
     implies it.
   - Emits a promotion recommendation; deterministic scheduler applies the
     event only if constraints pass.

Optional Phase 2.5/3 agents:

- **Library curator agent**: suggests `topic_path`, tags, duplicate merges,
  and library-role changes. It should produce suggestions only; librarian
  validates and applies.
- **Retrieval query agent**: rewrites mathematical context into Kuzu/BM25/vector
  queries and summarizes retrieved nodes for selector/generator.
- **Counterexample agent**: specializes in trying to refute suspicious
  statements when repair loops show semantic obstruction.

Agent output rules:

- All agent outputs must be strict JSON plus optional Markdown rationale.
- JSON is advisory until admitted by deterministic validators.
- Every action must cite the input hashes/run state it used.
- Agents may recommend state transitions, but scheduler/librarian perform the
  transition.
- Agents must never be the only guard against infinite loops; budgets and
  legality checks are deterministic.

## Loop and Oscillation Guards

The current Phase I flow can detect several stuck states but usually does not
stop dispatch automatically. Phase 2 should turn repeated no-progress patterns
into explicit branch or run states.

Current Phase I loop risks to avoid carrying forward:

- A proof-requiring node rejected by the verifier returns to `pass_count = -1`.
  With no hard repair budget, it can be dispatched to the generator forever.
- Crash, timeout, and repeated same-reason `apply_failed` outcomes are surfaced
  as human-attention items, but they do not automatically block future
  dispatch.
- A `publishing` job whose event never receives an `AppliedEvent` row can block
  the same target until restart.
- Repeated statement changes in a dependency can keep invalidating downstream
  verification work.
- Repeated missing references can lead to generator/verifier repair loops if
  unresolved refs are not indexed as first-class blocked dependencies.

Recommended hard budgets:

```yaml
search_budget:
  max_active_branches_per_goal: 4
  max_expansions_per_goal: 12
  max_branch_depth: 3
  max_repairs_per_branch: 2
  max_same_failure_signature: 2
  max_duplicate_selector_actions: 1
  max_hash_mismatch_per_target_window: 3
  max_consecutive_crash_or_timeout: 3
  publishing_stale_seconds: 300
```

Recommended statuses:

```text
run_status:
  active | done | budget_exhausted | counterexample_found |
  needs_user | degraded

branch_status:
  active | cooldown | stuck | abandoned | exhausted | promoted

node_status:
  unverified | locally_verified | closed_verified |
  blocked_on_dependency | blocked_on_unresolved_ref |
  stale_due_to_dependency | needs_generation | needs_user
```

Every scheduled action should be justified by one of these progress outcomes:

- a goal becomes closed verified;
- a node in an active goal closure becomes locally or closed verified;
- an unresolved reference is resolved;
- a blocked goal branch is unlocked;
- a genuinely new non-duplicate branch is opened within budget;
- a branch is marked stuck/abandoned/exhausted, shrinking the active search
  frontier;
- a counterexample or statement-level obstruction is recorded.

If an action does none of these and repeats the same failure signature, do not
dispatch it again. Change state instead.

Failure signatures should be normalized. Useful inputs:

- verifier verdict kind plus normalized critical/gap summary;
- missing reference label set;
- `apply_failed` reason and detail class;
- crash/timeout marker;
- generator strategy id;
- statement/proof hash pair;
- selector action hash.

Circuit-breaker rules:

- Same branch receives `max_repairs_per_branch` verifier rejections without a
  new verified helper or changed failure signature: stop local repair and ask
  selector to spawn a sibling or abandon.
- Same failure signature appears `max_same_failure_signature` times on a
  branch: mark branch `stuck`.
- Same target crashes or times out `max_consecutive_crash_or_timeout` times:
  mark node `needs_user` and stop automatic dispatch for that target.
- Same selector action for the same selector input state is emitted twice:
  reject the duplicate action; after the duplicate budget, mark the branch
  `needs_user`.
- Repeated `hash_mismatch` for one target should trigger a cooldown and
  dependency-state refresh rather than immediate re-dispatch.
- Stale `publishing` jobs should be reconciled by event id. If no matching
  event file or `AppliedEvent` row appears after `publishing_stale_seconds`,
  mark the job lost/degraded and unblock the scheduler with a visible alert.
- Promotion is one-way within a goal-directed run. Once a candidate is
  promoted and closes the goal, do not let the selector oscillate between
  alternate verified candidates.
- Metadata normalization must be idempotent. The librarian should not emit a
  new truth event merely because it rewrote derived frontmatter fields into the
  canonical format.
- Once all required goals are closed verified, stop goal-directed generation.
  Do not let non-goal exploratory nodes keep the run alive.

Selector/MCTS exploration bonuses must be bounded by branch status and budget.
Abandoned, exhausted, promoted, or `needs_user` branches are not selectable,
even if an exploration formula would otherwise give them a high score.

## Generator Behavior

Generator in ordinary repair mode may use previous proof and repair hints.

Generator in search branch mode should receive:

- root target;
- branch strategy;
- existing branch state;
- relevant library summary from Kuzu/BM25/vector retrieval;
- retrieved learner-created source-backed nodes;
- aliases, proof variants, and dependency summaries;
- verifier reports for this branch;
- strategies to avoid.

It should not be anchored to the old failed proof unless the selector selected
local repair.

Generator must search the theorem/lemma library before proposing new helper
nodes. It should prefer reusing admitted KB nodes over inventing a new lemma.
Learner expands this library from papers/books/sources; generator consumes it
through retrieval context packets.

Generator should respect verification state:

- `closed_verified` nodes are safe reusable premises.
- locally verified nodes with open dependencies are usable only when the
  scheduler allows dependency risk.
- unverified `source_backed` nodes are useful hints or candidate premises, but
  should not be treated as settled theorem facts.
- referee reports and issue records should downrank or block nodes with
  unresolved major gaps.

## Verifier Behavior

Verifier continues to verify ordinary node documents. It should not try to
prove that every node in the library is complete.

The verifier's unit of work is local and kind-specific:

```text
node kind + statement + optional proof + cited dependency statements + provenance/source context
```

It writes a verdict for the node's `verification_input_hash`. The librarian
then computes `closure_status` from dependency closure state.

Verifier modes:

```text
verify_definition         well-formed definition / notation / dependencies
verify_external_theorem   citation/source statement and hypotheses
verify_proof_node         lemma/proposition/theorem proof check
verify_bridge_node        generated bridge/helper proof plus provenance
```

Definition checks should include:

- referenced concepts exist or are explicitly marked unresolved;
- notation is not ambiguous in the local topic context;
- no circular definition dependency is introduced;
- required parameters and ambient assumptions are stated;
- the definition is usable by later theorem nodes.

External theorem checks should include:

- source note or source span exists;
- cited statement matches the node statement;
- hypotheses, conventions, version, and theorem numbering are recorded;
- source access failures are explicit, not silently accepted.

Verifier scheduling should prioritize:

1. ready nodes in the dependency closure of active goals;
2. ready nodes that unlock many blocked goal branches;
3. low-dependency helper lemmas whose verification would enable several
   candidates;
4. branch candidates with high selector score;
5. opportunistic reusable library nodes when workers would otherwise be idle.

This means the system should verify as many useful ready nodes as possible
before generating more proof text. Verification is cheaper than expanding a
bad branch, and verified helper lemmas are reusable even if the branch that
created them is abandoned.

For informal proofs, verifier acceptance is an attestation, not absolute
mathematical truth.

## Scheduler Behavior

Scheduler should:

- read Kuzu/index and node metadata;
- track the active goal set;
- compute goal dependency closures and reverse-dependency unlock counts;
- schedule ready verification work before opening new generation work when
  verification can unblock goals;
- avoid scheduling unverified nodes in abandoned branches;
- continue to preserve and use verified branch assets;
- open fresh branch candidates when a target is stuck;
- promote winning candidates according to explicit promotion rules;
- stop the goal-directed run when all required goals are closed verified;
- enforce branch/run budgets and circuit breakers before dispatching workers.

Recommended verifier priority:

```text
priority =
  goal_closure_bonus
+ unlock_count_bonus
+ branch_score_bonus
+ ready_leaf_bonus
+ age_bonus
- abandoned_branch_penalty
- non_goal_penalty
- repeated_failure_penalty
```

Generation should happen when the active goal frontier has no high-priority
ready verification work left, or when the selector decides that a goal needs a
fresh branch rather than local repair.

## Implementation Roadmap

### P2.A: Node schema

- Extend frontmatter with `topic_path`, `tags`, `library_role`,
  `verification`, `goal`, and `search`.
- Define field ownership: user/generator-owned vs librarian/verifier-owned.
- Keep one md file per node.

### P2.B: Recursive nodes layout

- Support `nodes/<topic_path>/<prefix>_<slug>.md`.
- Keep label as identity.
- Update renderer and readers to use recursive paths.
- Change generator node scanning from one-level glob to recursive glob.

### P2.C: Hashing and invalidation

- Define canonical Markdown/YAML parsing for hash inputs.
- Add `statement_hash`, `proof_hash`, `dependency_statement_root`,
  `verification_input_hash`, `verification_hash`, `closure_hash`, and
  `metadata_hash`.
- Implement reverse-dependency invalidation for statement changes.
- Keep topic/tag/search/goal metadata out of proof-verification hashes.

### P2.D: Kuzu library index

- Store topic path, tags, file path, library role, and search metadata.
- Store goal sets, hash fields, local status, and closure status.
- Add branch/search relations.
- Add reverse-dependency indexes for invalidation and scheduler scoring.
- Preserve dependency graph by labels.

### P2.E: Goal set and stop condition

- Add persistent goal metadata and/or run-level goal-set configuration.
- Compute `goal_done(label)` from closed verification, not local verification.
- Stop goal-directed runs when all required goals are done.
- Leave non-goal unverified nodes alone unless `library_sweep` is enabled.

### P2.F: Verifier scheduler

- Prefer ready nodes that unblock active goals.
- Prefer low-dependency helper lemmas that unlock many candidates.
- Verify useful ready nodes before expanding more branches.
- Opportunistically verify reusable library nodes only when goal work is idle.

### P2.G: Loop guards and run state

- Add explicit run and branch statuses.
- Track normalized failure signatures.
- Enforce repair, expansion, crash/timeout, duplicate-action, and
  hash-mismatch budgets.
- Add stale-`publishing` reconciliation.
- Ensure metadata normalization is idempotent and does not create truth-event
  loops.

### P2.H: Search branch layer

- Represent branch candidates/helpers as ordinary node documents.
- Add branch metadata in node frontmatter and Kuzu.
- Filter abandoned unverified branches from scheduling.
- Preserve verified branch nodes permanently.

### P2.I: Selector agent

- Add a branch selector that reads Kuzu/library summaries and verifier reports.
- Use MCTS-guided best-first beam search with UCT/PUCT-style exploration.
- Condition branch value on the active goal set.
- Do not allow selection of abandoned, exhausted, promoted, or `needs_user`
  branches.
- Emit strict JSON actions.

### P2.J: Promotion

- Add `search.candidate_promoted` journal event.
- Verify candidate statement compatibility with canonical target.
- Revise canonical proof to cite the winning branch candidate.
- Keep alternate verified branch candidates.

### P2.K: Agent contracts

- Define strict JSON schemas for selector, generator, verifier, failure
  analyst, and promotion checker outputs.
- Keep MCTS bookkeeping and state transitions deterministic.
- Treat Codex as the policy/value oracle for MCTS, not as the MCTS state owner.
- Treat Rethlas as the harness that prepares context, constraints, prompts,
  schemas, and admission checks around Codex.
- Add validator/admission tests for every agent output schema.
- Ensure every agent recommendation is tied to input hashes and run state.

## Locked Decisions

- Manual Markdown editing is allowed only through librarian validation; the
  editor may change node docs, not runtime or index files.
- Branch candidate statements may be stronger than the root target, but
  promotion requires exact canonical equivalence to the target statement or an
  explicit target rewrite.
- Promotion compatibility is canonical equality after normalization /
  alpha-equivalence.
- Canonical hash input format is Markdown+YAML normalization with stable field
  ordering and normalized line endings.
- Verifier identity belongs in the event/attestation layer, not in
  `verification_hash`.
- Failed-branch similarity should start with normalized failure signatures and
  exact structural overlap, not embeddings.
- Verifier reports should be stored fully in node detail / event records, but
  only summarized excerpts belong in frontmatter.
- `library_sweep` should be a separate opt-in scheduler mode, not the default
  coordinator mode.
- Failure-signature schema should be normalized `reason + structural_context +
  dependency context`, hashed for duplicate detection.
