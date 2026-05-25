"""Decoder for source-claim-verifier LLM output (issue #12)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rethlas_kb_agents._shared.json_decoder import (
    coerce_confidence,
    coerce_optional_string,
    coerce_str_list,
    find_last_json_blob,
    strip_for_parse,
)

VALID_DECISIONS = frozenset({
    "accepted",
    "mismatch",
    "proof_gap",
    "proof_critical",
    "cannot_verify",
})


class SourceClaimReviewParseError(Exception):
    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class SourceClaimReview:
    decision: str
    rationale: str
    confidence: float = 0.0
    # Verbatim-quote discipline: the LLM copies the statement it judged
    quoted_node_statement: str = ""
    quoted_source_statement: str = ""
    # When mismatch: enumerated differences
    differences: list[str] = field(default_factory=list)
    # When proof_gap / proof_critical:
    proof_issues: list[str] = field(default_factory=list)
    # When cannot_verify:
    missing_evidence: str = ""
    raw: str = ""

    @property
    def is_accepted(self) -> bool:
        return self.decision == "accepted"


def parse(raw: str) -> SourceClaimReview:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(cleaned, required_keys=("decision", "rationale"))
    if blob is None:
        raise SourceClaimReviewParseError(
            "no_review_json",
            "no JSON with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise SourceClaimReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except SourceClaimReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _validate(data: dict[str, Any], *, raw: str) -> SourceClaimReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise SourceClaimReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise SourceClaimReviewParseError(
            "missing_rationale", "rationale must be a non-empty string",
        )

    confidence = coerce_confidence(
        data.get("confidence", 0.0), error_cls=SourceClaimReviewParseError,
    )
    quoted_node = coerce_optional_string(
        data.get("quoted_node_statement", ""), "quoted_node_statement",
        error_cls=SourceClaimReviewParseError,
    )
    quoted_source = coerce_optional_string(
        data.get("quoted_source_statement", ""), "quoted_source_statement",
        error_cls=SourceClaimReviewParseError,
    )
    differences = coerce_str_list(
        data.get("differences", []), "differences",
        error_cls=SourceClaimReviewParseError,
    )
    proof_issues = coerce_str_list(
        data.get("proof_issues", []), "proof_issues",
        error_cls=SourceClaimReviewParseError,
    )
    missing_evidence = coerce_optional_string(
        data.get("missing_evidence", ""), "missing_evidence",
        error_cls=SourceClaimReviewParseError,
    )

    # Discriminated-union constraints
    if decision == "mismatch" and not differences:
        raise SourceClaimReviewParseError(
            "mismatch_requires_differences",
            "decision=mismatch must list at least one specific difference",
        )
    if decision in ("proof_gap", "proof_critical") and not proof_issues:
        raise SourceClaimReviewParseError(
            f"{decision}_requires_proof_issues",
            f"decision={decision} must list at least one proof issue",
        )
    if decision == "cannot_verify" and not missing_evidence.strip():
        raise SourceClaimReviewParseError(
            "cannot_verify_requires_missing_evidence",
            "decision=cannot_verify must explain in missing_evidence",
        )

    return SourceClaimReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        quoted_node_statement=quoted_node,
        quoted_source_statement=quoted_source,
        differences=differences,
        proof_issues=proof_issues,
        missing_evidence=missing_evidence,
        raw=raw,
    )


__all__ = [
    "SourceClaimReview",
    "SourceClaimReviewParseError",
    "VALID_DECISIONS",
    "parse",
]
