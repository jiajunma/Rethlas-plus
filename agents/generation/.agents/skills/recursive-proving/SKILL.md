---
name: recursive-proving
description: Launch one sub-agent per decomposition plan after direct screening has identified the key stuck points for each plan. Use when all current plans have been screened by direct proving, none fully solves the problem, and parallel recursive work is needed.
---

# Recursive Proving

Use this skill when direct proving has failed on the current decomposition plans.

## Input Contract

Read:

- the current set of decomposition plans (from `subgoals`)
- the direct-proving reports and key stuck points for each plan
  (from `proof_steps` with `attempt_type: "direct"`)
- the known stuck points from other plans (same channel)
- relevant `failed_paths`, `branch_states`, and search results

## Procedure

1. Confirm that all current decomposition plans have already been attempted with `$direct-proving` and that none has fully solved the problem.
2. Spawn at most one bounded layer of sub-agents, one per selected decomposition plan, subject to the run's configured worker/budget limits.
3. Give each sub-agent:
   - the full target theorem
   - the assigned decomposition plan
   - the key stuck points for its own plan
   - the key stuck points found in the other plans
   - the instruction to follow `AGENTS.md`
   - the literal `problem_id` value the parent received from the
     prompt's `## Memory scope` section, with the explicit instruction
     to use that exact value for every `memory_search` /
     `memory_append` / `memory_init` / `branch_update` call inside the
     sub-agent. Sub-agents do not see the parent's prompt by default;
     skipping this step shards scratch memory between parent and
     sub-agent and breaks `query-memory` recall.
   - an inline reminder of the §6.2 batch contract that any candidate
     `<node>` blocks must satisfy: `kind` ∈ {lemma, proposition,
     theorem, definition} (no `external_theorem`); non-empty
     `statement`; content-descriptive labels with the correct
     `def:`/`lem:`/`prop:`/`thm:` prefix (no placeholders like
     `thm:main`); the dispatched target label must appear; only the
     target may already exist (other batch labels must be brand-new);
     no duplicate labels; no self-references; every dependency must
     appear as a `\ref{label}` in the statement or proof and resolve
     either inside the batch or to an existing verified node;
     batch-internal references must form a DAG.
4. Tell each sub-agent to tackle the assigned plan under the instructions in `AGENTS.md`, treating that plan as its starting point rather than restarting the search from zero. If new evidence or discoveries justify it, the sub-agent may refine, extend, or locally revise the plan, but it should preserve continuity with the assigned plan instead of discarding it outright.
5. Tell each sub-agent not to spawn further sub-agents. Phase I keeps recursive exploration as a bounded internal search pattern, not an unbounded process tree.
6. Require each sub-agent to return progress, failures, and any
   successful candidate node text to the parent generator. The parent
   records useful artifacts in scratch memory using the prompt-supplied
   `problem_id` (the one forwarded to the sub-agent in step 3), keeping
   parent and sub-agent on a single shared memory namespace.
7. Wait for all sub-agents to finish, then gather their reports.
8. If any plan succeeds, assemble candidate `<node>` blocks from that plan. The parent generator emits the only final batch.
9. If all plans fail, append a fresh `subgoals` record per failed
   plan_id with `status: "failed"` so the next planning round sees
   which plans were tried-and-failed (the channel is append-only;
   newest-first ranking makes the new status win on recall). Then
   hand the collected reports to `$identify-key-failures`.

## Output Contract

Append a `scratch_events` record for the recursive round:

```json
{
  "event_type": "recursive_proving_round",
  "plan_ids": ["..."],
  "subagent_ids": ["..."],
  "shared_stuck_points": {
    "plan_id": ["..."]
  },
  "status": "running|completed",
  "successful_plan_ids": ["..."],
  "failed_plan_ids": ["..."]
}
```

Update `branch_states` by calling `branch_update(problem_id, branch_id, state)`
once per branch with the recursive round status and per-plan outcomes;
each call appends a fresh state record (the channel is append-only).
Reuse stable `branch_id` values across rounds so newest-first ranking
in `memory_search` surfaces the most recent state for each branch.

## MCP Tools

- `memory_search`
- `memory_append`
- `branch_update`
- `search_arxiv_theorems`

## Codex Sub-agent Tools

Provided by Codex CLI's `multi_agent` feature, configured in
`agents/generation/.codex/config.toml` and
`agents/generation/.codex/agents/subgoal-prover.toml`. Not part of the
project's MCP server.

- `spawn_agent`
- `send_input`
- `wait_agent`
- `close_agent`

Use exactly one bounded layer; sub-agents must not spawn further
sub-agents.

## Failure Logging

If every plan fails in the recursive round, append a summary record to `failed_paths` and immediately invoke `$identify-key-failures`.

## Next Skill

- Any sub-agent succeeds → assemble the `<node>` batch from that
  plan (step 8) and exit; the parent generator emits the final batch.
- Every sub-agent fails → `$identify-key-failures` per the Failure
  Logging line above.
