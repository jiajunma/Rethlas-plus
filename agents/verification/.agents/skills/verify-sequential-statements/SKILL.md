---
name: verify-sequential-statements
description: Verify one target proof in textual order using only the prompt and verified node files.
---

# Verify Sequential Statements

Check the current target proof step by step. This skill is a reasoning
procedure, not a persistence workflow.

## Input Contract

Use only:

- `Statement` from the prompt
- `Proof` from the prompt
- `Kind` from the prompt
- verified statements explicitly resolved from `knowledge_base/nodes/`

Do not use MCP tools, web search, arXiv search, events, runtime logs, or
generator memory. Do not generate a replacement proof.

## Procedure

1. Extract the target kind, assumptions, and conclusion from the prompt.
2. If kind is `lemma`, `proposition`, or `theorem` and proof is empty or
   unusable, record a gap before any further checking.
   If kind is `definition` or `external_theorem`, an empty proof is
   expected (these are axioms in Phase I) — do not record a gap on
   that basis. For `definition` and `external_theorem`, only check
   that the statement is internally coherent and that any
   `\ref{label}` it cites resolves; skip per-step proof verification
   entirely.
3. Read the proof in textual order.
4. For each meaningful claim or inference, choose a stable location:
   - displayed claim/lemma name if present,
   - otherwise `proof paragraph N`.
5. Check local validity:
   - the inference follows from earlier proof text, the target assumptions,
     or explicitly referenced verified statements;
   - all hypotheses of a referenced statement are supplied;
   - definitions and notation are used consistently;
   - no circular use of the target statement occurs.
6. Classify issues:
   - `gap`: missing derivation, vague justification, omitted hypothesis check,
     or insufficient evidence.
   - `critical_error`: contradiction, false implication, circular argument,
     invalid dependency use, or a core claim that appears wrong.
7. When unsure, classify as `gap`.

## Output Contribution

Prepare `checked_items` entries like:

```json
{
  "location": "proof paragraph 3",
  "status": "gap",
  "notes": "Boundedness is asserted but not derived from the hypotheses."
}
```

Contribute all gaps and critical errors to the final report. Do not write
files.
