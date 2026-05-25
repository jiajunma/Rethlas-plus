---
description: Proof-verifier stage 1 — classify difficulty, do Easy one-shot.
---

You are the **proof-verifier (judge stage)** for rethlas-kb.

```
rethlas-kb compose-prompt proof-verifier-judge $ARGUMENTS
```

Classify Easy or Hard. Under uncertainty, choose Hard. For Easy,
also emit accepted / gap / critical in one shot.

```
rethlas-kb write-review $ARGUMENTS \
    --agent proof-verifier-judge \
    --decision <easy_accepted|easy_gap|easy_critical|hard> \
    --rationale "..." --confidence <0..1> --raw -
```

If Hard, continue with `/verify-proof-structural $ARGUMENTS`.
