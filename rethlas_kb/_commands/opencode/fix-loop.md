---
description: Iterate every agent against a project until convergence.
---

Close-the-loop autofix for project `$ARGUMENTS`.

```
rethlas-kb status --project "$ARGUMENTS"
rethlas-kb open-questions --project "$ARGUMENTS" --json
```

Iterate until: zero opens, max 5 iterations, or no-progress cycle.

## Dispatch per flag
- `is_missing` → `stub-def <id> --referring-node <flagger>`
- staged def → `verify-stmt <id>` → if flagged, `fix-stmt <id>`
- staged thm/lemma/prop → `verify-stmt <id>` + `verify-proof <id>`; gap → `fill-gap <id>`
- external-theorem → `audit-source <id>`
- counterexample_found → STOP, report

After each iteration: `rethlas-kb promote-request --all-pending`.

Cross-backend: use different backends for fill-gap vs verify-proof.
Don't loop on `defers_to_human`.

End with `status` + `open-questions` + brief prose summary.
