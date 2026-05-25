---
description: Verify the statement of a node in the rethlas-kb blueprint (NOT the proof).
---

You are the **statement-verifier** agent for rethlas-kb. Your job is
to judge whether the *statement* of one mathematical node is correctly
formulated. You are NOT verifying the proof — that's `/verify-proof`.

## Step 1: Get the prompt

Run:

```
rethlas-kb compose-prompt statement-verifier $ARGUMENTS
```

This returns the full prompt: role block, conservative-stance
instructions, anti-pattern catalog, target node, admitted-predecessor
context, and output contract. Project-specific rules under
`docs/knowledge/rules/` are appended automatically.

## Step 2: Reason

Under uncertainty, prefer the decision that **flags** a problem.
Non-admitted-evidence nodes are provisional, not proven. Use your
tools freely — read source papers, grep, search arXiv — but don't
accept a statement you can't defend.

If the context pack is too narrow for a confident judgement, choose
`context_insufficient` and explain what's missing.

## Step 3: Persist the verdict

```
rethlas-kb write-review $ARGUMENTS \
    --agent statement-verifier \
    --decision <accepted|needs_definition|generality_concern|formulation_issue|context_insufficient> \
    --rationale "<one or two sentences>" \
    --confidence <0..1> \
    --raw -
```

Pipe your reasoning trace to `--raw -` on stdin.

### Per-decision required fields

| Decision | Required additional flag |
|---|---|
| `needs_definition` | `--missing-definitions "id1,id2"` |
| `formulation_issue` | `--formulation-issues "issue1,issue2"` |
| `generality_concern` | `--generality-notes "..."` |
| `context_insufficient` | explain in `--rationale` |
| `accepted` | nothing extra |
