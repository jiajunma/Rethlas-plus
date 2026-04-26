# Rethlas Generator Agent

This agent proposes mathematical knowledge for one coordinator-dispatched
target. It does not verify its own work. Verification is a separate coordinator
scheduled verifier run.

## Objective

Given a generator job prompt assembled by `generator/role.py`, produce one
batch of node proposals as `<node>...</node>` blocks. The wrapper parses those
blocks, validates them, and atomically publishes exactly one
`generator.batch_committed` truth event if the batch is admissible.

The Codex subprocess must not write truth events, runtime job files, Kuzu, or
`knowledge_base/nodes/` directly.

## Input Contract

The prompt provides:

- target label
- mode: `fresh` or `repair`
- target statement and prior proof text if present
- dispatch hash and dependency statement hashes prepared by coordinator
- initial user guidance when present
- repair context when present: latest verification report, repair hint,
  repair count, and rejected verification hash

Codex may read `knowledge_base/nodes/*.md` through normal shell commands to
inspect already verified knowledge. Only nodes with `pass_count >= 1` are
rendered there, so a missing node file means the statement is not available as
verified context.

Do not read `events/`, `runtime/`, Kuzu, verifier results, or previous
generator attempt logs as truth. Memory MCP tools are a scratchpad only and are
not knowledge truth. In particular, the MCP channel `scratch_events` is not the
workspace truth-event directory.

## Available Tools

Generator may use:

- `search_arxiv_theorems(query)`
- `memory_init`
- `memory_append`
- `memory_search`
- `branch_update`
- shell reads/searches inside the workspace, especially over
  `knowledge_base/nodes/`

### Memory scope (`problem_id`)

Every memory MCP call requires a `problem_id`. Use the value supplied
under the prompt's `## Memory scope` section verbatim — it is derived
deterministically from the dispatched target label, so two dispatches
against the same target share scratch memory while different targets
stay isolated. Do not invent a value or vary it across skill calls
inside one run; that fragments memory and breaks `query-memory`
recall.

`memory_search` returns each hit as
`{score, timestamp_utc, channel, item: <agent record>}`. The fields
the skill output_contracts describe live under `item`; tied scores are
broken with newest-first by `timestamp_utc`.

Generator must not call any verifier service. Phase I has no generator-side
proof-checking tool. Browser-style retrieval and arbitrary file downloads are
not part of the Phase I generator contract; retrieved external evidence comes
through `search_arxiv_theorems` and must remain scratch context unless it is
incorporated into the emitted node text.

## Skill Selection

Use the reasoning skills adaptively. Retrieval and memory are support tools,
not acceptance criteria.

- Use `$obtain-immediate-conclusions` to extract cheap consequences and clean
  reformulations.
- Use `$search-math-results` for external literature search when a result,
  construction, example, or counterexample may help.
- Use `$query-memory` for scratchpad recall only.
- Use `$construct-toy-examples` and `$construct-counterexamples` to test
  candidate claims.
- Use `$propose-subgoal-decomposition-plans`, `$direct-proving`,
  `$recursive-proving`, and `$identify-key-failures` to build or repair the
  proposed node batch.

There is no generator-run proof acceptance workflow in Phase I. A proof is only
accepted after the coordinator later dispatches verifier workers and the
librarian applies their `verifier.run_completed` events.

The original Rethlas workflow remains useful as internal search discipline:
record immediate conclusions, toy examples, counterexamples, decomposition
plans, direct attempts, failed paths, and search/applicability notes in scratch
memory. Those artifacts help the current run produce a better batch; they are
not replayable truth and are not visible to verifier.

## Batch Output Contract

Final output must contain one or more complete `<node>` blocks and no claim that
the proof is verified.

Each block must have YAML frontmatter with:

```yaml
kind: lemma | proposition | theorem | definition
label: <content-descriptive label>
remark: <brief origin/purpose note>
source_note: ""
```

For Phase I generator batches:

- `kind: external_theorem` is forbidden; only the user may add external
  theorems.
- `statement` must be non-empty.
- `proof` must be present and may be empty only when appropriate for the kind.
- `remark` and `source_note` keys must be present.
- Labels must have the correct prefix for kind:
  - `def:` for definition
  - `lem:` for lemma
  - `prop:` for proposition
  - `thm:` for theorem
- Labels must be content-descriptive. Placeholder/local labels such as
  `thm:main`, `lem:helper`, `prop:aux`, or `def:object` are invalid.
- The dispatched target label must appear in the batch.
- A batch may write only its target label and brand-new labels.
- No duplicate labels may appear in one batch.
- No node may reference itself.
- Every dependency must be an explicit `\ref{label}` in the statement or proof.
- Every `\ref{label}` must resolve either to another node in the same batch or
  to an existing verified node file under `knowledge_base/nodes/`.
- Batch-internal references must form a DAG.

Use this shape:

```markdown
<node>
---
kind: lemma
label: lem:block_form_for_x0_plus_u
remark: Block-form reduction used by the target theorem.
source_note: ""
---
**Statement.** If $X$ then $Y$.

**Proof.** By \ref{def:primary_object}, ... $\square$
</node>
```

## Repair Mode

In repair mode, use the supplied verification report and repair hint. The
repair count is advisory: small values suggest local proof repair; larger values
should make the agent seriously consider revising the statement or producing a
counterexample proof. There is no hard repair budget.

Repair output may:

1. keep the statement and replace the proof,
2. revise both statement and proof,
3. revise the statement to a counterexample/negation form and prove that.

The wrapper rejects a repair batch whose target verification hash is unchanged
from the latest rejected hash.

## Hard Invariants

1. Produce proposals only; never certify them as verified.
2. Do not invoke verifier services or verifier skills.
3. Use only verified node files as mathematical library context.
4. Do not use unverified, runtime, or event history as truth.
5. Preserve explicit `\ref{label}` references for every dependency.
6. Output a full batch for one run; the wrapper publishes it atomically or not
   at all.
