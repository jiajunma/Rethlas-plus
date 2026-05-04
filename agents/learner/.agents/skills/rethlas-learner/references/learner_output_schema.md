# Learner Output Schema

```json
{
  "output_schema": "learner_batch_v1",
  "source_id": "src:...",
  "run_id": "learn_...",
  "context_hash": "sha256:...",
  "source_spans": [
    {"span_id": "span:...", "span_hash": "sha256:..."}
  ],
  "notation_contexts": [],
  "candidate_nodes": [
    {
      "label": "def:...",
      "kind": "definition",
      "statement": "...",
      "proof": "",
      "remark": "",
      "source_note": "",
      "source_refs": [
        {"span_id": "span:...", "span_hash": "sha256:..."}
      ]
    }
  ],
  "dependency_edges": [],
  "bridge_requests": [],
  "verification_requests": [
    {"target": "def:...", "kind": "verify_definition"}
  ],
  "issues": [],
  "summary": ""
}
```
