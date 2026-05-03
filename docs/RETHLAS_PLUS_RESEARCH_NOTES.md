# Rethlas-plus Research Notes

Date: 2026-05-02

This note records the design direction discussed for Rethlas-plus. It is a
proposal, not the current Phase I contract.

The non-blockchain system design has been split into
`docs/PHASE2_RETHLAS_PLUS.md`. The blockchain, token, Lean formalization market,
and decentralized Lean artifact-cache ideas have been split into
`docs/PHASE5.md`.

The phase-level roadmap, including Phase 0 / Phase I / current Phase II and
the proposed Rethlas-plus phases, is recorded in
`docs/RETHLAS_PLUS_ROADMAP.md`.

The concrete harness-first system design is recorded in
`docs/RETHLAS_PLUS_SYSTEM_DESIGN.md`.

Phase 3 source ingestion and review, including the `learner` and `referee`
agent roles, is recorded in `docs/PHASE3_LEARNER_REFEREE.md`.

## Core diagnosis

The current system is too close to a single proof-slot workflow:

- A node with `pass_count = -1` enters the generator pool.
- If a proof fails, `repair_count` increases and the next generator prompt
  receives the previous proof, verifier report, and repair hint.
- That makes the generator naturally repair the same direction instead of
  exploring materially different proof strategies.
- The coordinator also prevents concurrent same-label work, and
  `generator.batch_committed` replaces the current statement/proof for the
  target label. So multiple proofs for the same statement cannot safely race
  inside one target label.

This explains the induced-orbit-toy failure mode: once the first route is
chosen, later attempts are biased toward local repair and complexity grows
around the same stuck idea.

## First-class object

For Rethlas-plus, the first-class object should be the node document:

```text
NodeDocument = one theorem/lemma/definition/candidate as one md file
```

The knowledge base is the collection of node documents:

```text
knowledge_base/nodes/**/*.md
```

Kuzu is the same knowledge base viewed as a graph/index. Events are a journal
of changes and attestations, not the main object users or agents reason about.

Recommended model:

```text
nodes/**/*.md     current theorem/lemma/definition library
dag.kz / Kuzu     graph database representation and scheduler index
events/           append-only history / audit / replay journal
runtime/          temporary process state
```

The important conceptual shift is node-first rather than event-first. Events
remain valuable for auditability and recovery, but the mathematical object the
system should expose is the node document.

## Node document format

Metadata and content should live in one file. Do not split `meta.yaml` from
`proof.md`; that creates drift and makes review harder.

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
  statement_hash: sha256:...
  verification_hash: sha256:...

search:
  root_target: thm:induced_orbit_toy_problem
  branch_id: b002
  branch_status: promoted
  strategy: slice_model_open_dense_reduction

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

Stable metadata belongs in frontmatter:

- `label`
- `kind`
- `topic_path`
- `tags`
- `library_role`
- `verification`
- `goal`
- `search`
- `depends_on`

Ephemeral runtime state does not belong in frontmatter:

- process ids
- active job ids
- heartbeat timestamps
- temporary Codex logs

## Topic organization

`nodes/` should allow subdirectories by topic:

```text
knowledge_base/nodes/
  lie_theory/
    nilpotent_orbits/
      thm_induced_orbit_toy_problem.md
      lem_induced_orbit_slice_bridge.md
  analysis/
    convexity/
      lem_vandermonde_bound.md
```

Rules:

- The label is the unique identity.
- The path is not the identity.
- `\ref{...}` always points to labels, never paths.
- `topic_path` determines the rendered path.
- Moving a node between topics is metadata-only and must not change
  statement/proof hashes.
- Prefer one primary `topic_path` plus multiple `tags`; do not duplicate one
  node into multiple topic directories.

## Librarian role

The librarian is the synchronizer and validator between the three views:

```text
nodes/**/*.md <-> Kuzu graph/index <-> events journal
```

Recommended direction:

- `nodes/**/*.md` is the current human/agent-readable knowledge base.
- Kuzu is the indexed graph representation used by scheduler, dashboard, and
  dependency queries.
- Events record changes, verifier attestations, generator batches, branch
  decisions, and promotion decisions.

The librarian should:

1. Parse node markdown/frontmatter.
2. Validate unique labels, kind-prefix rules, refs, topic paths, and schema.
3. Compute `depends_on`, `statement_hash`, and `verification_hash`.
4. Update Kuzu.
5. Normalize derived fields back into the markdown frontmatter.
6. Append or consume event journal entries.
7. Reconcile on startup.

Reconciliation policy:

- If Kuzu is missing or stale, rebuild it from nodes and/or events.
- If a rendered node file is stale, normalize it.
- If a node file was manually edited, ingest it through librarian checks.
- If an orphan file is present, surface it clearly instead of silently treating
  it as verified knowledge.

## Events

Current Phase I is event-first. That is safe, but it is not the best mental
model for Rethlas-plus.

Rethlas-plus should treat events as a journal:

- `user.node_added`
- `user.node_revised`
- `user.node_metadata_updated`
- `generator.batch_committed`
- `verifier.run_completed`
- `search.branch_opened`
- `search.branch_scored`
- `search.branch_closed`
- `search.candidate_promoted`

Events remain useful for:

- audit history;
- replay/debug;
- timestamped provenance;
- conflict records;
- chain anchoring.

But the working knowledge object is still the node document.

## Hashing and goal-directed verification

Phase 2 needs hash fields that answer different questions. Do not use one
monolithic node hash.

Recommended split:

- `statement_hash`: normalized statement and mathematical assumptions.
- `proof_hash`: normalized proof text plus cited labels.
- `dependency_statement_root`: direct dependency labels plus their
  `statement_hash` values.
- `verification_input_hash`: the exact node/verifier input a verdict applies
  to.
- `verification_hash`: the verifier attestation/report digest.
- `closure_hash`: local verification plus dependency closure verification.
- `metadata_hash`: topic/tags/search/goal metadata that should not reset proof
  verification.

Important invalidation rule:

- Statement changes propagate to downstream verification inputs.
- Proof changes invalidate the edited node's local verification.
- Topic/tag/search/goal metadata changes should update indexes but should not
  invalidate proof verification.

Distinguish:

- local verification: this node's statement/proof pair passes the verifier;
- closed verification: this node and every dependency in its transitive closure
  are verified.

Goal completion must use closed verification.

Rethlas-plus should support explicit goal sets. The coordinator should stop a
goal-directed run when all required goals are closed verified. It does not need
to expand or verify every node in the library. A separate `library_sweep` mode
can verify non-goal nodes opportunistically, but that should be opt-in.

Verifier scheduling should prioritize ready nodes that help active goals:

1. ready nodes in active goal dependency closures;
2. nodes that unlock many blocked goal branches;
3. low-dependency helper lemmas useful to several candidates;
4. high-scoring branch candidates;
5. reusable library nodes only when goal work is idle.

This keeps the system from wasting verifier/generator budget on interesting
but irrelevant parts of the library before the declared goals are done.

## Loop risks and circuit breakers

Current Phase I can detect stuck states, but it often keeps dispatching. In
particular, a proof-requiring node rejected by the verifier goes back to
`pass_count = -1`, and there is no hard `repair_count` budget. The dashboard
surfaces high repair counts, repeated crashes/timeouts, and repeated
`apply_failed` reasons, but those are not automatic stop conditions.

Phase 2 tree search needs harder guards:

- cap repairs per branch;
- cap active branches and total expansions per goal;
- normalize failure signatures and stop repeating identical failures;
- mark branches `stuck`, `abandoned`, `exhausted`, or `needs_user` instead of
  redispatching forever;
- add cooldown for repeated `hash_mismatch`;
- reconcile stale `publishing` jobs that never receive an `AppliedEvent`;
- make promotion one-way within a goal-directed run so the selector cannot
  oscillate between alternate verified candidates;
- make librarian metadata normalization idempotent so frontmatter cleanup does
  not create truth-event loops;
- stop all goal-directed generation once the active goal set is closed
  verified.

Every scheduled action should either verify something goal-relevant, unlock a
blocked branch, resolve a missing reference, open a genuinely new branch within
budget, promote a candidate, record a counterexample/obstruction, or shrink the
active frontier by marking a branch done/stuck/abandoned. If it does none of
these and repeats the same failure signature, it should not be scheduled again.

## Search tree

The search tree should not be a second knowledge base. It should be expressed
through normal node documents plus search metadata.

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

Abandoning means only:

- stop allocating generator/verifier budget to unverified nodes in that branch;
- keep verified nodes as reusable library assets;
- record the reason so selector agents avoid the same route.

Promotion rule:

- If a candidate branch theorem verifies, it may be promoted to the canonical
  target.
- Promotion can revise the canonical target proof to cite the winning
  candidate:

```markdown
**Proof.** This follows from \ref{thm:target_b002_candidate}. \square
```

Other verified candidates remain as alternate theorems/proofs.

## Branch expansion

Do not let repair mode be the only response to failure. For search mode, a new
branch should be a fresh proof attempt with a different strategy and should not
be forced to inherit the previous proof text.

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

Selector output should be strict JSON, for example:

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

## Tree search or graph expansion?

After checking related proof-search systems, the answer is: both, but at
different layers.

- Aesop in Lean is a tree-search tactic: rules create subgoals and the search
  tree is explored best-first with configurable limits.
- HTPS/Evariste makes the more important point for Rethlas: proof search is a
  hypergraph problem. Goals are nodes, tactics are hyperedges, and one tactic
  can create several subgoals that all need to be proved.
- DeepSeek-Prover-V1.5 uses MCTS in Lean via truncate-and-resume. Its search
  tree stores successful proof prefixes and expands from selected nodes, using
  Lean feedback and intrinsic rewards to avoid sparse-reward stagnation.
- LeanDojo/ReProver emphasizes retrieval: search without good premise/library
  retrieval wastes budget.
- Isabelle Sledgehammer and CoqHammer are not MCTS systems; they are closer to
  premise selection, external solver portfolios, and proof reconstruction.

For Rethlas-plus:

```text
MCTS is the selector/controller view.
Kuzu + node documents are the graph/hypergraph substrate.
```

This means:

- alternate proof directions form OR choices;
- dependencies/subgoals form AND requirements;
- one branch action may introduce multiple helper nodes, so it is a hyperedge;
- verified lemmas are shared graph nodes, not duplicated tree leaves;
- MCTS should see a goal-local hypertree projection, while the library remains
  a reusable graph.

## MCTS-guided selector

Introduce Monte Carlo Tree Search as the selector policy, but keep it bounded
inside the scheduler. MCTS should decide which branch/action to spend budget on
next; it should not own the knowledge base or the truth state.

Proof search has expensive expansions and sparse rewards: a generator/verifier
cycle is not a cheap random rollout. So the right shape is MCTS-guided
best-first search with shallow, verifier-shaped value estimates.

Use a hybrid:

```text
MCTS-guided best-first beam search + verifier-shaped reward
```

Mapping:

- root: active goal set or one goal target;
- state: branch plus dependency/verification context;
- action: `repair`, `expand`, `spawn_sibling`, `defer`, `abandon`, `promote`;
- expansion: create a candidate/helper node or schedule one verifier job;
- rollout/value: cheap estimate from verifier reports, retrieval, novelty, and
  failure signatures;
- backup: update branch/action value statistics.

Suggested score:

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
beam_width: 3
max_active_branches_per_target: 4
max_depth: 3
max_expansions_per_target: 12
exploration_c: 0.8
```

MCTS must obey branch budgets, goal stop conditions, and circuit breakers. It
must not select abandoned, exhausted, promoted, or `needs_user` branches.

MCTS ownership:

```text
Rethlas owns the deterministic MCTS controller.
Codex acts as the policy/value oracle.
```

Rethlas should build the context packet, enumerate legal actions, store visit
counts and Q values, enforce budgets/goal stop conditions/hash legality, and
apply only schema-valid decisions. Codex should read the curated context and
return priors, value estimates, action recommendations, and mathematical
rationale. This lets the system benefit as Codex improves while keeping
replayability and safety under Rethlas control.

Harness-first principle:

```text
Rethlas is the harness.
Codex is the reasoning engine.
```

Rethlas should focus on durable state, context packing, legal action
enumeration, MCTS bookkeeping, hashes, goals, budgets, schemas, replay,
admission, and auditing. Codex should do mathematical strategy, proof
generation, informal criticism, premise relevance judgment, and policy/value
estimation. This is the right boundary if Codex capability keeps improving:
the harness becomes more valuable because it consistently supplies the right
context and constraints to a stronger model.

## Which workflows should become agents?

Agentize judgment, not bookkeeping.

Should be agents in Phase 2:

- selector / policy-value agent;
- branch generator/prover;
- informal verifier/critic;
- failure analyst / branch closer;
- promotion compatibility checker.

Can be agent-assisted but should remain tool-driven:

- retrieval/premise selection;
- topic/tag/library-role curation;
- counterexample search.

Should not be agents:

- librarian projection;
- hash canonicalization and invalidation;
- Kuzu synchronization;
- MCTS visit counts/value backup/legal-action filtering;
- goal stop condition;
- branch/run budget enforcement;
- event admission and replay.

Reason: those parts must be deterministic, replayable, and testable. Agents can
recommend actions, priors, values, tags, or state transitions, but scheduler
and librarian apply them only after schema and legality checks.

## Generator and verifier behavior

Generator:

- In ordinary repair mode, may use previous proof and repair hints.
- In search branch mode, should receive `search_context` and strategy, but not
  be anchored to the old failed proof unless the selector explicitly asks for a
  local repair.
- Must search the theorem/lemma library first.
- Should create branch-scoped candidate/helper labels.

Verifier:

- Verifies ordinary node documents.
- Its verdicts update verification metadata and Kuzu index.
- For informal proofs, verifier acceptance is an attestation, not absolute
  mathematical truth.

Scheduler:

- Reads Kuzu/index and node metadata.
- Tracks the active goal set.
- Schedules ready verification work that unlocks goals before opening more
  generation work.
- Does not schedule unverified nodes belonging to abandoned branches.
- Continues to schedule verified branch assets as normal dependencies.
- Promotes winning candidates according to explicit promotion rules.
- Stops a goal-directed run when every required goal is closed verified.

## Lean and formalization

Lean should not be required for the core Rethlas exploration workflow. Many
target domains may lack enough formal library support, and a Lean-first design
would slow down mathematical search.

Lean should be the high-confidence settlement layer:

```text
informal node -> formal statement -> Lean proof checked -> formal attestation
```

Verification levels:

```text
L0 generated / unverified
L1 LLM verifier accepted
L2 multiple independent LLM/human attestations
L3 formal statement aligned
L4 Lean/Coq/Isabelle proof kernel checked
L5 upstreamed / maintained across versions
```

Node frontmatter can link to formalization artifacts:

```yaml
formalization:
  status: proof_checked
  system: lean4
  package: rethlas_orbits
  module: Rethlas.Orbits.InducedToy
  lean_name: induced_orbit_toy_problem
  lean_version: ...
  mathlib_commit: ...
  artifact_cid: ipfs://...
  proof_hash: sha256:...
```

The chain/reward layer should put major rewards on L4/L5, not merely on LLM
acceptance.

## Blockchain / token layer

The chain should not be the database and should not decide mathematical truth.
It should provide:

- timestamping;
- provenance;
- staking;
- bounty escrow;
- verifier/finalization attestations;
- snapshot anchoring.

Recommended chain object:

```text
Merkle root of nodes/**/*.md snapshot
content CID for snapshot/artifacts
attestations over statement/proof/formal hashes
```

Do not store full proofs on-chain.

Verifier miners should be better understood as attesters:

1. Prover/formalizer submits a node or Lean artifact.
2. Verifier stakes and runs the required verifier/build process.
3. Verifier submits commit/reveal verdict and report hash.
4. Challenge window opens.
5. If not successfully challenged, rewards are released.
6. False attestations can be slashed.

Token rewards should prioritize:

- formal proof checked by a pinned kernel/toolchain;
- reusable definitions and lemmas;
- successful counterexample/gap discovery;
- maintenance across Lean/mathlib upgrades;
- high-value theorem bounties.

For informal proofs, on-chain records should say:

```text
Verifier X attested verification_hash H as accepted.
```

They should not claim the theorem is absolutely true.

## Lean library market

A strong long-term direction is a market for building formal Lean libraries,
not just verifying isolated theorems.

Task types:

- definition bounty;
- statement alignment bounty;
- lemma bounty;
- proof bounty;
- dependency bounty;
- maintenance bounty.

Rethlas discovers useful informal theorem/lemma dependencies. The formal
market turns selected nodes into Lean definitions, statements, and proofs.
Kuzu maintains the formal dependency graph and bounty decomposition.

## Implementation roadmap

Phase A: document/node schema

- Extend node frontmatter with `topic_path`, `tags`, `library_role`,
  `verification`, `goal`, `search`, and optional `formalization`.
- Keep one md file per node.
- Define ownership of fields: user/generator-owned vs librarian/verifier-owned.

Phase B: recursive nodes layout

- Support `nodes/<topic_path>/<prefix>_<slug>.md`.
- Keep label as identity.
- Update renderer and readers to use recursive paths.
- Change generator node scanning from one-level glob to recursive glob.

Phase C: Kuzu as node-library index

- Store topic path, tags, file path, library role, goal metadata, search
  metadata, hash fields, local verification status, and closure status.
- Add indexes/relations for branches and owned labels.
- Add reverse-dependency indexes for invalidation and verifier scheduling.
- Preserve dependency graph by labels.

Phase D: hashing, goals, and verifier scheduler

- Define canonical hash inputs for statement/proof/dependency/verifier fields.
- Use hash-based invalidation instead of raw timestamp or file-byte checks.
- Add explicit goal sets and `closed_verified` stop conditions.
- Prioritize ready verification work that unlocks active goals.
- Do not require all library nodes to be verified before stopping a goal run.

Phase E: search branch layer

- Represent branch candidates/helpers as ordinary node documents.
- Add branch metadata in node frontmatter and Kuzu.
- Filter abandoned unverified branches from scheduling.
- Preserve verified branch nodes permanently.

Phase F: selector agent

- Add a branch selector that reads Kuzu/library summaries and verifier reports.
- Use MCTS-guided best-first beam search with UCT/PUCT-style exploration.
- Condition branch value on the active goal set.
- Emit strict JSON actions.

Phase G: promotion

- Add `search.candidate_promoted` journal event.
- Verify candidate statement compatibility with canonical target.
- Revise canonical proof to cite the winning branch candidate.
- Keep alternate verified branch candidates.

Phase H: agent contracts

- Define strict JSON schemas for selector, generator, verifier, failure
  analyst, and promotion checker.
- Keep MCTS bookkeeping and all state transitions deterministic.
- Require every agent recommendation to cite input hashes and run state.

Phase I: formalization and chain

- Add formalization metadata.
- Add snapshot Merkle root generation.
- Add optional on-chain anchoring.
- Later add staking/bounty contracts and formal verification rewards.

## Locked Decisions

- Phase I event-root replay should migrate to checkpointed node-library
  snapshots plus replay of later events once node-doc canonicalization is
  stable.
- Manual md editing is allowed only through librarian validation.
- Branch candidates may prove stronger statements, but promotion requires exact
  canonical equivalence to the target statement or an explicit target rewrite.
- Promotion compatibility is canonical equality after normalization /
  alpha-equivalence.
- Failed branches should first be clustered by normalized failure signatures and
  exact structural overlap.
- Hash inputs should include mathematical content and dependency statements;
  topic moves, tags, job ids, and heartbeats are metadata-only.
- Verifier reports should be rendered in full detail views, but frontmatter
  should keep only a short summary / hash pointer.
- The first Lean reward domain should be whichever existing Rethlas topic has a
  stable informal library and a pinned Mathlib dependency boundary; do not tie
  rewards to a brand-new domain before the build pipeline is ready.
