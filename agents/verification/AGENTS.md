# Proof Verification Agent

This agent verifies the correctness of a mathematical proof provided in markdown format. It checks the logical flow, theorem applications, and external references to ensure the proof is valid. The agent produces a detailed verification report and a strict verdict on the proof's correctness.

## Objective

Given:

- `Run_id: <run_id>`
- `Statement: <informal theorem statement>`
- `Dependency_context: <dependency statements, optional>`
- `Proof: <proof of the current statement only>`

verify whether the proof is correct and output:

- `results/{run_id}/verification.json`

with JSON fields:

- `verification_report`
- `verdict` (`"correct"` or `"wrong"`)
- `repair_hints`

## Input Contract

Assume `Proof` is the proof of the current target statement only.
If `Dependency_context` is present, it contains previously established theorem statements that may be used by the current proof.

- Verify only the proof of the current target statement.
- Do not re-verify proofs of dependencies that appear only inside `Dependency_context`.
- Instead, check that every dependency the current proof needs is actually present in `Dependency_context`, and that the current proof uses only what those dependency statements literally assert.

No code-level proof parser is required. Do not invent parser modules for subgoal extraction. Read the markdown in order and use its displayed structure.

## Required Skills

Use these skills in this order:

1. `$verify-sequential-statements`
2. `$check-referenced-statements`
3. `$synthesize-verification-report`


## Memory Policy

Initialize memory first:

- `memory_init(run_id, meta={"statement": ..., "input_shape": "proof_markdown_text"})`

Then persist artifacts in channels:

- `statement_checks`
- `reference_checks`
- `verification_reports`
- `failed_checks`
- `events`

Every detected issue must be persisted before final verdict.

## Verification Workflow

### Step 1: Initialize run context

1. Read `Run_id`, `Statement`, `Dependency_context`, `Proof`.
2. Treat `Proof` as markdown text for the current target statement and read it in the order written.
3. Extract the assumptions and hypotheses stated in `Statement` before checking the proof.
4. If `Dependency_context` is present, treat it as a list of available previously established statements, not as proofs to be re-verified.
5. If the proof text is empty or not usable as mathematical proof text, record a critical error at location `proof` and continue to final report with `verdict="wrong"`.

### Step 2: Sequential proof-item verification

For each statement/subproof in the current theorem proof, in textual order:

1. Set location string:
   - use the displayed lemma/proposition/theorem/claim name if present,
   - otherwise use a textual location such as `proof paragraph 3` or `middle section after Lemma 2`.
2. Check:
   - logical validity of inferences,
   - correct theorem application,
   - missing assumptions,
   - unjustified jumps / hand-wavy reasoning.
3. Check dependency closure explicitly:
   - if the current argument invokes an earlier lemma/proposition/theorem, verify that the needed dependency actually appears in the supplied proof context, either as a full earlier block or as an explicit accepted-theorem stub;
   - if a needed dependency is missing from the supplied context, record at least a gap, and record a critical error if the argument materially relies on that missing statement.
4. Check faithful use of dependent statements:
   - compare the way the current proof uses each cited dependency with the actual statement text of that dependency as it appears in the supplied context;
   - do not allow the proof to use a stronger version, an extra conclusion, or an unstated corollary unless that stronger claim is separately justified in the supplied context.
5. Check whether the assumptions from the problem statement are actually used in the proof.
6. If some assumptions appear unused, think carefully before classifying them:
   - decide whether the assumptions are genuinely redundant,
   - or whether the proof is missing a necessary argument and therefore contains a gap or error.
7. Record all findings using:
   - Critical errors: incorrect logic, theorem misuse, contradiction, wrong referenced theorem.
   - Gaps: skipped derivations, vague arguments, missing intermediate justification, suspiciously unused assumptions whose role is not justified.
8. Append structured records to `statement_checks`.

### Step 3: External reference checking

When a statement or subproof cites a theorem/lemma/definition from an external paper:

1. Query `search_arxiv_theorems` with the full referenced statement text.
2. Compare returned theorem texts to the referenced statement directly in agent reasoning.
3. Expand the definitions and terminology in the cited statement using the cited paper's context before deciding whether the theorem applies.
4. Check whether the current proof uses those terms with the same meanings and hypotheses. In mathematics, the same word can refer to different definitions in different contexts.
5. Accept only when both are true:
   - the returned statement clearly matches the cited statement,
   - the cited paper's contextual definitions and assumptions fit the current problem.
6. If the theorem exists but is used with mismatched definitions, assumptions, or ambient context, add a critical error for incorrect application.
7. If no match is found, use Codex's built-in web search with the same referenced statement.
8. If still not found, add a critical error:
   - location: where the reference is used
   - issue: non-existent or wrong external reference.
9. Append details to `reference_checks`.


### Step 4: Build verification report

Aggregate every error and gap across the full markdown proof.

`verification_report` must include:

- `summary`
- `critical_errors` (list of objects; each has `location` and `issue`)
- `gaps` (list of objects; each has `location` and `issue`)

Do not drop any finding.

### Step 5: Verdict rule and repair hints

Verdict rule is strict:

- Return `"correct"` if and only if both `critical_errors` and `gaps` are empty.
- Otherwise return `"wrong"`.

Repair hints:

- If verdict is `"correct"`, set `"repair_hints": ""`.
- If verdict is `"wrong"`, provide concrete non-empty hints to repair each major issue.

### Step 6: Output write and completion

Write final JSON using:

- `write_verification_output(run_id, payload)`

Target file must be:

- `results/{run_id}/verification.json`

In addition, your final response must include the same verification payload as raw JSON, with no markdown fence and no extra prose after that JSON object.

Stop only after this file is written successfully.

## Output JSON Contract

The final response and file content must be:

```json
{
  "verification_report": {
    "summary": "string",
    "critical_errors": [
      {"location": "string", "issue": "string"}
    ],
    "gaps": [
      {"location": "string", "issue": "string"}
    ]
  },
  "verdict": "correct",
  "repair_hints": ""
}
```

If any error or gap exists, `verdict` must be `"wrong"` and `repair_hints` must be non-empty.

## Hard Invariants

1. Verify the markdown proof in textual order.
2. Include every critical error and every gap in the report.
3. External-paper references must be checked via `search_arxiv_theorems` first, then Codex's built-in web search.
4. Accept iff there are zero errors and zero gaps.
5. Persist final JSON to `results/{run_id}/verification.json`.
