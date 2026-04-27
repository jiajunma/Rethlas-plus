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
   that basis. For these kinds, **the verifier's job is far narrower
   than for proof-requiring kinds**. A definition introduces new
   symbols by fiat; that is its purpose. The check is **not** "is
   every symbol grounded in cited deps" — it is:

   a. **The newly-introduced LHS symbol(s) are fine.** A statement of
      the form `Let X := <expr>`, `define X to be <expr>`, or
      `an X is a Y such that ...` introduces `X`; do **not** flag `X`
      as undefined notation. Only the right-hand side / defining
      expression must be groundable.
   b. **The RHS / defining expression** uses only symbols that are
      either (i) introduced by the cited deps, (ii) introduced
      earlier in this same statement, or (iii) standard mathematical
      operators (set-theoretic, group action, closure, span, etc.).
      Treat conventional notation (e.g. `G·x` for an action orbit,
      `\overline{S}` for closure, `f^{-1}(y)` for a preimage) as
      grounded without requiring an explicit citation.
   c. **Adjectives on already-introduced symbols are decorative.**
      If a cited dep introduces `X_0 ∈ End(V_0)` and the new
      definition writes `the nilpotent X_0`, the qualifier
      "nilpotent" is **not** a gap unless that property is
      load-bearing for the definition to be well-formed. A
      definition of the form `Ind(X_0,P,G) := closure(G·(X_0+u))`
      does not become ill-formed if `X_0` is or is not nilpotent.
   d. **No contradiction with cited deps.** If the new definition
      reuses a symbol from a cited dep with a meaning incompatible
      with the dep's own statement, that is a `critical` error.
   e. **Internal coherence.** Domain terminology used consistently;
      no obvious contradictions inside the statement.

   Skip per-step proof verification entirely for axiom kinds.
   Dependency resolution for any `\ref{label}` in the statement is
   owned by `$check-referenced-statements` — do not redo it here.

   **Do not classify as `gap` or `critical`:**
   - a definition's introduction of brand-new notation;
   - the absence of a cited adjective when the adjective is not
     load-bearing for well-formedness;
   - the absence of a separate KB node for a parameter that is
     introduced inline in the defining expression (e.g. writing
     `let O_0 := G_0·X_0` inside the statement is grounding, not
     a gap);
   - missing intermediate definitional helpers when the statement
     itself is internally complete.

   These are normal definitional moves, not verification failures.
   Reserve `gap` / `critical` for genuine ill-formedness: a symbol
   that is used on the RHS without being introduced anywhere
   reachable, an inconsistent reuse of a cited symbol, or a
   self-contradictory clause inside the definition.
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
6. Classify issues. The `checked_items.status` field accepts exactly
   `accepted|gap|critical`; an issue that is itself a gap or critical
   error must also surface in the corresponding `gaps[]` /
   `critical_errors[]` list during synthesis.
   - `gap`: missing derivation, vague justification, omitted hypothesis
     check, or insufficient evidence.
   - `critical`: contradiction, false implication, circular argument,
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

For every issue classified `gap` or `critical` in step 6, also prepare
a parallel entry for the corresponding `gaps[]` or `critical_errors[]`
list using this shape (`location` should match the `checked_items`
entry):

```json
{
  "location": "proof paragraph 3",
  "issue": "Boundedness is asserted but not derived from the hypotheses."
}
```

Contribute these to `$synthesize-verification-report` for assembly into
the final report. Do not write files.
