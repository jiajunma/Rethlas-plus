---
description: Iterate every agent against a project until convergence (close-the-loop autofix)
allowed-tools: Bash(rethlas-kb:*), Read, Grep, Glob, WebFetch
---

You are running the **close-the-loop autofix** for a rethlas-kb
project. The project id is `$ARGUMENTS`.

Your job: keep dispatching agents until either:
1. `open-questions` reports zero open work items, OR
2. You hit the max iteration cap (default 5 — adjust if the user
   said otherwise), OR
3. The same set of open questions appears in two consecutive
   iterations (you're not making progress).

## Setup

```bash
PROJECT="$ARGUMENTS"
MAX_ITER=5
PRIOR_OPENS=""
```

## Iterate

For each iteration:

### 1. Snapshot status

```bash
rethlas-kb status --project "$PROJECT" --json
rethlas-kb open-questions --project "$PROJECT" --json
```

If the open-questions JSON is `[]`, you're DONE — stop and report.

### 2. Dispatch by flag type

For each open question in the list, decide which agent to run:

| Open question | Action |
|---|---|
| `is_missing: true` | `rethlas-kb stub-def <node-id> --referring-node <flagger> --reason "..."` |
| `status: staged`, kind ∈ {definition / concept} | `rethlas-kb verify-stmt <node-id>`; if `formulation_issue` / `generality_concern` → `rethlas-kb fix-stmt <node-id>` |
| `status: staged`, kind ∈ {lemma / proposition / theorem / external-theorem} | `rethlas-kb verify-stmt <id>` THEN `rethlas-kb verify-proof <id> --depth structural`; if proof has gap → `rethlas-kb fill-gap <id>` |
| External-theorem with no source review | `rethlas-kb audit-source <id>` (fetch source passage yourself if needed via WebFetch) |

Use batch flags where you can: `rethlas-kb verify-stmt --project
"$PROJECT"` sweeps every staged node in one go. After a sweep, read
the `reviews/` directory to see what was flagged.

### 3. Promote any new requests

When `fill-gap` proposes new sub-lemmas, they land as request files.
Auto-promote them to staged nodes so the next iteration can verify
them:

```bash
rethlas-kb promote-request --all-pending --blueprint .
```

### 4. Check for cycle / converge

```bash
CURRENT_OPENS=$(rethlas-kb open-questions --project "$PROJECT" --json)
if [ "$CURRENT_OPENS" = "$PRIOR_OPENS" ]; then
  echo "No progress this iteration — stopping to avoid burning tokens."
  break
fi
PRIOR_OPENS="$CURRENT_OPENS"
```

## Decision discipline (IMPORTANT)

- **Conservative on statement-fixer**: when statement-verifier returns
  `generality_concern` or `context_insufficient`, the fixer often
  returns `defers_to_human`. Don't loop on those — report them as
  human-action-required and move on.
- **Don't auto-accept counterexample-found verdicts**: if
  `hunt-counterexample` finds a witness, the statement is likely wrong.
  Don't try to fix the proof — report and stop on that node.
- **Cost awareness**: each agent invocation is one LLM call (or 3 for
  `verify-proof --depth auto`). For a 50-node project with 5
  iterations, you can easily burn $5-50. If the user didn't pre-approve,
  ask before continuing past iteration 1.
- **Cross-backend isolation (issue #13)**: if you're using `claude`
  for `verify-proof`, use `codex` (or vice versa) for `fill-gap`:
  ```bash
  rethlas-kb fill-gap --project "$PROJECT" --backend codex
  rethlas-kb verify-proof --project "$PROJECT" --backend claude
  ```
  This avoids same-source-bias.

## Report

When you stop (converged, capped, or cycled), emit a final summary:

```bash
rethlas-kb status --project "$PROJECT"
echo "---"
rethlas-kb open-questions --project "$PROJECT" --limit 20
```

Plus a brief prose summary of what you did this run: which agents
ran how many times, which nodes were fixed / staged / still flagged,
and which nodes need human attention (defers_to_human / cannot_fix
/ counterexample_found cases).
