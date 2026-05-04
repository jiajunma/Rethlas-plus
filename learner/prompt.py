"""Prompt composer for Phase 3 learner jobs."""

from __future__ import annotations

import json

from common.runtime.jobs_v2 import RoleJobRecord


def compose_prompt(rec: RoleJobRecord) -> str:
    packet = {
        "task": "learn_source",
        "source_id": rec.input.get("source_id", ""),
        "source_spans": rec.input.get("source_spans", []),
        "page_image_refs": rec.input.get("page_image_refs", []),
        "tex_refs": rec.input.get("tex_refs", []),
        "layout_refs": rec.input.get("layout_refs", []),
        "alignment_refs": rec.input.get("alignment_refs", []),
        "existing_kb_matches": rec.input.get("existing_kb_matches", []),
        "notation_context": rec.input.get("notation_context", {}),
        "budgets": rec.input.get("budgets", {}),
        "output_schema": "learner_batch_v1",
    }
    return "\n".join(
        [
            "# Rethlas learner job",
            "",
            "Learn source-backed mathematical material from the supplied spans.",
            "Return one strict JSON object, with no markdown fence, matching output_schema=learner_batch_v1.",
            "Every candidate node must cite source refs with span_id and span_hash.",
            "For lemma/proposition/theorem candidates, extract as much proof logic as the spans support.",
            "Do not leave proof empty when the source span contains proof text: write a concise proof skeleton with cited dependencies, key reductions, and unresolved jumps.",
            "Use proof_status on proof-requiring candidates: source_proof_extracted, proof_sketch_extracted, proof_incomplete, or statement_only.",
            "When a proof jump is reconstructable from the source context, include it in proof; when it is not, emit a bridge_request and/or issue.",
            "Prefer dependency_edges that connect candidates to extracted dependencies, external theorems, and bridge requests.",
            "For cited literature in a published source, accept the citation provisionally as a published-source premise unless the context itself makes the cited statement suspect.",
            "Do not block learning on bibliography access. Instead, recover the cited statement as precisely as the local context permits and mark citation details as loose when needed.",
            "Spend effort on determining the correct statement, hypotheses, and notation from surrounding context; many statements cannot be read accurately from the theorem line alone.",
            "Definition candidates require verification_request kind=verify_definition.",
            "External theorem candidates require verification_request kind=verify_external_theorem.",
            "",
            "## Context packet",
            "```json",
            json.dumps(packet, sort_keys=True, ensure_ascii=False, indent=2),
            "```",
        ]
    )


__all__ = ["compose_prompt"]
