---
name: synthesize-verification-report
description: Build the Phase I verifier verdict JSON for one target node.
---

# Synthesize Verification Report

Aggregate the verification reasoning into the final JSON object consumed by
`verifier/role.py`.

## Input Contract

Use the current run's in-context findings:

- checked proof items
- dependency checks
- external-reference observations
- gaps
- critical errors
- prompt-provided `verification_hash`

Do not query memory or write result files.

## Procedure

1. Include every checked item, gap, critical error, and external-reference
   check. Do not drop weak findings to make the verdict cleaner.
2. Choose verdict:
   - `accepted` iff `gaps=[]` and `critical_errors=[]`;
   - `critical` iff `critical_errors` is non-empty;
   - otherwise `gap`.
3. For `accepted`, set `repair_hint` to `""`.
4. For `gap` or `critical`, set `repair_hint` to a concise non-empty summary of
   what evidence or revision the generator should provide. Do not write a
   replacement proof.
5. Return the raw JSON object as the final output with no markdown fence and no
   prose after it.

## Output Contract

```json
{
  "verification_hash": "string from prompt",
  "verdict": "accepted",
  "verification_report": {
    "summary": "string",
    "checked_items": [],
    "gaps": [],
    "critical_errors": [],
    "external_reference_checks": []
  },
  "repair_hint": ""
}
```

The `verification_hash` must exactly match the prompt value.
