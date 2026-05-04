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
  "recommended_kb_updates": [],
  "summary": ""
}
```
