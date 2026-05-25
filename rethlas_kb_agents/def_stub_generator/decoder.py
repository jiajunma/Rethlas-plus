"""Decoder for def-stub-generator (v1.4)."""

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

VALID_DECISIONS = frozenset({"drafted", "placeholder_only", "cannot_stub"})


class DefStubReviewParseError(Exception):
    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class DefStubReview:
    """One def-stub-generator invocation's result."""

    decision: str  # "drafted" | "placeholder_only" | "cannot_stub"
    rationale: str
    confidence: float = 0.0
    # When decision in {drafted, placeholder_only}:
    proposed_id: str = ""        # e.g. "algebra.normal_subgroup"
    title: str = ""              # human title, e.g. "Normal Subgroup"
    primary_topic: str = ""      # e.g. "algebra" (derived from id if absent)
    topics: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    body: str = ""               # markdown body (def or TODO placeholder)
    # When decision == "cannot_stub":
    blocker: str = ""
    raw: str = ""

    @property
    def writes_stub(self) -> bool:
        return (
            self.decision in ("drafted", "placeholder_only")
            and bool(self.proposed_id.strip())
            and bool(self.title.strip())
        )


def parse(raw: str) -> DefStubReview:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(cleaned, required_keys=("decision", "rationale"))
    if blob is None:
        raise DefStubReviewParseError(
            "no_review_json",
            "no JSON with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise DefStubReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except DefStubReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _validate(data: dict[str, Any], *, raw: str) -> DefStubReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise DefStubReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise DefStubReviewParseError(
            "missing_rationale", "rationale must be a non-empty string",
        )

    confidence = coerce_confidence(
        data.get("confidence", 0.0), error_cls=DefStubReviewParseError,
    )
    proposed_id = coerce_optional_string(
        data.get("proposed_id", ""), "proposed_id",
        error_cls=DefStubReviewParseError,
    )
    title = coerce_optional_string(
        data.get("title", ""), "title", error_cls=DefStubReviewParseError,
    )
    primary_topic = coerce_optional_string(
        data.get("primary_topic", ""), "primary_topic",
        error_cls=DefStubReviewParseError,
    )
    topics = coerce_str_list(
        data.get("topics", []), "topics",
        error_cls=DefStubReviewParseError,
    )
    uses = coerce_str_list(
        data.get("uses", []), "uses", error_cls=DefStubReviewParseError,
    )
    body = coerce_optional_string(
        data.get("body", ""), "body", error_cls=DefStubReviewParseError,
    )
    blocker = coerce_optional_string(
        data.get("blocker", ""), "blocker", error_cls=DefStubReviewParseError,
    )

    # Discriminated-union constraints
    if decision in ("drafted", "placeholder_only"):
        if not proposed_id.strip():
            raise DefStubReviewParseError(
                f"{decision}_requires_proposed_id",
                f"decision={decision} requires non-empty proposed_id",
            )
        if not title.strip():
            raise DefStubReviewParseError(
                f"{decision}_requires_title",
                f"decision={decision} requires non-empty title",
            )
        if not body.strip():
            raise DefStubReviewParseError(
                f"{decision}_requires_body",
                f"decision={decision} requires non-empty body "
                "(use 'TODO: define' for placeholder_only if needed)",
            )
    if decision == "cannot_stub" and not blocker.strip():
        raise DefStubReviewParseError(
            "cannot_stub_requires_blocker",
            "decision=cannot_stub requires non-empty blocker",
        )

    return DefStubReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        proposed_id=proposed_id,
        title=title,
        primary_topic=primary_topic,
        topics=topics,
        uses=uses,
        body=body,
        blocker=blocker,
        raw=raw,
    )


__all__ = [
    "DefStubReview",
    "DefStubReviewParseError",
    "VALID_DECISIONS",
    "parse",
]
