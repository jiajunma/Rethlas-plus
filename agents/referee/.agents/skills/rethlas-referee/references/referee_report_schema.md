# Referee Report Schema

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
  "requested_details": [
    {
      "issue_type": "requested_detail",
      "severity": "major",
      "requested_detail": "State the missing lemma.",
      "blocks_verdict": true
    }
  ],
  "issues": [],
  "counterexample_attempts": [],
  "external_reference_checks": [
    {
      "citation_key": "...",
      "applicability": "resolved_exact",
      "evidence_hash": "sha256:...",
      "quoted_or_paraphrased_statement": "..."
    }
  ],
  "extraction_quality_checks": [],
  "theorem_nodes": [
    {
      "label": "thm:source_main",
      "kind": "theorem",
      "title": "Main theorem",
      "statement": "Compact referee summary of the theorem.",
      "source_excerpt": "Original theorem statement copied from the source, including hypotheses and displayed formulas.",
      "formula_excerpt": "Displayed formula block(s) from the source statement, when present.",
      "display_source_excerpt": "Optional dashboard-ready Markdown/TeX transcription; never replaces source_excerpt.",
      "display_formula_excerpt": "Optional dashboard-ready TeX formula block; never replaces formula_excerpt.",
      "typesetting_notes": "Optional notes for OCR/layout/transcription uncertainty.",
      "status": "proved | conditional | conjectural | gap | wrong",
      "extraction_kind": "explicit_environment",
      "source_locator": "Chapter 5, Theorem 5.1, PDF p. 42",
      "source_note": "Explicit theorem environment.",
      "source_refs": [
        {
          "span_id": "span:...",
          "locator": "page/section/theorem number",
          "span_hash": "sha256:..."
        }
      ]
    },
    {
      "label": "rem:source_warning",
      "kind": "remark",
      "title": "Remark on the limiting hypothesis",
      "statement": "...",
      "status": "context",
      "source_refs": [
        {
          "span_id": "span:...",
          "locator": "page/section/remark number",
          "span_hash": "sha256:..."
        }
      ]
    },
    {
      "label": "def:implicit_notation",
      "kind": "definition",
      "title": "Implicit notation convention",
      "statement": "...",
      "status": "source_claim",
      "extraction_kind": "implicit_paragraph",
      "source_locator": "PDF p. 17, paragraph after (2.3)",
      "source_note": "The paragraph is not labelled Definition, but it introduces notation used by later theorems.",
      "promotion_confidence": 0.86,
      "overpromotion_risk": false,
      "source_refs": []
    },
    {
      "label": "ext:source_cited_theorem",
      "kind": "external_theorem",
      "title": "Cited theorem used in the main proof",
      "statement": "...",
      "status": "review_only",
      "scope": "review_only",
      "source_refs": []
    }
  ],
  "theorem_dependency_edges": [
    {
      "dependency": "lem:key_input",
      "dependent": "thm:source_main",
      "relation": "uses | assumes | proves_exhaustivity | proves_injectivity | needs_bridge",
      "source_refs": []
    }
  ],
  "node_location_notes": [
    {
      "label": "thm:source_main",
      "locator": "Chapter 5, Theorem 5.1, PDF p. 42",
      "note": "Main theorem statement.",
      "source_refs": []
    }
  ],
  "typo_findings": [
    {
      "typo_id": "typo:source:p42_formula_5_1",
      "severity": "major",
      "locator": "PDF p. 42, formula (5.1)",
      "observed": "...",
      "suggested": "...",
      "reason": "The displayed exponent conflicts with the following proof line.",
      "source_refs": []
    }
  ],
  "recommended_kb_updates": [],
  "summary": ""
}
```
