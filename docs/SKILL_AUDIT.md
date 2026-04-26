# Skill / Architecture Alignment Audit (2026-04-26, re-checked 2026-04-27)

Snapshot of where the agent skills under `agents/{generation,verification}/.agents/skills/` and their host `AGENTS.md` files agree or disagree with `docs/ARCHITECTURE.md`. Keep this file as a punch-list — once an item is fixed, mark it done with the commit hash.

**Re-check note (2026-04-27)**: F1, F3, and (partially) F4 were already addressed by commit `514a44c "Align skill contracts with Phase I agent design"` before this audit was written. They are kept here for history but marked **resolved**. F7–F11 are new findings from the re-check.

## Scope audited

- `agents/generation/.agents/skills/`: 9 skills
  (obtain-immediate-conclusions, search-math-results, query-memory,
  construct-toy-examples, construct-counterexamples,
  propose-subgoal-decomposition-plans, direct-proving,
  recursive-proving, identify-key-failures)
- `agents/verification/.agents/skills/`: 3 skills
  (check-referenced-statements, verify-sequential-statements,
  synthesize-verification-report)
- `agents/generation/AGENTS.md`, `agents/verification/AGENTS.md`
- `agents/generation/mcp/server.py` (MCP tool surface + channel registry)
- `agents/generation/.codex/config.toml` and the
  `subgoal-prover` sub-agent definition
- `docs/ARCHITECTURE.md` §6.2, §6.3 (and structural refs back to §3 / §4 / §5)

## Real inconsistencies

### F1 — Verifier skill ordering (AGENTS.md vs ARCH §6.3)
- **ARCH §6.3 line 1944-1946**: `check-referenced-statements`,
  `verify-sequential-statements`, `synthesize-verification-report`.
- **`agents/verification/AGENTS.md` line 55-57**: same order.
- **Status**: **resolved** in commit `514a44c` (pre-dates this audit).
  Re-check on 2026-04-27 confirms both files match.

## MCP channel surface drift

### F2 — `big_decisions` is a dead channel
- Original gap: registered in MCP but no producers, consumers, or
  documented purpose.
- **Status**: resolved. Wired per Option B (see "big_decisions design
  proposal" below). Producers: `identify-key-failures` (strategic
  pivots), `propose-subgoal-decomposition-plans` (materially different
  plan sets). Consumers: `propose-subgoal-decomposition-plans` (avoid
  re-pursuing abandoned strategies), `query-memory` (now lists it).

### F3 — `query-memory` underdocuments queryable channels
- Original gap: `subgoals` and `proof_steps` missing from the channel
  list.
- **Status**: **resolved** in commit `514a44c`. The current skill
  enumerates all 8 non-`big_decisions` channels.

### F4 — `recursive-proving` mixes MCP tools and Codex multi-agent tools in one list
- **Status**: resolved. The Tools section is now split into "## MCP
  Tools" and "## Codex Sub-agent Tools" with the latter explicitly
  citing `agents/generation/.codex/config.toml` as the source.

## Skill content gaps

### F5 — `verify-sequential-statements` does not explicitly cover empty proof for `definition` / `external_theorem`
- ARCH §6.3 mandates: "For definitions: Stage 2: vacuously passing
  (empty proof — no step-by-step to verify)" and the same for
  `external_theorem`.
- The skill step 2 only addresses proof-requiring kinds:
  > If kind is `lemma`, `proposition`, or `theorem` and proof is empty
  > or unusable, record a gap before any further checking.
- For the other two kinds the skill is silent. The implicit
  conditional-fall-through is correct, but the absence of a positive
  rule invites a Codex run to mis-classify a definition's empty proof
  as a gap.
- **Status**: resolved. Skill step 2 now positively states that for
  `definition` / `external_theorem` an empty proof is expected and
  per-step proof verification is skipped.

### F6 — `direct-proving` and `recursive-proving` both produce `<node>` blocks but neither restates the §6.2 batch contract
- ARCH §6.2 enforces: no `external_theorem` from generator; non-empty
  `statement`; content-descriptive labels (no placeholders); target
  label must appear; no rewriting non-target existing labels;
  `\ref{}` per dep; intra-batch DAG.
- The "Batch Output Contract" section of `agents/generation/AGENTS.md`
  carries this. The two skills tell sub-agents to "assemble candidate
  `<node>` blocks" without referencing that contract.
- `recursive-proving` only ships the assigned plan + stuck points to
  each sub-agent (step 3); if the sub-agent's prompt context loses
  AGENTS.md, the produced `<node>` blocks may violate the schema and
  waste a batch.
- **Status**: resolved. `recursive-proving` step 3 now spells out the
  full §6.2 batch contract inline (kind allow-list, label
  prefix/placeholder rules, target-must-appear, write-scope, DAG, etc.)
  so sub-agents see it even when AGENTS.md is not in context.
  `direct-proving` step 8 carries the same restatement before
  `<node>` block assembly.

## Re-check findings (2026-04-27)

### F7 — Append-only MCP can't represent the status mutations skills assume
- `propose-subgoal-decomposition-plans` line 48 declares
  `"status": "proposed|screening|screened|selected|failed|solved"`,
  implying a mutable field per `plan_id`.
- `direct-proving` line 57: "Update the corresponding decomposition-plan
  record in `subgoals` to `screening`, `screened`, or `solved`."
- But `agents/generation/mcp/server.py` exposes only `memory_append` —
  there is no `memory_update`. JSONL files are append-only.
- A literal reading produces multiple records with the same `plan_id`
  and different `status`. `memory_search` does not dedupe by `plan_id`
  and ranks ties by ascending `timestamp_utc` (older first; see F11),
  so a status query may return stale state ahead of fresh.
- Same shape applies to `branch_states` (`branch_update` is just
  `memory_append`), and to `counterexamples` "mark impacted
  branches/lemmas as invalid" wording.
- **Status**: resolved. Took option (a):
  - `propose-subgoal-decomposition-plans` Output Contract now spells
    out the append-only + newest-wins convention.
  - `direct-proving` "Update the corresponding decomposition-plan
    record" wording now says "append a fresh record with the same
    `plan_id` and the new `status`".
  - `recursive-proving` `branch_states` update step calls out that
    `branch_update` is append-only and that callers should reuse
    stable `branch_id` values across rounds.
  - `memory_search` ranking change in F11 makes newest-first the
    default tie-break, so the convention "Just-Works" without extra
    reader logic.

### F8 — Record IDs (`plan_id`, `branch_id`, `subgoal_id`) have no allocator
- Skills require these IDs but no skill or `AGENTS.md` says how to
  derive them. Agents must invent values.
- Across rounds (or across sub-agents in `recursive-proving`)
  collisions are possible — there is no namespace, no monotonic
  counter, no UUID convention.
- The MCP server does not validate uniqueness either.
- **Status**: resolved. ID-derivation rules added to
  `propose-subgoal-decomposition-plans` (`plan_id = "plan-{N}"`),
  `identify-key-failures` (`decision_id = "decision-{N}"`). Both rules
  use `memory_search` to find the largest existing suffix and increment
  by one, with a documented note that the MCP server does not enforce
  uniqueness. `branch_id` derivation stays caller-driven because
  `branch_update` is the only writer and branches are typically named
  by the agent for human-readability — but the skill now mentions
  reusing stable values across rounds.

### F9 — `problem_id` has no documented source
- `query-memory`, `obtain-immediate-conclusions`, and every skill that
  goes through `memory_append`/`memory_search` need a `problem_id`.
- `agents/generation/AGENTS.md` does not mention `problem_id`.
- `generator/prompt.py` (the prompt composer) emits no `problem_id`
  field; `JobRecord` has no such field; no env var carries it.
- The Codex agent therefore has nothing to pass except whatever it
  invents. Two skill calls within the same run could pick different
  values and shard the workspace memory.
- A natural source is `rec.target` (per-target memory persists across
  repair rounds; cross-target memory is separate). But the design
  needs to commit to that explicitly.
- **Status**: resolved. `generator/prompt.py:_problem_id_for` derives
  `problem_id` from the dispatched target via the same sanitisation
  rule as `agents/generation/mcp/server.py:sanitize_problem_id`
  (e.g. `lem:foo` → `lem_foo`). A new `## Memory scope` section in
  the prompt surfaces the derived value verbatim. `AGENTS.md` now
  has a "Memory scope" subsection telling agents to read the
  prompt-supplied value and not invent one. Test:
  `tests/unit/test_m6_prompt.py::test_memory_scope_section_surfaces_problem_id`.

### F10 — `memory_search` returns wrapped records but skills show plain shapes
- `memory_append` wraps the agent's record:
  `{"timestamp_utc": ..., "channel": ..., "record": {<agent fields>}}`.
- `memory_search` returns each hit as
  `{"score": float, "item": <wrapped record>}`.
- Skill output_contract examples (e.g. `propose-subgoal-decomposition-plans`)
  show only the inner record shape `{"plan_id": ..., "status": ...}`.
- An agent that follows the skill literally will look for `plan_id` at
  the top level of search hits and miss it (it lives under
  `result["item"]["record"]["plan_id"]`).
- **Status**: resolved. `memory_search` now unwraps server-side: each
  hit is `{score, timestamp_utc, channel, item: <agent record>}` so
  the `item` shape matches what the skill output_contracts describe.
  System metadata moved to sibling keys. BM25 scoring now runs against
  the inner `record` only, removing the timestamp-string vocabulary
  bias. AGENTS.md "Memory scope" subsection documents the shape.

### F11 — `memory_search` ties ranked older-first
- `agents/generation/mcp/server.py:283-289`:
  `sorted(zip(items, scores), key=lambda pair: (-pair[1], pair[0].get("timestamp_utc", "")))`.
- BM25 score descending (correct), tied scores ascending by
  `timestamp_utc` — i.e. older record first.
- For status-mutating channels (F7) that means the stale record
  outranks the fresh one when both match the query equally.
- For `failed_paths`, `counterexamples`, `immediate_conclusions` the
  effect is mild (older finding has equal weight). For `subgoals` and
  the proposed `big_decisions` channel the effect compounds F7.
- **Status**: resolved. `memory_search` now sorts by descending BM25
  score with descending `timestamp_utc` as the tiebreaker (newest
  first). Implemented as a stable two-pass sort: secondary key
  (timestamp) descending first, then primary key (score) descending.
  Underwrites the F7 "latest wins" convention.

## Already-aligned design (sanity-check pass)

These were verified against ARCH and pass — no action needed:

- `verify-proof` skill removed from `agents/generation/.agents/skills/`
  (commit history). Aligns with §6.2 "Generator must not call any
  verifier service".
- `agents/verification/api/server.py` and
  `agents/verification/mcp/server.py` deleted. Aligns with §6.3 "The
  old verifier mcp/ and api/ service paths are not active Phase I
  components".
- All three verifier skills explicitly forbid MCP / web / arXiv /
  events / runtime. Aligns with §6.3 information boundary.
- All nine generator skills write only via `memory_append` to MCP
  scratch channels. Aligns with §6.2 "Skills may write only to
  generator MCP scratch memory".
- Label → filename rule (`:` → `_`) in
  `check-referenced-statements` matches the §6.3 table exactly.
- Verdict three-state logic in `synthesize-verification-report` step 2
  agrees with §6.3 across all four (gaps × critical_errors)
  combinations.
- The five `external_reference_checks.status` values
  (`verified_in_nodes`, `verified_external_theorem_node`,
  `missing_from_nodes`, `insufficient_information`, `not_applicable`)
  match between §6.3 and the verifier skills.
- `recursive-proving` enforces a single layer of sub-agents
  (step 5), aligning with §6.2 "at most one internal exploration
  layer per generator run".
- `verifier.run_completed.verification_hash` defense-in-depth: the
  skill instructs Codex to echo the prompt's value, and
  `verifier/role.py:222-227` overrides with `rec.dispatch_hash` at
  publish time regardless. Two independent enforcements of the
  hash-match contract.
- "One batch per attempt" parent-emit rule
  (`recursive-proving` step 8) aligns with §6.2 "one complete `<node>`
  batch emitted on stdout for the wrapper to decode and publish as
  one `generator.batch_committed` event".

## big_decisions design proposal

### Question
`big_decisions` is registered in MCP but has no readers or writers.
Two options:

#### Option A — delete the channel
- One-line edit in `agents/generation/mcp/server.py:CHANNEL_FILES`.
- No skill changes required.
- YAGNI-pure: nothing in Phase I needs it.
- Risk: zero (currently dead code).

#### Option B — define a clear role and wire it in
The niche it would fill: **cross-round strategic pivots and
abandonments** — material decisions that change the search direction
for an entire problem, separate from per-plan subgoals
(`subgoals`), per-subgoal attempts (`proof_steps`), per-plan failure
syntheses (`failed_paths`), and meta-log entries (`scratch_events`).

Concretely, Phase I generator currently has these reasoning
artefacts but no place to record decisions like:
- "Round 2 abandoned the induction-on-rank approach after both
  proposed plans hit the same SU(2)-character obstruction. Switch to
  compactness."
- "Reformulating the target as a counter-example proof —
  `repair_count >= 3` and the induction angle keeps producing the
  same gap."
- "Elevate constraint X from a per-plan assumption to a top-level
  invariant the next planning round must respect."

These belong in their own channel because they are *not* failures
themselves (those go to `failed_paths`) and are *not* per-plan
records (those go to `subgoals`). They are the rationale for the
strategic shift between planning rounds.

**Proposed contract**:

```json
{
  "decision_id": "...",
  "round": 0,
  "decision_type": "strategy_pivot|abandonment|reformulation|elevation",
  "summary": "...",
  "previous_approach": "...",
  "new_approach": "...",
  "drove_by": {
    "failed_plan_ids": ["..."],
    "key_failures": ["..."],
    "counterexamples": ["..."]
  },
  "implications_for_next_plans": ["..."]
}
```

**Producers**:
- `identify-key-failures` — when the synthesized failures call for a
  strategic pivot rather than another local plan iteration. Append a
  `big_decisions` record alongside the existing `failed_paths`
  summary record.
- `propose-subgoal-decomposition-plans` — when the new plan set is
  materially different from prior rounds (e.g. switches the proof
  technique or rewrites the target as a counter-example). Append a
  `big_decisions` record describing the pivot before publishing the
  new plans.

**Consumers**:
- `propose-subgoal-decomposition-plans` should pull recent
  `big_decisions` via `memory_search` to avoid re-pursuing
  abandoned strategies.
- `query-memory` should list `big_decisions` as a queryable channel
  (and update its skill text accordingly).
- Repair-mode prompt composition (in `generator/role.py`) could
  optionally summarize recent `big_decisions` so the next round
  doesn't relitigate them.

### Recommendation
**Option B**, with the caveat that it requires four touch points:
1. New skill text in `identify-key-failures` and
   `propose-subgoal-decomposition-plans` to write
   `big_decisions` records (with the schema above).
2. Update `query-memory` channel list (also closes F3).
3. Optional: enrich repair prompt composition to surface them.
4. Add a one-paragraph note in ARCH §6.2 acknowledging the channel
   so future readers can find its purpose.

If the repair prompt enrichment turns out unwanted, reduce to
Option A — the cost is just deleting the registry entry. Phase I
should not carry an undocumented dead channel either way.

## Audit method

For reproducibility, the audit walked through:
1. List every `SKILL.md` under both agents directories.
2. Diff stated tools / channels against
   `agents/generation/mcp/server.py`'s actual surface.
3. Trace each skill's "Procedure" section against ARCH §6.2 / §6.3
   for matching invariants.
4. For verifier skills, additionally cross-check verdict logic and
   information boundary against §6.3.
5. For generator skills, verify scratch-only writes (no `events/`,
   `runtime/`, `knowledge_base/`, Kuzu writes) per §6.2.

A future re-audit should re-run all five steps and append findings
under a new dated heading rather than rewriting prior entries.
