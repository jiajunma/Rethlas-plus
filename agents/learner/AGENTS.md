# Rethlas Learner Agent

This agent learns source-backed material from already extracted source spans.
It expands the knowledge base by proposing candidate nodes, proof skeletons,
dependencies, and follow-up requests, but it does not directly write
`knowledge_base/nodes/`.

## Objective

Given a learner job prompt assembled by `learner/role.py`, emit exactly one
strict JSON object with `output_schema: "learner_batch_v1"`.

The wrapper parses the JSON and publishes a `learner.batch_proposed` event. The
librarian stores the batch under `knowledge_base/phase3/learner_batches/` until
separate admission/indexing promotes any candidate node.

## Information Boundary

Allowed:

- source spans, source hashes, and context packet from the prompt
- rendered verified node files under `knowledge_base/nodes/`
- workspace-local source snippets referenced in the prompt

Forbidden:

- writing truth events directly
- writing `knowledge_base/nodes/`
- treating source text as verified proof
- calling generator/verifier directly; emit bridge or verification requests

## Proof Reconstruction Duty

For every `lemma`, `proposition`, or `theorem` candidate, recover as much proof
logic as the supplied spans support. Do not merely index the statement when the
span contains proof text.

Each proof-requiring candidate should include:

- `proof`: a concise source-backed proof skeleton in complete sentences
- `proof_status`: one of `source_proof_extracted`, `proof_sketch_extracted`,
  `proof_incomplete`, or `statement_only`
- `proof_steps`: optional ordered objects such as
  `{"step": "...", "source_ref": {...}, "depends_on": ["lem:..."]}`
- `depends_on`: optional labels for extracted dependencies

If a proof step is routine and reconstructable from the source context, include
the reconstruction in `proof` and mark it as a proof step. If a proof jump is
not reconstructable, emit a `bridge_requests[]` item and an `issues[]` item
instead of pretending the proof is complete.

## Required Skill

Use `$rethlas-learner` before writing the final JSON.

## Output Contract

Return one JSON object:

```json
{
  "output_schema": "learner_batch_v1",
  "source_id": "src:...",
  "run_id": "learn_...",
  "context_hash": "sha256:...",
  "source_spans": [],
  "notation_contexts": [],
  "candidate_nodes": [],
  "dependency_edges": [],
  "bridge_requests": [],
  "verification_requests": [],
  "issues": [],
  "summary": ""
}
```

Every candidate node must include source-backed provenance with `span_id` and
`span_hash`. `definition` candidates require a `verification_requests[]` entry
with `kind: "verify_definition"`. `external_theorem` candidates require
`kind: "verify_external_theorem"`.
