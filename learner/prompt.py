"""Prompt composer for Phase 3 learner jobs."""

from __future__ import annotations

import json

from common.runtime.jobs_v2 import RoleJobRecord


def compose_prompt(rec: RoleJobRecord) -> str:
    packet = {
        "task": "learn_source",
        "run_id": rec.job_id.replace("-", "_"),
        "context_hash": rec.context_hash,
        "source_id": rec.input.get("source_id", ""),
        "source_spans": rec.input.get("source_spans", []),
        "page_image_refs": rec.input.get("page_image_refs", []),
        "tex_refs": rec.input.get("tex_refs", []),
        "layout_refs": rec.input.get("layout_refs", []),
        "alignment_refs": rec.input.get("alignment_refs", []),
        "allowed_read_paths": rec.input.get("allowed_read_paths", []),
        "existing_kb_matches": rec.input.get("existing_kb_matches", []),
        "notation_context": rec.input.get("notation_context", {}),
        "learning_contract": rec.input.get("learning_contract", {}),
        "budgets": rec.input.get("budgets", {}),
        "output_schema": "learner_batch_v1",
    }
    return "\n".join(
        [
            "# Rethlas learner job",
            "",
            "Use $rethlas-learner.",
            "Return one raw JSON object matching output_schema=learner_batch_v1, no markdown fence.",
            "Use packet run_id/context_hash/source_id and span_hashes exactly; echo non-empty learning_contract exactly.",
            "Use candidate node field kind, not type.",
            "Every candidate node must include source_refs with exact span_id and span_hash from source_spans.",
            "Every proof_steps[].source_ref you include must also carry exact span_id and span_hash.",
            "Use only supplied source_spans unless allowed_read_paths names an exact file. Do not search workspace/runtime/events/reviews/KB artifacts.",
            "If expected_labels has one label, emit exactly one candidate node with that label.",
            "Use canonical notation from notation_context in statement/proof/proof_steps; record source/OCR variants only in notation_contexts/source_note.",
            "Keep neighboring remarks/examples out of a definition statement unless the expected label targets them.",
            "For lemma/proposition/theorem, include proof_status plus proof and proof_steps when proof logic is present.",
            "Strict proof_capture: proof nodes need non-empty proof/proof_steps, except statement_only nodes need statement_only_reason and a bridge_request with for_label or blocks naming that label.",
            "Definitions require verify_definition; external_theorem nodes require verify_external_theorem plus citation/source provenance.",
            "",
            "## Context packet",
            "```json",
            json.dumps(packet, sort_keys=True, ensure_ascii=False, indent=2),
            "```",
        ]
    )


__all__ = ["compose_prompt"]
