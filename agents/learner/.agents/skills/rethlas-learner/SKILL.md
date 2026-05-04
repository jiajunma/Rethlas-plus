# Rethlas Learner

Use this skill when learning mathematical facts from a source span packet into
Rethlas candidate node batches.

## Workflow

1. Read the context packet and preserve every supplied `span_hash`.
2. Extract definitions, notation, theorem statements, examples, proof skeletons,
   proof dependencies, and citation needs only from supplied source spans.
3. Map each candidate to a descriptive Rethlas label with the correct prefix.
4. Attach source provenance to every candidate node:

```json
{"span_id": "span:...", "span_hash": "sha256:..."}
```

5. For every lemma/proposition/theorem, reconstruct the proof logic as far as
   the source permits:
   - include `proof` unless the span truly gives statement only;
   - include `proof_status`;
   - include `proof_steps` when this helps preserve the source proof structure;
   - include `depends_on` and `dependency_edges` for source dependencies.
6. Treat citations inside an already published source as provisional
   published-source premises during study. Do not block extraction because
   `[R1]`, `[Z]`, or another reference is not locally available. Reconstruct
   the cited statement from the local context as accurately as possible and
   record loose citation details as an issue only when they affect the
   recovered statement.
7. Use surrounding paragraphs, notation setup, and proof usage to determine the
   correct statement. The displayed theorem line alone is often insufficient.
8. Emit verification requests instead of marking candidates verified.
9. Emit bridge requests for skipped proof steps instead of invoking generator
   directly.
10. Emit issues for ambiguous notation, missing dependencies, low-confidence OCR,
   or manual transcription needs.

## Required Output

Return one raw JSON object with `output_schema: "learner_batch_v1"`. Do not wrap
it in markdown.

Definitions require:

```json
{"target": "def:...", "kind": "verify_definition"}
```

External theorems require:

```json
{"target": "ext:...", "kind": "verify_external_theorem"}
```

Proof-requiring candidates should look like:

```json
{
  "label": "lem:...",
  "kind": "lemma",
  "statement": "...",
  "proof": "Source-backed proof skeleton...",
  "proof_status": "proof_sketch_extracted",
  "proof_steps": [
    {"step": "Reduce to ...", "source_ref": {"span_id": "span:...", "span_hash": "sha256:..."}}
  ],
  "depends_on": ["lem:..."],
  "source_refs": [{"span_id": "span:...", "span_hash": "sha256:..."}]
}
```
