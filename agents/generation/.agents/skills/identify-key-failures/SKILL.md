---
name: identify-key-failures
description: Synthesize the common stuck points across failed decomposition plans and recursive sub-agent reports. Use when the current batch of decomposition plans has failed.
---

# Identify Key Failures

Use this skill to turn many failed attempts into reusable guidance for the next planning round.

## Input Contract

Read:

- the failed decomposition plans (from `subgoals`)
- per-subgoal direct-proving stuck points (from `proof_steps`)
- recursive sub-agent reports (from `proof_steps` with
  `attempt_type: "recursive"`, plus relevant `scratch_events`
  recursive_proving_round records)
- existing `failed_paths`
- relevant `counterexamples` and `toy_examples`
- recent `big_decisions` so a strategic pivot recorded earlier still
  informs the synthesis

## Procedure

1. Gather the reports from all failed plans and sub-agents.
2. List the key stuck points for each plan.
3. Identify common points across those failures:
   - recurring obstructions or counterexamples
   - decomposition patterns that keep breaking
   - search gaps or missing background facts
4. Summarize what the failures suggest for the next generation of decomposition plans.
5. Save the synthesized failure knowledge to `failed_paths` so later planning skills can use it.
6. If the synthesis points at a strategic pivot rather than another
   local iteration of the same plan family — for example, every plan
   hits the same obstruction, the target needs reformulating, or a
   constraint should be elevated from per-plan to top-level — also
   append a `big_decisions` record (schema below). Do not write a
   `big_decisions` record when the failures only suggest a tactical
   tweak; that belongs in the next round's plans, not in the
   strategic-decision log.
7. After recording the failure synthesis, return control to `$propose-subgoal-decomposition-plans`.

## Output Contract

Append to `failed_paths`:

```json
{
  "record_type": "key_failures_summary",
  "failed_plan_ids": ["..."],
  "plan_failures": [
    {
      "plan_id": "...",
      "stuck_points": ["..."]
    }
  ],
  "common_failures": ["..."],
  "implications_for_next_plans": ["..."]
}
```

When step 6 applies, also append to `big_decisions`:

```json
{
  "decision_id": "decision-{N}",
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

Pick `decision_type` per these definitions:

- `strategy_pivot`: switch the proof technique while keeping the
  target statement (e.g. induction on rank → compactness argument).
  `previous_approach` and `new_approach` are both proof techniques.
- `abandonment`: stop pursuing this target/sub-target entirely
  because available evidence says the claim is wrong, the resources
  required exceed Phase I scope, or every viable angle has been
  exhausted. `new_approach` may be empty; the implication is that
  the next round should not re-propose this target.
- `reformulation`: rewrite the target statement itself — e.g. as a
  counter-example/negation form, under a weaker hypothesis, or
  under a different invariant. `previous_approach` is the old
  statement; `new_approach` is the new one.
- `elevation`: promote a per-plan assumption or observation up to a
  top-level invariant the next planning round must respect (e.g.
  "every plan from now on must keep $|G| < n$ as a global
  constraint"). `new_approach` describes the elevated invariant.

`decision_id` derivation: see the AGENTS.md "Identifier conventions"
table (form `decision-{N}`, allocated by this skill).

Also append a `scratch_events` record indicating that a new planning round is needed.

## MCP Tools

- `memory_search`
- `memory_append`
- `branch_update`

## Failure Logging

If the reports are too weak to identify meaningful common failures, append a `scratch_events` record with `event_type="key_failures_inconclusive"` and state what information is still missing.

## Next Skill

Always return control to `$propose-subgoal-decomposition-plans` for the
next planning round. If a `big_decisions` record was appended in step
6, the next round must read it (its Input Contract already requires
this) and produce plans consistent with the strategic pivot.
