"""Decoder for statement-fixer LLM output (v1.4)."""

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

VALID_DECISIONS = frozenset({"fixed", "cannot_fix", "defers_to_human"})


class StatementFixReviewParseError(Exception):
    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class StatementFixReview:
    """One statement-fixer invocation's result."""

    decision: str  # "fixed" | "cannot_fix" | "defers_to_human"
    rationale: str
    confidence: float = 0.0
    # When decision == "fixed":
    fixed_body: str = ""           # the corrected node body (markdown)
    addressed_issues: list[str] = field(default_factory=list)
    # When decision in {cannot_fix, defers_to_human}:
    blocker: str = ""              # what's stopping the fix
    raw: str = ""

    @property
    def writes_body(self) -> bool:
        """True when CLI should apply fixed_body to the staged node."""
        return self.decision == "fixed" and bool(self.fixed_body.strip())


def parse(raw: str) -> StatementFixReview:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(cleaned, required_keys=("decision", "rationale"))
    if blob is None:
        raise StatementFixReviewParseError(
            "no_review_json",
            "no JSON with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise StatementFixReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except StatementFixReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _validate(data: dict[str, Any], *, raw: str) -> StatementFixReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise StatementFixReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise StatementFixReviewParseError(
            "missing_rationale", "rationale must be a non-empty string",
        )

    confidence = coerce_confidence(
        data.get("confidence", 0.0), error_cls=StatementFixReviewParseError,
    )
    fixed_body = coerce_optional_string(
        data.get("fixed_body", ""), "fixed_body",
        error_cls=StatementFixReviewParseError,
    )
    addressed = coerce_str_list(
        data.get("addressed_issues", []), "addressed_issues",
        error_cls=StatementFixReviewParseError,
    )
    blocker = coerce_optional_string(
        data.get("blocker", ""), "blocker",
        error_cls=StatementFixReviewParseError,
    )

    # Discriminated-union constraints
    if decision == "fixed" and not fixed_body.strip():
        raise StatementFixReviewParseError(
            "fixed_requires_fixed_body",
            "decision=fixed requires non-empty fixed_body",
        )
    if decision in ("cannot_fix", "defers_to_human") and not blocker.strip():
        raise StatementFixReviewParseError(
            f"{decision}_requires_blocker",
            f"decision={decision} requires non-empty blocker explanation",
        )

    return StatementFixReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        fixed_body=fixed_body,
        addressed_issues=addressed,
        blocker=blocker,
        raw=raw,
    )


__all__ = [
    "StatementFixReview",
    "StatementFixReviewParseError",
    "VALID_DECISIONS",
    "parse",
]
