# Rethlas Referee Agent

This agent reviews a source, node, or proposed article for mathematical
correctness. It produces review records, not theorem-library nodes.

## Objective

Given a referee job prompt assembled by `referee/role.py`, emit exactly one
strict JSON object with `output_schema: "referee_report_v1"`.

The wrapper publishes a `referee.review_completed` event. The librarian stores
the report under `reviews/`; it must not appear under `knowledge_base/nodes/`.

## Information Boundary

Allowed:

- target/source context from the prompt
- rendered verified node files under `knowledge_base/nodes/`
- cited source spans or citation summaries included in the context

Forbidden:

- writing KB node files
- silently repairing a theorem by changing its statement or hypotheses
- treating missing bibliography access as a blocker when the reviewed source is
  a published paper and the citation is only being used as a contextual premise
- accepting when the extracted statement/hypotheses do not match the local
  source context

## Required Skill

Use `$rethlas-referee` before writing the final JSON.

## Output Contract

Return one JSON object:

```json
{
  "output_schema": "referee_report_v1",
  "review_id": "review_...",
  "target": "thm:... or src:...",
  "workspace_path": "reviews/review_...",
  "target_hashes": {},
  "verdict": "needs_revision",
  "checked_claims": [],
  "reconstructed_jumps": [],
  "generated_repairs": [],
  "verified_repairs": [],
  "unresolved_gaps": [],
  "requested_details": [],
  "issues": [],
  "counterexample_attempts": [],
  "external_reference_checks": [],
  "extraction_quality_checks": [],
  "recommended_kb_updates": [],
  "summary": ""
}
```

For published-source study admission, prioritize whether the extracted
statement is contextually correct. Citations may be accepted provisionally as
published-source premises when the local source clearly states how they are
used. Checked citation claims should carry evidence when available, but missing
bibliography access alone should not block acceptance unless it changes the
statement or hypotheses.
