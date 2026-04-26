---
name: propose-subgoal-decomposition-plans
description: Propose multiple subgoal decomposition plans for the current theorem using the information already gathered. Use when enough information has been collected from examples, counterexamples, search results, and previous failures to break the problem into several materially different plans.
---

# Propose Subgoal Decomposition Plans

Use this skill when the agent has enough context to propose several viable decomposition plans.

## Input Contract

Read:

- the current target theorem or branch goal
- relevant `immediate_conclusions`, `toy_examples`, and `counterexamples`
- relevant `failed_paths` and `branch_states`
- recent strategic pivots from `big_decisions` so the new plan set does
  not re-pursue an approach that earlier rounds already abandoned
- recent search results and useful references from `scratch_events`

## Procedure

1. Gather the current information that materially constrains the problem: useful examples, failed claims, known obstructions, and relevant search results.
2. Propose materially different decomposition plans.
3. For each plan, state:
   - the main idea of the plan
   - the ordered subgoals
   - why this plan is plausible given the current information
   - which earlier failures or counterexamples it tries to avoid
4. If the new plan set is materially different from the prior round —
   different proof technique, reformulated target, new top-level
   invariant, or an explicit pivot away from a strategy recorded in
   `big_decisions` — append a fresh `big_decisions` record describing
   the pivot before publishing the new plans (schema in `$identify-key-failures`).
5. Hand each plan to `$direct-proving` for a quick screening pass.

## Output Contract

`plan_id` must be unique across rounds. Derive it as
`"plan-{N}"` where `N` is one greater than the largest numeric suffix
seen via `memory_search(problem_id, "plan-", channels=["subgoals"])`,
starting from `1`. The MCP server does not enforce uniqueness; if two
runs produce colliding ids the latest record will silently overwrite
the earlier one in `query-memory` results.

`status` is mutable: append a fresh record with the updated `status`
each time a plan transitions (`proposed` → `screening` → `screened` →
`solved`/`failed`). Append-only JSONL plus newest-first ranking in
`memory_search` makes the latest record per `plan_id` win on recall.

Append one record per plan to `subgoals`:

```json
{
  "plan_id": "...",
  "record_type": "decomposition_plan",
  "goal": "...",
  "plan_summary": "...",
  "subgoals": ["..."],
  "motivation": ["..."],
  "uses_information_from": {
    "examples": ["..."],
    "counterexamples": ["..."],
    "key_failures": ["..."],
    "search_results": ["..."]
  },
  "status": "proposed|screening|screened|solved|failed",
  "branch_id": "optional"
}
```

Also append a `scratch_events` record summarizing the new plan set.

## MCP Tools

- `memory_search`
- `memory_append`
- `branch_update`
- `search_arxiv_theorems`

## Failure Logging

If the agent cannot yet propose meaningful decomposition plans, append a `scratch_events` record with:

- `event_type="decomposition_plans_not_ready"`
- the missing information
- the blockers that prevent proposing plans
