---
name: rethlas-learner
description: Learn mathematical facts from supplied source spans into a Rethlas learner_batch_v1 JSON batch with source provenance, proof skeletons, dependencies, notation contexts, bridge requests, and verification requests.
---

# Rethlas Learner

Use only the source spans and context packet in the prompt. Do not search or
read workspace files unless an exact path appears in `allowed_read_paths`.

Return exactly one raw JSON object with `output_schema: "learner_batch_v1"`.
Use the prompted `run_id`, `context_hash`, `source_id`, every cited
`span_id`, and every cited `span_hash` exactly. If the prompt has a non-empty
`learning_contract`, echo it exactly. Candidate nodes use `kind`, not `type`.

Every candidate node must include `source_refs` entries with exact `span_id`
and `span_hash` values from `source_spans`. Every `proof_steps[].source_ref`
that you include must also carry exact `span_id` and `span_hash`.

If `notation_context.expected_labels` has one label, emit exactly one
candidate node with that label. Use canonical notation from
`notation_context.canonical_symbols` in `statement`, `proof`, and
`proof_steps`; keep OCR/source variants only in `notation_contexts` or
`source_note`. Keep neighboring remarks or examples out of a definition
statement unless the expected label targets them.

For every lemma/proposition/theorem candidate, include source-backed `proof`,
`proof_status`, non-empty `proof_steps`, `depends_on`, and dependency edges when
the span supplies proof logic. In strict proof-capture mode, a statement-only
proof node must include `statement_only_reason` and be blocked by a
`bridge_requests[]` entry with `for_label` or `blocks` naming the candidate, or
by an `issues[]` entry naming the candidate.

Definitions require `verification_requests[]` with `kind: "verify_definition"`.
External theorems require `kind: "verify_external_theorem"` and source/citation
provenance.

Minimal output shape:

```json
{
  "output_schema": "learner_batch_v1",
  "source_id": "src:...",
  "run_id": "learn_...",
  "context_hash": "sha256:...",
  "learning_contract": {},
  "source_spans": [{"span_id": "span:...", "span_hash": "sha256:..."}],
  "notation_contexts": [],
  "candidate_nodes": [],
  "dependency_edges": [],
  "bridge_requests": [],
  "verification_requests": [],
  "issues": [],
  "summary": ""
}
```
