# Proof + Verification on the KB — Design

Date: 2026-06-02
Status: design (no code yet)
Related: [mdblueprint#159](https://github.com/gametheoryinlean/mdblueprint/issues/159) (multi-candidate subdir layout)

This document defines how Rethlas-plus drives proof + verification on top of the
mdblueprint knowledge base. It captures six design decisions and the resulting
agent / event / storage shape.

It deliberately stays inside the Rethlas pattern from `docs/ARCHITECTURE.md`:
append-only event journal, librarian admission gate, deterministic Python
coordinator, LLM workers that never write durable truth directly.

## Non-goals

- Lean / formal-kernel verification (that is Phase IV).
- MCTS or learned policy (rules-based selector first).
- Decentralized verification / incentives (Phase V).
- Rewriting mdblueprint. mdblueprint is the durable KB. This document specifies
  how Rethlas writes into it via librarian.

## Five user constraints (from 2026-06-02)

1. `plan` is a standalone md file.
2. Each agent does one simple thing per call; pipeline is split, not monolithic.
3. `counterexample` is a first-class artifact.
4. Workspace (transient agent state) is physically separated from KB (durable
   truth).
5. Each agent has its own role; no cross-cutting "super agent".

## 1. Physical layering — workspace ↔ KB isolation

```text
<rethlas-workspace>/                          ← Rethlas repo, transient state
  rethlas.toml
  events/                                     ← append-only truth journal
    2026-06-02/<ts>--<type>--<target>--<actor>--<seq>--<uid>.json
  runtime/
    state/{coordinator,librarian}.json
    jobs/{plan,decompose,prove,verify,referee,triage,promote}-<id>.json
    logs/
  workspace/                                  ← agent drafts, NEVER part of KB
    drafts/
      plan-A/{scratch.md, decompose.json}
      lemma-X/{proof-attempt-1.md, proof-attempt-2.md}
    abandoned/                                ← failed attempts (anti-hint)
    pending-cex/                              ← referee-proposed CEXs awaiting witness check
  agents/                                     ← one Codex working dir per role
    planner/      AGENTS.md + skills/
    decomposer/   ...
    prover/       ...
    verifier/     ...
    referee/      ...
    triage/       ...
    promoter/     ...

<kb-submodule>/                               ← mdblueprint, durable truth
  docs/knowledge/
    nodes/
      <topic>/
        <canonical>/                          ← multi-candidate dir layout (issue #159)
          canonical.md                        ← statement + promoted_plan: <slug>
          plans/plan-A.md plan-B.md
          candidates/
            plan-A/
              proof.md                        ← status: abandoned | promoted
              helpers/lem-foo.md              ← plan-local helper
            plan-B/
              proof.md
              helpers/lem-bar.md
        lem-shared.md                         ← helper promoted to topic level
    counterexamples/cex-<id>.md
    reviews/<canonical>/{plan-<X>-abandon-<ts>.md, plan-<Y>-promote-<ts>.md}
```

### Boundary rules

- **LLM agents may only write under** `events/` and `workspace/drafts/`.
- **Librarian (deterministic Python) is the only writer to** `<kb-submodule>/`.
  It does so by calling `mdblueprint.tools.knowledge.admit.admit_node` (and
  new `promote_candidate`, `abandon_candidate` helpers from issue #159) on
  admittable events.
- Failed `proof_attempt`s land in `workspace/abandoned/` until librarian
  decides whether the abandoned candidate is worth preserving in KB as
  anti-hint (status: abandoned + reason). In-progress drafts never touch KB.
- KB stays a clean record of mathematics. Workspace stays a clean record of
  compute. The journal in `events/` is the only bridge.

## 2. Independent agent roles

Every LLM agent below follows the existing Rethlas role pattern: separate
`AGENTS.md`, separate Codex skill, separate prompt contract, separate decoder,
separate job kind, separate scheduler lane, structured JSON output.

| Agent | Input | Output (events/) | Decides | Affects durable truth? |
|---|---|---|---|---|
| **planner** | canonical node, existing plans summary, known counterexamples, prior-plan anti-hints | `plan.proposed` | one new strategy + subgoal list | no — proposal only |
| **decomposer** | one `plan.md` | `decomposition.proposed` (batch of lemma/def statement skeletons) | how to break the plan into named lemmas | no — proposal only |
| **prover** | one lemma skeleton + permitted premise list | `proof_attempt.drafted` | proof body for one lemma | no — proposal only |
| **verifier** | one node (statement + proof + premise refs) | `verifier.run_completed` with verdict ∈ {accepted, gap, critical}, report, repair_hint | did this single node pass | **yes** (informal verdict) |
| **referee** | a plan, a complete proof chain, or a single statement | `referee.review_completed`, optionally `counterexample.proposed` | is the path viable; is there a refuting witness | **yes** for counterexamples |
| **triage** | failed candidate history, repair_count, latest verifier feedback | `plan_pivot.suggested` or `repair.requested` | repair / spawn sibling / give up (soft trigger) | no — suggestion only |
| **promoter** | a verified helper + cross-plan usage stats | `helper_promotion.proposed` | should a helper move from plan-local to topic shared | no — proposal only |
| **librarian** *(Python)* | every `*.proposed` event | `*.admitted` or entry in `rejected_writes.jsonl` | admission gate to durable KB | **yes** — sole writer |
| **coordinator** *(Python)* | global state | dispatch | scheduling | no |

### Why split decomposer / prover

A monolithic "produce lemmas + their proofs in one Codex call" is cheaper per
turn but wastes prover tokens on lemmas that won't survive the verifier or
that turn out to be plan-incompatible. Splitting lets the verifier short-circuit
plan-A as soon as one structurally-necessary lemma fails, before any prover
work is spent on the remaining lemmas.

### Why split planner / triage

Producing a new strategy requires forward-looking creative output. Deciding
"this strategy is dead, switch" requires backward-looking evidence aggregation.
Mixing them in one prompt blurs the rubric and makes failure analysis harder
to audit.

## 3. Event types (extends Rethlas Phase I/III)

```text
plan.proposed                   ← planner
plan.admitted                   ← librarian
decomposition.proposed          ← decomposer
decomposition.admitted          ← librarian
proof_attempt.drafted           ← prover
proof_attempt.admitted          ← librarian
verifier.run_completed          ← verifier  (Phase I, unchanged)
referee.review_completed        ← referee   (Phase III, extended)
counterexample.proposed         ← referee
counterexample.admitted         ← librarian (after verifier checks the witness)
plan_pivot.suggested            ← triage
repair.requested                ← triage
helper_promotion.proposed       ← promoter
helper_promotion.admitted       ← librarian
plan.promoted                   ← librarian (canonical points at new plan)
plan.abandoned                  ← librarian (a plan terminates)
```

All `.admitted` events are librarian-only. LLM agents only produce `.proposed`,
`.drafted`, `.suggested`, `.run_completed`, or `.review_completed`.

## 4. Plan and proof split

`plan.md` and `candidates/<plan-slug>/proof.md` are two separate files bound
by a `plan_id` frontmatter field.

```yaml
# nodes/<topic>/<canonical>/plans/plan-A.md
---
id: <canonical>.plan-A
title: <canonical> — plan A (Brouwer fixed point)
kind: proof-plan
status: candidate | promoted | abandoned
plan_for: <canonical>
plan_slug: plan-A
strategy: |
  Reduce to a fixed-point statement about the best-response correspondence,
  apply Brouwer on the simplex of mixed strategies.
subgoals:
  - id: lem-br-continuous
    statement: "The best-response correspondence is continuous."
  - id: lem-domain-compact
    statement: "Δ(S) is non-empty compact convex."
  - id: lem-brouwer-applies
    statement: "A continuous self-map of Δ(S) has a fixed point."
uses_counterexamples: []       # known CEXs this plan must not contradict
abandoned_reason: null
---

# Plan A — Brouwer fixed point route

(Plan rationale, references, why this is plausible.)
```

```yaml
# nodes/<topic>/<canonical>/candidates/plan-A/proof.md
---
id: <canonical>.plan-A.proof
title: <canonical> — proof along plan A
kind: theorem
status: candidate | promoted | abandoned
candidate_of: <canonical>
follows_plan: <canonical>.plan-A
uses:
  - <canonical>.plan-A
  - <canonical>.plan-A.lem-br-continuous
  - <canonical>.plan-A.lem-domain-compact
  - <canonical>.plan-A.lem-brouwer-applies
verification:
  statement: accepted
  proof: pending | accepted | gap | critical
---

# Proof of <canonical> via plan A

(Body of the proof, referencing each subgoal lemma by id.)
```

The plan file is light (referee-friendly). The proof file is heavy (verifier
target). Promotion (`plan.promoted`) moves the canonical's `promoted_plan`
pointer to a new slug; both the plan and the proof flip to `status: promoted`
atomically via librarian.

## 5. Counterexample lifecycle

```text
referee reviewing lemma_3 finds witness w refuting lemma_3's statement
  -> events/referee.review_completed (verdict: refuted)
  -> events/counterexample.proposed
       payload: {statement, witness, derivation_sketch, source_lemma}

librarian receives counterexample.proposed:
  -> dispatches verifier job (kind: counterexample)
       verifier validates that witness genuinely refutes statement
  -> on accepted: counterexample.admitted
       -> KB: counterexamples/cex-<id>.md (kind: counterexample, status: admitted)
       -> plan.abandoned for every plan whose subgoal closure intersects this statement
  -> on rejected: counterexample stays in workspace/pending-cex/ for referee revision
```

Counterexamples are durable negative evidence, valid across all plans. Future
planner prompts include the admitted CEX set; the planner is forbidden from
proposing a plan whose subgoals contradict an admitted CEX. Librarian enforces
this at admission: `plan.proposed` whose subgoal closure conflicts with an
admitted CEX is sent to `rejected_writes.jsonl` and the planner is asked
again with the conflict spelled out.

## 6. Trigger mechanics for replan

Two kinds:

- **Hard triggers** (deterministic, coordinator-level Python):
  - `repair_count > 2` on a lemma (current Rethlas `_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT`)
  - admitted counterexample lands on a subgoal in the current plan
  - dependency cycle detected during decomposition
  - per-plan compute budget exhausted
- **Soft triggers** (LLM judgement, no automatic truth change):
  - `triage` emits `plan_pivot.suggested` based on aggregate evidence
  - operator manually requests a new plan from the dashboard

Coordinator on hard trigger: emit `plan.abandoned` for the dead plan,
schedule a fresh `planner` job with the failure context as anti-hint.
Soft trigger: emit `plan_pivot.suggested` and wait for operator approval
or a `Human Attention` clear.

## 7. Anti-hint mechanism

Future planner / decomposer / prover calls do not start blind. Librarian
assembles a structured anti-hint packet from prior events:

- All admitted CEXs (statement + witness reference).
- All abandoned plans for the same canonical: their strategy summary,
  which lemma killed them, the verifier's `repair_hint`.
- All `repair.requested` events still open: the prior `verification_report`.

The anti-hint packet is **assembled by deterministic Python**, not by an LLM.
This keeps the planner from re-running through the failure modes the system
has already mapped, without giving any LLM the power to choose which prior
attempts to remember.

## 8. End-to-end walkthrough — prove `thm:T`

```text
0. operator: rethlas prove --canonical thm:T
1. coordinator: thm:T has no plan -> dispatch planner job
2. planner -> plan.proposed (plan-A: Brouwer route)
3. librarian admit -> KB writes nodes/.../T/plans/plan-A.md
4. coordinator: plan-A has 5 subgoals -> dispatch decomposer
5. decomposer -> decomposition.proposed (5 lemma statements)
6. librarian admit -> KB writes 5 files under candidates/plan-A/helpers/
   (status: candidate, verification.proof: pending)
7. coordinator: 5 prover jobs in parallel (no concurrent same-lemma)
8. prover[i] -> proof_attempt.drafted (lemma_i body, in workspace/drafts/)
9. librarian admit -> KB updates lemma_i.md proof body
10. coordinator: 5 verifier jobs
11. verifier results: lemmas 1,2,4,5 accepted; lemma_3 critical
12. coordinator: lemma_3 repair_count < cap -> dispatch prover (mode=repair)
13. verifier again critical, repair_count = cap -> HARD TRIGGER
14. coordinator: emit plan.abandoned(plan-A), schedule planner with anti-hint
15. planner -> plan.proposed (plan-B: Kakutani route, avoids lemma_3 form)
16. librarian: check plan-B subgoals against admitted CEXs -> ok -> admit
17. ... decomposer / prover / verifier for plan-B ...
18. plan-B all green -> referee reviews full proof chain
19. referee -> referee.review_completed (verdict: ok)
20. librarian -> plan.promoted (plan-B), canonical's promoted_plan = plan-B
21. promoter: lemma_1 is used by both plan-A and plan-B -> helper_promotion.proposed
22. librarian admit -> lemma_1 moves from candidates/plan-A/helpers/ to
    nodes/<topic>/lem_1.md (topic-shared)
23. plan-A's proof.md stays in KB with status: abandoned + reason
    (durable anti-hint for future planners)
```

## 9. Single-canonical-single-plan still works

Step 0–9 of the workflow are identical for an easy theorem that proves
on the first plan. canonical.md may stay as a single file with no
`candidates/` subdir until a second plan is spawned (matching the
migration story in issue #159 §"Migration").

## 10. Open questions for review

1. Promotion threshold for `helper_promotion.proposed`: pure rule (used by ≥ N
   plans) vs LLM judgement (promoter agent reads the lemma and decides) vs
   hybrid. This document picks **promoter LLM + librarian admission gate**
   to honour "each agent does one thing". A pure-rule option remains open
   if the LLM's signal turns out noisy.
2. Should the `plan.abandoned` proof body stay in KB indefinitely, or only
   for N most-recent abandoned plans per canonical? Disk + KB clutter vs
   completeness of anti-hint history.
3. Whether `referee` should be allowed to short-circuit a `plan` before
   decomposer runs (read the plan, declare it dead before any prover work).
   This is a free quality win but adds a coordinator-side conditional.
4. CEX format: full witness + derivation, or just statement + reference to a
   theorem that contradicts it. Latter is cheaper but less self-contained.

## 11. Out of scope

- Selector improvements (MCTS, learned priors): Phase II-B-2 or later.
- Lean alignment: Phase IV.
- Chain anchoring of CEXs and proofs: Phase V.
- Cross-canonical branching ("what if the definition were different"): future.

## 12. Implementation order

1. Land mdblueprint#159 (canonical/candidate dir layout, schema, validator).
2. Add `plan-proof` and `counterexample` node kinds to mdblueprint
   (frontmatter + validator + admit hooks).
3. New Rethlas event types + librarian admission rules.
4. `planner` and `decomposer` agents (smallest new surface area).
5. Extend coordinator for the new lanes; reuse existing prover/verifier.
6. `referee` extension for CEX emission.
7. `triage` and `promoter` agents.
8. Dashboard plan / candidate / CEX views.

Each numbered step is a separate PR. The pipeline degrades gracefully: even
if only steps 1–4 ship, you get one-plan-per-theorem with explicit plans, and
the rest of the existing Rethlas loop keeps working.

## 13. Addendum (2026-06-02) — KB-grounded goal-directed proving via skill-toolbox agents

This addendum revises §2 after studying two reference systems:
`~/mycodes/QED` (a decomposer → single-prover → verifier → regulator pipeline)
and `~/mycodes/Rethlas-original` (two Codex agents — *generation* and
*verification* — each a **skill toolbox** driven by an adaptive control loop).

We adopt the **Rethlas-original** structure, and we **drop the external-literature
("sources") path entirely**: on the KB, the only admissible basis is the set of
already-verified nodes. There is no arXiv / literature retrieval and no `sources`
section in any plan.

### 13.1 Control model — skill toolbox + adaptive loop (revises §2)

§2 split planning, decomposition, and proving into separate agents. We instead
follow Rethlas-original: **the unit of work is one staged Goal node**, handled by
a single *generation (prover) agent* whose internal control loop chooses among
skills adaptively (no fixed decompose → prove → verify order).

This is **not** the cross-cutting "super agent" forbidden in §2 — its scope is
exactly one Goal node. Decomposition, direct proving, recursive proving,
counterexample construction, and failure analysis become **skills**, not separate
durable-truth writers. (Durable truth is still librarian-only; see §13.4.)

The "证不出来退回 decomposer" behaviour is **automated recovery inside one Goal**
and is just an arc of the generation agent's adaptive loop — no extra agent for
that part:

```text
direct-proving fails → recursive-proving fails → identify-key-failures
  → propose-subgoal-decomposition-plans (new generation of plans)
```

What *does* need its own agent is the **continue-vs-stop decision**. A separate
**regulator** agent owns it: given accumulated evidence (repeated failures,
`repair_count`, per-Goal budget, whether recent rounds produced any *fresh*
progress), it decides either to **continue** (let the adaptive loop run another
generation) or to **stop and escalate to the user for help** (raise Human
Attention and pause the Goal). The regulator does **no proving** and writes no
proof content — its one job is the continue / stop-and-ask-human call. It is the
soft-judgement complement to the deterministic §6 hard triggers (coordinator
Python): hard triggers fire mechanically (`repair_count > cap`, budget exhausted,
admitted CEX on a subgoal); the regulator judges whether more autonomous effort is
worthwhile or the human should be pulled in. (This is QED's regulator narrowed:
its REVISE_*/FINAL branches collapse to "continue" vs "stop-and-ask-human",
because the *how* of continuing is now the generation agent's adaptive loop.)

### 13.2 Node states — grounds "staged" / "verified" in §5.4 `pass_count`

- **staged** — statement admitted, proof pending: `pass_count ≤ 0`
  (`-1` = no proof drafted yet; `0` = proof drafted, unverified). A staged node is
  either the Goal or an as-yet-unproven subgoal.
- **verified** — `pass_count ≥ 1`; the only nodes written to
  `knowledge_base/nodes/*.md`; the sole admissible basis.
- A **Goal** is a staged node selected by the coordinator (a §6 hard/soft trigger,
  or an operator pick from the dashboard).

### 13.3 Two agents, each a skill toolbox

**Generation (prover) agent** — dispatched with `Goal = one staged node`:

| skill | from Rethlas-original | KB-version change |
|---|---|---|
| `query-kb` | replaces `search-math-results` | **only basis source** (verified nodes, no literature). KB-search **skill**: composes lexical `kb_grep` (甲) + dependency-graph walk (丙) + knowledge-driven label-guessing (`kb_node`), LLM judges relevance. No embeddings, no ranking service (§13.4). |
| `propose-subgoal-decomposition-plans` | kept | subgoals = candidate new staged nodes |
| `direct-proving` | kept | premises restricted to verified nodes |
| `recursive-proving` | kept | one sub-prover per plan; surviving subgoals land as staged nodes |
| `identify-key-failures` | kept | the "退回 decomposer" arc → regenerate plans |
| `construct-counterexamples` | kept | feeds the §5 counterexample lifecycle |
| `construct-toy-examples`, `obtain-immediate-conclusions` | kept | workspace-only scratch |
| `verify-proof` | kept | calls the verification agent |
| ~~`search-math-results`~~ | **dropped** | no external literature |

**Verification agent:**

| skill | KB-version meaning |
|---|---|
| `check-referenced-statements` | hardened: confirm every cited premise is a verified KB node → enforces "basis = verified" |
| `verify-sequential-statements` | step-by-step logical check |
| `synthesize-verification-report` | verdict ∈ {accepted, gap, critical} + `repair_hint` |

**Regulator agent** — the continue-vs-stop gate (one decision, no proving):

| skill | meaning |
|---|---|
| `assess-progress` | read the Goal's failure history, `repair_count`, per-Goal budget, and a fresh-progress signal (did the last rounds add anything new?) |
| `decide-continue-or-escalate` | emit `continue` (run the adaptive loop another generation) **or** `stop` (raise Human Attention, pause the Goal, hand to the user) |

This is the §2 `triage` role, narrowed to the continue / stop-and-ask-human
judgement. It reads KB / workspace state through the same read MCP tools (§13.4)
and emits its decision as an event; it never writes proof content.

### 13.4 Storage is markdown in the filesystem; all KB I/O is MCP-mediated (hard constraints)

Two hard constraints:

1. **Kuzu is removed entirely** — **no `dag.kz`, no graph database** anywhere.
   This supersedes the §4.1/§5 Kuzu-backed projection.

   **The knowledge base *is* exactly the markdown math nodes** — the durable body
   of mathematics, one file per node: `label`, `kind`, `pass_count`,
   `statement_hash`, `verification_hash`, `depends_on` (frontmatter) plus the
   `statement` / `proof` / `remark` / `source_note` body (the mdblueprint model of
   §1; `pass_count` per ARCHITECTURE §5.4 `nodes/*.md`). **Nothing else is the
   KB.** Agents read these via the read MCP tools.

   Two supporting stores live in the **workspace, not the KB** (§1 boundary):
   - **`events/`** — the append-only truth journal + audit + rebuild source.
   - **in-memory operational projection** — rebuilt by replaying `events/` at
     startup (no DB, no persistence). Holds the runtime fields markdown does not
     carry: `repair_count`, `verification_report`, `repair_hint`,
     `introduced_by_actor`. Used by coordinator / regulator / §6 triggers; the
     latest `verification_report` / `repair_hint` reach the prover via job
     dispatch, not markdown.

   Every former Kuzu read path (`common/kb/kuzu_backend.py`,
   `common/kb/interface.py` `KBReader`, `librarian/query_server.py`, the
   projector's Kuzu writes) is replaced by reading the markdown KB (math) or the
   workspace operational projection (runtime fields).
2. **Agents reach the KB only through MCP tools** — never by reading or writing
   those markdown files directly. MCP mediation is what guarantees the KB contract
   (frontmatter schema, label rules, dependency well-formedness,
   premise-is-verified, CEX-conflict) holds on **every** read and write.

This extends the existing FastMCP `reasoning-agent` server
(`agents/generation/mcp/server.py`, today exposing the transient `memory_*` /
`branch_update` workspace tools) with a durable-KB surface. Two distinct MCP
surfaces, matching the §1 workspace ↔ KB boundary:

- **Workspace memory MCP** (existing, transient): `memory_init`, `memory_append`,
  `memory_search`, `branch_update`. Channels `failed_paths`, `toy_examples`,
  `branch_states`, … — never durable truth. **All run-related logs and runtime
  artifacts live only in the workspace**, mirroring Rethlas-original's per-problem
  `memory/{id}/` + `logs/`: the per-Goal reasoning trace, agent run logs, and
  verifier reports-as-logs stay workspace-local and never enter the KB. (The
  in-memory operational projection of §13.4 constraint 1 is rebuilt from the
  workspace `events/`; it is not a separate persisted store.)
- **Durable KB MCP** (new): reads/writes the markdown KB; agents see only
  contract-conformant projections.

**Read tools are dumb primitives** — there is **no ranking service**. They parse
the markdown KB through a shared parser/projector (the same code that enforces
the frontmatter contract) and return only contract-conformant views — so an agent
*cannot* read an unverified node's proof and treat it as basis (`kb_grep` /
`kb_list_verified` only ever surface `pass_count ≥ 1` nodes):

- `kb_grep(query)` — lexical (keyword/BM25) match over **verified** node
  statements; returns matching labels + statements. *(甲)*
- `kb_node(label)` — statement / kind / `pass_count` / deps for one node
- `kb_dependencies(label)`, `kb_dependents(label)` — walk the dependency
  graph. *(丙)*
- `kb_list_verified()` / `kb_list_staged()` — enumerate by state

Markdown files are safe to read concurrently, so reads need no central process;
they go through the shared read library so the contract/projection logic stays in
one place.

**KB search itself is a skill, not a service.** The `query-kb` skill (LLM,
generation agent) *composes* these primitives — exactly the Rethlas-original
style. Three retrieval modalities, all driven by the agent:

- **(甲) lexical** — `kb_grep` for terms drawn from the Goal.
- **(丙) graph** — follow `kb_dependencies` / `kb_dependents` from the hits and
  from the Goal's own deps to pull neighbours.
- **(knowledge + name-guessing)** — labels are semantic (`lem:best_response_continuous`,
  `def:simplex`…), so the agent **guesses plausible labels from its own
  mathematical knowledge** and probes them with `kb_node(guessed_label)`; a hit is
  a direct find, a miss costs nothing.

The agent's mathematical knowledge then judges which candidates from any modality
are genuinely usable premises and folds them into the decomposition. **No
embeddings, no learned ranker** — the relevance judgement (and the label guesses)
are the LLM's; the primitives are mechanical.

**Write tools** never edit markdown directly. They submit a proposed change as an
append-only event; the **librarian remains the sole writer** of the markdown KB
and runs admission before creating or updating any file. Each tool returns the
admission verdict so the agent learns whether its write met the contract:

- `kb_propose_subgoal(…)` → `decomposition.proposed` → new staged node file
- `kb_submit_proof(label, proof, premise_refs)` → `proof_attempt.drafted` → proof
  body written onto the node file
- `kb_propose_counterexample(…)` → `counterexample.proposed` → counterexample file

On admission the librarian writes/updates the markdown node file (frontmatter +
body) and flips `pass_count` as the verifier verdict dictates. The `query-kb` and
`verify-proof` skills are thin wrappers over these MCP tools. **There is no file
path from any agent to durable truth** — agents emit proposals; the librarian
writes markdown.

### 13.5 Subgoal → durable-node bridge

Rethlas-original keeps subgoals in a transient per-problem `subgoals` memory
channel and assembles a single blueprint. Here, a subgoal that survives screening
is **admitted as a durable staged node** via `kb_propose_subgoal`; once its proof
is accepted it flips to verified and joins the basis for *future* Goals.
Plan-local helpers may later be promoted topic-wide by the §10 promoter.
Transient reasoning (failed paths, toy examples, branch states) stays in
workspace memory and never enters the KB.

### 13.6 Revised implementation order (supersedes §12 steps 4–7)

1. **Markdown read library** (`common/kb/`, Kuzu-free): parse a node `.md` back
   into a read projection (inverse of `librarian/renderer.render_node`);
   `read_node`, `list_nodes`, `query_verified` (`pass_count ≥ 1`), `list_staged`
   (`pass_count ≤ 0`), `dependencies_of`. Round-trip tested against the renderer.
2. **Replace the Kuzu projection**: repoint `librarian/projector.py` from Kuzu
   writes to (a) rendering markdown (math) and (b) an in-memory operational
   projection rebuilt from `events/` (`repair_count`, verifier feedback,
   `introduced_by_actor`). Retire `kuzu_backend.py` / `KBReader` /
   `query_server.py`.
3. Durable-KB MCP **read** tools over the markdown library (`kb_node`,
   `kb_query_verified`, `kb_list_staged`, deps) + the `query-kb` skill.
4. Durable-KB MCP **write** tools that submit events to the librarian (sole writer
   of markdown), returning admission verdicts (`kb_propose_subgoal`,
   `kb_submit_proof`, `kb_propose_counterexample`).
5. Generation agent skill set: port the Rethlas-original skills **minus**
   `search-math-results`, repointing all premise access to `query-kb`.
6. Verification agent skill set: harden `check-referenced-statements` to the
   verified-only rule.
7. Coordinator goal-selection lane + Regulator agent: pick a staged node, run the
   adaptive loop, let the verdict drive the §6 triggers; the regulator
   (`assess-progress` / `decide-continue-or-escalate`) gates continue-vs-stop and
   wires into the dashboard's Human Attention surface.
