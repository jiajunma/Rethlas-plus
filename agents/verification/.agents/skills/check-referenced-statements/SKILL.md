---
name: check-referenced-statements
description: Resolve explicit proof references against verified node files only. Use when a verifier run must check that cited dependencies exist in knowledge_base/nodes and are used no stronger than their statements.
---

# Check Referenced Statements

Validate dependencies and external-looking citations using only the target text
and `knowledge_base/nodes/`.

## Input Contract

Read:

- target statement
- target proof
- target kind
- labels explicitly cited as `\ref{label}`
- verified node files under `knowledge_base/nodes/`

Do not use MCP tools, web search, arXiv search, events, runtime logs, or
generator memory.

## Procedure

1. Extract every `\ref{label}` from the statement and proof.
2. Convert each label to its node filename by replacing `:` with `_` and
   appending `.md`.
3. Read the corresponding file from `knowledge_base/nodes/`.
4. If the file is missing, record a gap unless the proof materially depends on
   the missing statement as a decisive step; then record a critical error.
5. Inspect the dependency node's rendered kind/metadata. If it is an
   `external_theorem`, record status `verified_external_theorem_node`; otherwise
   use `verified_in_nodes`.
6. Compare each use in the target proof with the dependency's rendered
   statement. If the proof uses a stronger claim, extra hypothesis, unstated
   corollary, or incompatible definition, record a gap or critical error.
7. For citations to external papers that are not represented by a verified
   `external_theorem` node, do not search externally. Record
   `insufficient_information`; classify as a gap or critical error depending on
   whether the proof materially relies on it.
8. Use `not_applicable` only when an apparent external-reference slot is
   structurally present but no actual external citation is being relied on at
   that location.

## Output Contribution

Prepare `external_reference_checks` entries like:

```json
{
  "location": "proof paragraph 2",
  "reference": "paper theorem or \\ref{ext:...}",
  "status": "missing_from_nodes|not_applicable",
  "notes": "The cited result is not available as a verified node."
}
```

Also contribute any dependency-related gaps or critical errors to the final
report. Do not write files.
