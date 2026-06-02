# Learner Output Schema

```json
{
  "output_schema": "learner_batch_v1",
  "source_id": "src:...",
  "run_id": "learn_...",
  "context_hash": "sha256:...",
  "learning_contract": {
    "proof_capture": "strict",
    "notation_normalization": "canonical"
  },
  "source_spans": [
    {"span_id": "span:...", "span_hash": "sha256:..."}
  ],
  "notation_contexts": [
    {
      "context_id": "notation:...",
      "scope": "batch or source scope",
      "canonical_symbols": [
        {
          "symbol": "Θ",
          "meaning": "G-invariant distribution being studied",
          "source_variants": ["Theta", "OCR variants"]
        }
      ]
    }
  ],
  "candidate_nodes": [
    {
      "label": "def:...",
      "kind": "definition",
      "statement": "...",
      "proof": "",
      "remark": "",
      "source_note": "",
      "extraction_kind": "explicit_environment | implicit_paragraph | reconstructed_statement",
      "source_locator": "Section/page/paragraph locator",
      "source_refs": [
        {"span_id": "span:...", "span_hash": "sha256:..."}
      ]
    },
    {
      "label": "lem:...",
      "kind": "lemma",
      "statement": "Statement written in canonical notation.",
      "proof": "Source-backed proof skeleton written in canonical notation.",
      "proof_status": "proof_sketch_extracted",
      "proof_steps": [
        {"step": "First source-backed proof step.", "source_ref": {"span_id": "span:...", "span_hash": "sha256:..."}}
      ],
      "depends_on": ["def:..."],
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
