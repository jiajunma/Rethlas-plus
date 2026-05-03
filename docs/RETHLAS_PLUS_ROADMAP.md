# Rethlas-plus Roadmap and Phase Boundaries

Date: 2026-05-02

This note connects the current Phase I/II implementation plans with the
Rethlas-plus design discussion. It is a phase-boundary document, not a
replacement for the detailed milestone plans.

The main rule is:

```text
Each phase should remove one bottleneck. Do not make a later-phase risk a
prerequisite for an earlier-phase system.
```

In particular, proof-search improvements should not require a blockchain, and
the core exploration loop should not require Lean.

## Existing Documents

- `docs/PHASE1.md`: current Phase I implementation plan.
- `docs/PHASE2.md`: current Phase II dashboard/proof-tree plan.
- `docs/PHASE2_RETHLAS_PLUS.md`: proposed Rethlas-plus node-library/search
  phase.
- `docs/PHASE3_LEARNER_REFEREE.md`: source ingestion, learner/referee, and
  review workflow.
- `docs/RETHLAS_PLUS_SYSTEM_DESIGN.md`: concrete harness-first Phase II-B
  system shape.
- `docs/PHASE5.md`: blockchain, token, formalization-market, and decentralized
  artifact-cache ideas.

## Phase Map

| Phase | Working name | First-class object | Problem it solves | Explicit non-goal |
| --- | --- | --- | --- | --- |
| Phase 0 | Research loop / design baseline | Scratch mathematical memory and branch notes | Make the original agent workflow useful for exploratory proof work before there is a durable product runtime | Persistent runtime correctness, Kuzu projection, production scheduling |
| Phase I | Correct local runtime | Event journal plus projected KB | Make one local workspace recoverable, auditable, rebuildable, lintable, and able to run generator/verifier workers under supervision | Search quality, multiple proof candidates for one statement, Lean, blockchain |
| Phase II-A | Proof-state observability | Dashboard proof tree / graph view | Let an operator see theorem dependency state, verifier status, shared lemmas, and live changes | New truth semantics, new scheduler behavior, branch search |
| Phase II-B | Node-first library and search | Markdown node documents under `knowledge_base/nodes/<topic_path>/...` | Fix path-dependent proof search by allowing multiple proof branches, goal-directed verification, reusable branch lemmas, KB snapshot/import/merge, hash-based invalidation, bounded loop guards, and promotion of the first verified route | Token economics, decentralized verification, mandatory Lean, proving every library node before stopping |
| Phase III | Source-to-KB extraction and review | Article/book corpus plus review queue | Turn PDFs/books/papers, including scanned PDFs and matching TeX projects when available, into node documents; repair missing proof steps when possible; retrieve external citations; flag unrepaired gaps explicitly | Formal kernel certainty, public incentives |
| Phase IV | Formalization bridge | Formal artifacts linked from nodes | Connect valuable informal nodes to Lean/Coq/Isabelle statements, proofs, pinned toolchains, and local formal build/cache workflows | Chain rewards, public staking, token issuance |
| Phase V | Decentralized verification and incentives | Snapshot roots, attestations, bounties, artifact manifests | Add provenance, external verifier incentives, maintenance bounties, and DHT/IPFS-style Lean artifact caches | Replacing the node library, replacing Kuzu, deciding mathematical truth directly on-chain |

The names `Phase II-A` and `Phase II-B` are intentional. The current
`docs/PHASE2.md` is dashboard-only. The Rethlas-plus proposal is a different
kind of Phase II work: it changes the knowledge model and scheduler/search
model. It may become Phase II-B or Phase II.5 in implementation, but it should
not be confused with the read-only dashboard plan.

## Phase 0: Research Loop / Design Baseline

Phase 0 is the pre-product workflow: generator/verifier prompts, scratch
memories, branch notes, counterexamples, failed paths, immediate conclusions,
and hand-managed research state.

It solves:

- preserving mathematical exploration instead of losing every failed route;
- discovering useful proof decompositions before the system has a durable
  theorem library;
- building real examples, such as the induced-orbit problem, that expose
  product requirements.

It does not solve:

- deterministic replay;
- scheduler correctness;
- workspace recovery after crashes;
- database projection;
- user-facing theorem-library structure.

The main lesson carried into later phases is that failed branches and verified
lemmas must both be preserved. Otherwise the system repeats old mistakes or
overfits to one proof route.

## Phase I: Correct Local Runtime

Phase I's core problem is not mathematical intelligence. It is product
correctness.

It solves:

- workspace initialization;
- append-only truth events;
- deterministic projection into Kuzu and rendered nodes;
- generator/verifier worker lifecycle;
- coordinator supervision;
- read-only dashboard endpoints;
- rebuild after crash or clone;
- linter checks for Phase I invariants.

The correct first-class object in Phase I is the event journal, because the
system needs a safe, replayable substrate before it can support richer editing
or search.

Phase I deliberately does not solve the Rethlas-plus search problem:

- one statement still effectively has one current proof slot;
- failed proof repair is biased toward the previous direction;
- the coordinator avoids same-label concurrent work;
- dashboard visibility is limited.

That is acceptable for Phase I. It is the foundation, not the search engine.

## Phase II-A: Proof-State Observability

The current `docs/PHASE2.md` is a dashboard phase.

It solves:

- `/api/tree` for theorem-rooted dependency outlines;
- live status updates via SSE;
- per-node drilldown;
- shared-dependency hints;
- later, a true DAG graph view in Phase II.5/M13.

It should remain read-only and should not introduce new truth-event types or
projector changes. This keeps Phase II-A low risk: it makes the current system
understandable without changing what the system means.

Phase II-A is useful before Phase II-B because tree search needs observability.
If multiple proof branches are active, operators need to see which branches are
growing, which are blocked, and which verified lemmas are being reused.

Phase II-A should therefore evolve into a search-observability dashboard, not
just a runtime dashboard. It should add a goal board, branch frontier, MCTS
stats, verifier queue, and a goal-local graph/hypertree projection. Goals,
branches, and nodes should expand on click with lazy-loaded neighborhoods, and
the UI should automatically reveal the path to currently running generator or
verifier jobs. The first version should stay read-only; controls such as pause,
abandon, and promote can be added later as admitted search events.

## Phase II-B: Node-first Library and Search

Phase II-B is the Rethlas-plus change that addresses the user's core complaint:
the proof path is fixed too early, and repair stays close to the original proof.

It solves:

- multiple proof candidates for the same root statement;
- branch-scoped helper lemmas;
- preservation of verified theorems and lemmas from abandoned branches;
- topic-organized node files with YAML frontmatter and Markdown content;
- Kuzu as a graph/index representation of the same node library;
- hash-based invalidation for statements, proofs, dependency statements, and
  verification results;
- explicit goal sets and stop conditions;
- verifier scheduling that proves ready goal-relevant nodes before expanding
  more branches;
- loop guards for repair spinning, duplicate selector actions, repeated failure
  signatures, stale publishing jobs, and branch budget exhaustion;
- selector-controlled `repair`, `expand`, `spawn_sibling`, `abandon`, and
  `promote` actions;
- promotion of the first verified compatible candidate to the canonical target.

The first-class object should change from event to node document:

```text
NodeDocument = one theorem/lemma/definition/candidate as one md file
```

Events remain important as a journal and audit trail, but the object that users
and agents reason about should be the node.

The search policy should start pragmatic:

```text
MCTS-guided best-first beam search + verifier-shaped reward
```

MCTS should be introduced as the selector policy in Phase II-B, but it should
not own truth state. It chooses which branch/action to expand next; the
scheduler still enforces goals, hashes, verifier readiness, promotion, and
loop guards.

The run should stop when all required goals are closed verified. A separate
library-sweep mode may continue verifying unrelated nodes, but that should not
be the default proof-search stop condition.

Phase II-B should convert repeated no-progress patterns into explicit branch
or run states such as `stuck`, `abandoned`, `exhausted`, `needs_user`, or
`budget_exhausted`. The current Phase I model surfaces many of these states in
Human Attention but keeps dispatching; tree search needs harder circuit
breakers to avoid turning one stuck proof route into many stuck branches.

The agent boundary should be narrow: selector/policy-value, generator,
informal verifier, failure analyst, and promotion checker may be LLM agents.
Librarian projection, hash invalidation, Kuzu sync, MCTS bookkeeping, goal
stop conditions, and budget enforcement should remain deterministic services.
Concretely, Rethlas should own the MCTS controller and Codex should serve as
the policy/value oracle that receives curated context and returns priors,
values, action recommendations, and mathematical rationale.

This is a harness-first architecture: Rethlas prepares context, legal actions,
prompts, schemas, replay, hashes, budgets, and admission checks around Codex.
Codex supplies the improving mathematical reasoning layer.

Phase II-B should also reserve source-provenance fields on node documents and
Kuzu indexes so later learner-created nodes can point to source spans and
source artifacts. Phase II-B should not implement PDF/TeX parsing; it only
defines where provenance lives and ensures provenance metadata does not affect
mathematical verification hashes.

Verification in Phase II-B should be node-kind-aware. Definitions and external
theorems are not automatically verified just because they have empty proof
text. Definitions need well-formedness and dependency checks; external theorem
records need source/citation checks; theorem/lemma/proposition nodes need proof
checks.

Phase II-B should also support merging two knowledge bases. The merge object is
a snapshot manifest plus node/source documents, not a Kuzu file. The first
implementation should support federated read-only indexing and planned deep
import through librarian admission. Duplicate statements with different proofs
should be preserved as aliases or proof variants.

Merge should be staged before deduplication: first index or copy the remote KB
into a federated/staging namespace, then run exact-hash, normalized-statement,
dependency-aware, and semantic duplicate checks before admitting canonical
local nodes.

## Phase III: Source-to-KB Extraction and Review

Phase III is the bridge from theorem-library search to source ingestion. It
turns articles and books into node documents and reviews them for logical
gaps.

It solves:

- extract claims, theorem statements, and proof skeletons from source text;
- read PDFs through a source-artifact pipeline, including OCR for scanned pages;
- read TeX projects when available, including theorem environments, labels,
  refs, citations, macros, and bibliography keys;
- align PDF spans to TeX spans when both artifacts are available;
- build a provenance graph from source spans to KB nodes;
- review jump steps and missing details in informal proofs;
- review hidden hypotheses, notation drift, citation applicability, source
  extraction quality, edge cases, and counterexample attempts;
- use generator/verifier to repair the missing logical chain when possible;
- retrieve external references and citation evidence for review;
- flag unrepaired gaps explicitly instead of silently accepting them;
- keep referee reports, requested details, evidence, and repair attempts in a
  separate review workspace so review work does not pollute the node library;
- keep unresolved-reference and source-claim indexes queryable for later work.

This phase should use two top-level agent roles:

- `learner`: extract candidate KB nodes, normalize statements, build proof
  skeletons, ask generator/verifier to fill missing steps, and grow the
  searchable theorem/lemma library used by later proof search;
- `referee`: review extracted or written claims, verify logical continuity,
  search external references, and emit explicit gap reports when repair fails.

Generator and verifier remain inner tools, not the top-level role boundary.
Retrieval is a harness service. The referee can ask for citations and source
matches, but it should not own the citation index itself.
Generator consumes learner output through the admitted KB and retrieval
indexes, not through raw source documents or learner scratch output.

The agent split is part of the architecture:

- learner produces KB import artifacts;
- referee produces correctness-review artifacts;
- learner may hand suspicious claims, unresolved jumps, and citation-dependent
  claims to referee;
- referee may emit verified repair lemmas or recommended KB updates, but those
  still go through librarian admission;
- neither agent directly changes durable truth state.

Implementation should treat both as independent roles with their own job
types, schemas, logs, budgets, and scheduler lanes. They are not generator
modes. They call generator/verifier through the harness when proof completion
or checking is needed.

The learner/referee skills should own the Codex-facing workflows, schemas, and
prompt discipline. Fragile source parsing such as PDF rendering, OCR, TeX
project parsing, PDF-TeX alignment, and hashing should be implemented as
skill-bundled scripts or shared harness services, then surfaced to Codex as
structured spans and references.

Implementation order should be source-first:

- source artifact pipeline: PDF/text/OCR/TeX/layout extraction, PDF-TeX
  alignment, and span records;
- job-v2 envelope for source-oriented roles;
- learner role, decoder, `learner.batch_proposed`, and librarian admission;
- Kuzu provenance edges from source spans to nodes;
- referee role, review reports, citation checks, and external-reference
  evidence;
- separate review workspace for referee reports, issues, requested details,
  evidence, and repair attempts;
- scheduler request queues for bridge generation, verification, citation
  retrieval, and manual checks;
- dashboard source/provenance/review views.

The reward target is different for the two modes:

- `learner` is rewarded for coverage, structured extraction, and correct node
  decomposition;
- `referee` is rewarded for soundness, citation fidelity, and honest gap
  reporting.

Phase III should not be required for the first tree-search implementation.
Phase II-B can begin with branch metadata and simple scoring. Phase III comes
after the node library is useful enough that there is something worth
extracting or reviewing.

## Phase IV: Formalization Bridge

Phase IV is the local, non-chain formalization layer.

It solves:

- linking informal nodes to formal statements;
- recording Lean/Coq/Isabelle module names and theorem names;
- pinning Lean version, mathlib commit, Lake manifest hash, and build options;
- checking valuable nodes with a formal kernel when the required library exists;
- maintaining local formal artifact manifests and build caches;
- formalization-aware KB merge: deciding whether imported formal artifacts,
  theorem names, module paths, and dependency closures are portable under the
  local pinned toolchain;
- tracking porting status when Lean/Mathlib/library versions evolve.

This phase should not be tokenized at first. The goal is to make formalization
technically correct before adding public economic incentives.

Lean is a settlement layer, not the core exploration engine. Many research
targets will not have enough formal library support on day one.

Phase IV should not be the first place where KB merge exists. Basic
Markdown/YAML node/source merge belongs in Phase II-B. Phase IV upgrades that
merge with formal semantics:

- map informal nodes to formal declarations;
- compare formal statement hashes under pinned environments;
- import or reject `.lean` modules and formal proof artifacts;
- decide whether remote formal verification can be accepted locally;
- mark artifacts as `checked_current`, `historically_checked`, `needs_port`,
  `port_in_progress`, `ported`, or `deprecated`;
- preserve old checked artifacts when the formal library evolves instead of
  mutating them in place.

## Phase V: Decentralized Verification and Incentives

Phase V is where blockchain belongs.

It solves:

- timestamping library snapshots;
- anchoring Merkle roots for normalized node libraries and dependency closures;
- recording verifier attestations;
- creating bounties for proof, formalization, counterexamples, and maintenance;
- distributing Lean artifacts such as `.olean`, `.ilean`, `.c`, and native
  objects through a content-addressed cache;
- rewarding useful formal library construction.

It must not claim that the chain decides truth. The chain can say:

```text
Verifier X attested verification_hash H under environment E.
```

For high confidence, the source of mathematical settlement is still a formal
kernel plus pinned dependencies.

## Dependency Logic

The phase order matters:

1. Phase I is needed before serious search because branch exploration needs
   durable state, verifier records, and rebuild semantics.
2. Phase II-A is useful before or alongside Phase II-B because tree search is
   otherwise invisible.
3. Phase II-B is needed before blockchain because there must be a real theorem
   library to anchor, reward, and maintain.
4. Phase IV should precede serious Phase V economics because formal artifacts
   and pinned build environments define what high-confidence rewards mean.
5. Phase V should remain optional. A strong local Rethlas-plus should work
   without a chain.

## What Each Phase Should Be Judged By

| Phase | Success test |
| --- | --- |
| Phase 0 | Does the workflow preserve useful mathematical ideas, counterexamples, and failed paths? |
| Phase I | Can the workspace recover deterministically and pass invariant checks after crashes, restarts, and rebuilds? |
| Phase II-A | Can a user see the live proof/dependency state without reading raw events or logs? |
| Phase II-B | Can the system avoid getting stuck on one proof direction, verify the nodes that unblock active goals, stop when all goals are closed verified, and keep verified branch lemmas forever? |
| Phase III | Does a larger library make proof search better instead of noisier? |
| Phase IV | Can selected valuable nodes be checked against pinned formal toolchains? |
| Phase V | Can outside participants verify, maintain, and cache formal library work with fair incentives and auditable provenance? |

## Practical Recommendation

Implement the near-term roadmap in this order:

1. Finish Phase I correctness gates.
2. Ship Phase II-A enough to observe proof state.
3. Implement Phase II-B node schema, recursive node layout, Kuzu library index,
   branch metadata, selector, and promotion.
4. Add Phase III source-to-KB extraction and review once the node library is
   rich enough to justify source ingestion.
5. Build Phase IV formalization metadata and local Lean artifact manifests.
6. Keep Phase V as a separate design track until the local library and formal
   artifact model are stable.
