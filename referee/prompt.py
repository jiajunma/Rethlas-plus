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
            "Do not edit files, tests, docs, agent instructions, or workspace artifacts during this job.",
            "For published-source study admission, focus on whether the extracted statement, hypotheses, and notation match the local source context.",
            "Accept cited literature provisionally as published-source premises unless local context makes the cited use suspect.",
            "Do not block acceptance merely because bibliography evidence is unavailable; record loose citation details instead.",
            "Requested-detail issues about the statement itself must block acceptance.",
            "Do not write theorem nodes; recommended KB updates are proposals only.",
            "When reviewing a source such as a paper, book, or thesis, include a theorem graph:",
            "- theorem_nodes: main definitions, assumptions, lemmas, propositions, theorems, conjectures, corollaries, remarks, and bridge requests;",
            "- labels must be stable across reruns and should combine source id, kind, and locator/title information;",
            "- use node status from proved, conditional, conjectural, assumed, gap, wrong, context, source_claim, review_only;",
            "- include ordinary prose paragraphs as theorem_nodes when they function as implicit definitions, notation conventions, assumptions, unnamed lemmas, criteria, equivalences, reductions, constructions, or theorem-like claims;",
            "- mark explicit environments with extraction_kind=explicit_environment and paragraph-derived nodes with extraction_kind=implicit_paragraph;",
            "- for explicit theorem/proposition/lemma/conjecture/assumption/definition nodes, preserve the source statement text in source_excerpt, including displayed formulas and hypotheses; use statement only for a compact review summary when needed;",
            "- when the original source statement contains displayed formulas, include formula_excerpt with the relevant formula block(s); never replace formulas by prose-only summaries;",
            "- keep source_excerpt/formula_excerpt as source evidence; when OCR or PDF extraction makes the node hard to read, add display_source_excerpt/display_formula_excerpt with normalized Markdown/TeX for dashboard display, and record unresolved transcription doubt in typesetting_notes or typo_findings;",
            "- for paragraph-derived nodes, source_locator and source_note are required; add promotion_confidence and overpromotion_risk when useful;",
            "- external_theorem nodes are review-only by default and must carry scope=review_only unless explicitly proposed as kb_candidate;",
            "- theorem_dependency_edges: directed edges with dependency -> dependent and a short relation;",
            "- edge relation must be one of uses, assumes, depends_on, proves, reduces_to, needs_bridge, supports_verdict, cites, proves_injectivity, proves_exhaustivity;",
            "- node_location_notes: source locators for each theorem node, including page/section/line/span evidence when available;",
            "- typo_findings: mathematical typos, notation drift, wrong references, and OCR-uncertain formula issues with locators.",
            "Flag over-promotion when a paragraph is only expository and should not be treated as a theorem-like node.",
            "Use the source's language for these fields when appropriate.",
            "",
            "## Context packet",
            "```json",
            json.dumps(packet, sort_keys=True, ensure_ascii=False, indent=2),
            "```",
        ]
    )


__all__ = ["compose_prompt"]
