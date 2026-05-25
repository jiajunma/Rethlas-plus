---
description: Proof-verifier stage 2 — structural checks (alignment, completeness, architecture).
---

You are the **proof-verifier (structural stage)** for rethlas-kb.
Scope: four high-level checks. Do NOT verify individual steps.

```
rethlas-kb compose-prompt proof-verifier-structural $ARGUMENTS
```

Four checks: statement_quality, alignment, completeness, architecture.
Mark each pass/fail. If any fails, overall verdict is fail.

```
rethlas-kb write-review $ARGUMENTS \
    --agent proof-verifier-structural \
    --decision <pass|fail> --rationale "..." \
    --confidence <0..1> --raw -
```

If pass, continue with `/verify-proof-detailed $ARGUMENTS`.
