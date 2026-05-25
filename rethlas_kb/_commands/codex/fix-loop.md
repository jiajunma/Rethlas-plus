---
description: Iterate every agent against a project until convergence.
---

Close-the-loop autofix for project `$ARGUMENTS`. Iterate
status → dispatch agents per flag type → re-verify → stop on convergence,
max-iter (5), or no-progress cycle.

## Snapshot
```
rethlas-kb status --project "$ARGUMENTS"
rethlas-kb open-questions --project "$ARGUMENTS" --json
```

## Dispatch
| flag | action |
|---|---|
| `is_missing: true` | `rethlas-kb stub-def <id> --referring-node <flagger>` |
| staged def | `rethlas-kb verify-stmt <id>` → if flagged `rethlas-kb fix-stmt <id>` |
| staged thm/lemma/prop | `rethlas-kb verify-stmt <id>` + `rethlas-kb verify-proof <id>`; on gap → `rethlas-kb fill-gap <id>` |
| external-theorem | `rethlas-kb audit-source <id>` (fetch source yourself) |
| counterexample_found | STOP — report to human |

After every iteration: `rethlas-kb promote-request --all-pending`.

## Discipline
- Use batch flags (`--project`) where possible.
- Cross-backend isolation: use different backends for fill-gap vs verify-proof.
- Don't loop on `defers_to_human` — report and move on.
- Be cost-aware: ask user before iteration 2 if not pre-approved.

## Final report
```
rethlas-kb status --project "$ARGUMENTS"
rethlas-kb open-questions --project "$ARGUMENTS" --limit 20
```
Plus brief prose on what ran, what got fixed, what needs human.
