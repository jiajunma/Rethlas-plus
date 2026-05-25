"""Decoder for proof-gap-filler LLM output (issue #10)."""

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

VALID_DECISIONS = frozenset({"filled", "partial", "cannot_fill"})


class GapFillReviewParseError(Exception):
    """Raised when the gap-filler's LLM output cannot be parsed."""

    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class NewSubLemma:
    """A sub-lemma the gap-filler wants the user to commission separately."""

    id: str             # proposed node id, e.g. "algebra.normal_subgroup_index"
    statement: str      # what the lemma claims
    rationale: str = "" # why this is needed for the parent proof


@dataclass(frozen=True, slots=True)
class GapFillReview:
    """One gap-filler invocation's typed result."""

    decision: str  # "filled" | "partial" | "cannot_fill"
    rationale: str
    confidence: float = 0.0
    # filled / partial only:
    filled_proof: str = ""  # markdown body for the new staged node
    # partial / cannot_fill only:
    gap_remaining: str = ""
    suggested_approaches: list[str] = field(default_factory=list)
    # Sub-lemmas the gap-filler invented and wants commissioned:
    new_sublemmas: list[NewSubLemma] = field(default_factory=list)
    raw: str = ""

    @property
    def is_filled(self) -> bool:
        return self.decision == "filled"

    @property
    def writes_proof(self) -> bool:
        """True when ``apply`` should overwrite the node's proof body."""
        return self.decision in ("filled", "partial") and bool(self.filled_proof.strip())


def parse(raw: str) -> GapFillReview:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(cleaned, required_keys=("decision", "rationale"))
    if blob is None:
        raise GapFillReviewParseError(
            "no_review_json",
            "no JSON with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise GapFillReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except GapFillReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _validate(data: dict[str, Any], *, raw: str) -> GapFillReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise GapFillReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise GapFillReviewParseError(
            "missing_rationale", "rationale must be a non-empty string",
        )

    confidence = _coerce_confidence_gf(data.get("confidence", 0.0))
    filled_proof = _coerce_optional_string_gf(
        data.get("filled_proof", ""), "filled_proof",
    )
    gap_remaining = _coerce_optional_string_gf(
        data.get("gap_remaining", ""), "gap_remaining",
    )
    suggested = _coerce_str_list_gf(
        data.get("suggested_approaches", []), "suggested_approaches",
    )
    new_sublemmas = _coerce_sublemmas(data.get("new_sublemmas", []))

    # Discriminated-union constraints
    if decision == "filled" and not filled_proof.strip():
        raise GapFillReviewParseError(
            "filled_requires_filled_proof",
            "decision=filled but filled_proof is empty",
        )
    if decision in ("partial", "cannot_fill") and not gap_remaining.strip():
        raise GapFillReviewParseError(
            f"{decision}_requires_gap_remaining",
            f"decision={decision} but gap_remaining is empty",
        )

    return GapFillReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        filled_proof=filled_proof,
        gap_remaining=gap_remaining,
        suggested_approaches=suggested,
        new_sublemmas=new_sublemmas,
        raw=raw,
    )


def _coerce_sublemmas(value: object) -> list[NewSubLemma]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GapFillReviewParseError(
            "new_sublemmas_not_list", f"got {type(value).__name__}",
        )
    out: list[NewSubLemma] = []
    for item in value:
        if not isinstance(item, dict):
            raise GapFillReviewParseError(
                "sublemma_not_object", f"got {type(item).__name__}",
            )
        lid = item.get("id")
        statement = item.get("statement")
        if not isinstance(lid, str) or not lid.strip():
            raise GapFillReviewParseError(
                "sublemma_missing_id", "sub-lemma needs a non-empty id",
            )
        if not isinstance(statement, str) or not statement.strip():
            raise GapFillReviewParseError(
                "sublemma_missing_statement",
                f"sub-lemma {lid!r} needs a non-empty statement",
            )
        rationale_val = item.get("rationale", "")
        if rationale_val is None:
            rationale_val = ""
        if not isinstance(rationale_val, str):
            raise GapFillReviewParseError(
                "sublemma_rationale_not_string",
                f"sub-lemma {lid!r}: got {type(rationale_val).__name__}",
            )
        out.append(NewSubLemma(
            id=lid, statement=statement, rationale=rationale_val,
        ))
    return out


# Tiny wrappers that re-raise shared coercion errors as our typed error.
def _coerce_confidence_gf(value: object) -> float:
    return coerce_confidence(value, error_cls=GapFillReviewParseError)


def _coerce_optional_string_gf(value: object, field_name: str) -> str:
    return coerce_optional_string(
        value, field_name, error_cls=GapFillReviewParseError,
    )


def _coerce_str_list_gf(value: object, field_name: str) -> list[str]:
    return coerce_str_list(
        value, field_name, error_cls=GapFillReviewParseError,
    )


__all__ = [
    "GapFillReview",
    "GapFillReviewParseError",
    "NewSubLemma",
    "VALID_DECISIONS",
    "parse",
]
