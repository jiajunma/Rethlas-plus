"""Prompt composer for Phase 3 referee jobs."""

from __future__ import annotations

import json

from common.runtime.jobs_v2 import RoleJobRecord


def compose_prompt(rec: RoleJobRecord) -> str:
    packet = {
        "task": "review_source_or_node",
        "target": rec.target,
        "target_hashes": rec.input.get("target_hashes", {}),
        "source_id": rec.input.get("source_id", ""),
        "source_spans": rec.input.get("source_spans", []),
        "node_records": rec.input.get("node_records", []),
        "citation_context": rec.input.get("citation_context", []),
        "budgets": rec.input.get("budgets", {}),
        "output_schema": "referee_report_v1",
    }
    return "\n".join(
        [
            "# Rethlas referee job",
            "",
            "Review the supplied source/node target for mathematical correctness.",
            "Return one strict JSON object, with no markdown fence, matching output_schema=referee_report_v1.",
            "For published-source study admission, focus on whether the extracted statement, hypotheses, and notation match the local source context.",
            "Accept cited literature provisionally as published-source premises unless local context makes the cited use suspect.",
            "Do not block acceptance merely because bibliography evidence is unavailable; record loose citation details instead.",
            "Requested-detail issues about the statement itself must block acceptance.",
            "Do not write theorem nodes; recommended KB updates are proposals only.",
            "",
            "## Context packet",
            "```json",
            json.dumps(packet, sort_keys=True, ensure_ascii=False, indent=2),
            "```",
        ]
    )


__all__ = ["compose_prompt"]
