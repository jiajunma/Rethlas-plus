"""Decoder for counterexample-hunter LLM output (issue #11)."""

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
    "counterexample_found",
    "no_counterexample_found",
    "inconclusive",
})


class CounterexampleHuntReviewParseError(Exception):
    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


@dataclass(frozen=True, slots=True)
class Witness:
    """A concrete counterexample to the stated claim."""

    description: str               # human-readable description
    instantiation: str = ""        # the explicit object(s) (e.g. "G = Z/4, H = ...")
    verification: str = ""         # how the witness was checked (computation log)


@dataclass(frozen=True, slots=True)
class AttemptedCase:
    """One case the hunter tried; for the transparency log."""

    description: str               # "G cyclic of order 6"
    outcome: str                   # "satisfies the claim" / "trivially true" / etc.


@dataclass(frozen=True, slots=True)
class CounterexampleHuntReview:
    """Typed result of one counterexample-hunter invocation."""

    decision: str
    rationale: str
    confidence: float = 0.0
    # When decision == counterexample_found:
    witness: Witness | None = None
    suggested_fixes: list[str] = field(default_factory=list)
    # When decision in {no_counterexample_found, inconclusive}:
    attempted_cases: list[AttemptedCase] = field(default_factory=list)
    # When decision == inconclusive:
    why_inconclusive: str = ""
    raw: str = ""

    @property
    def refuted(self) -> bool:
        return self.decision == "counterexample_found"


def parse(raw: str) -> CounterexampleHuntReview:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(cleaned, required_keys=("decision", "rationale"))
    if blob is None:
        raise CounterexampleHuntReviewParseError(
            "no_review_json",
            "no JSON with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise CounterexampleHuntReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except CounterexampleHuntReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _validate(data: dict[str, Any], *, raw: str) -> CounterexampleHuntReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise CounterexampleHuntReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise CounterexampleHuntReviewParseError(
            "missing_rationale", "rationale must be a non-empty string",
        )

    confidence = coerce_confidence(
        data.get("confidence", 0.0),
        error_cls=CounterexampleHuntReviewParseError,
    )
    witness = _parse_witness(data.get("witness"))
    suggested_fixes = coerce_str_list(
        data.get("suggested_fixes", []), "suggested_fixes",
        error_cls=CounterexampleHuntReviewParseError,
    )
    attempted_cases = _parse_attempted_cases(data.get("attempted_cases", []))
    why_inconclusive = coerce_optional_string(
        data.get("why_inconclusive", ""), "why_inconclusive",
        error_cls=CounterexampleHuntReviewParseError,
    )

    # Discriminated-union constraints
    if decision == "counterexample_found":
        if witness is None:
            raise CounterexampleHuntReviewParseError(
                "counterexample_found_requires_witness",
                "decision=counterexample_found but witness is missing",
            )
    if decision == "no_counterexample_found":
        if not attempted_cases:
            raise CounterexampleHuntReviewParseError(
                "no_counterexample_requires_attempted_cases",
                "no_counterexample_found must list at least one attempted case "
                "(this is a search outcome, not a proof — transparency required)",
            )
    if decision == "inconclusive":
        if not why_inconclusive.strip():
            raise CounterexampleHuntReviewParseError(
                "inconclusive_requires_why",
                "inconclusive must explain why in why_inconclusive",
            )

    return CounterexampleHuntReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        witness=witness,
        suggested_fixes=suggested_fixes,
        attempted_cases=attempted_cases,
        why_inconclusive=why_inconclusive,
        raw=raw,
    )


def _parse_witness(value: object) -> Witness | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CounterexampleHuntReviewParseError(
            "witness_not_object", f"got {type(value).__name__}",
        )
    desc = value.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise CounterexampleHuntReviewParseError(
            "witness_missing_description",
            "witness needs a non-empty description",
        )
    instantiation = coerce_optional_string(
        value.get("instantiation", ""), "witness.instantiation",
        error_cls=CounterexampleHuntReviewParseError,
    )
    verification = coerce_optional_string(
        value.get("verification", ""), "witness.verification",
        error_cls=CounterexampleHuntReviewParseError,
    )
    return Witness(
        description=desc, instantiation=instantiation, verification=verification,
    )


def _parse_attempted_cases(value: object) -> list[AttemptedCase]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CounterexampleHuntReviewParseError(
            "attempted_cases_not_list", f"got {type(value).__name__}",
        )
    out: list[AttemptedCase] = []
    for item in value:
        if not isinstance(item, dict):
            raise CounterexampleHuntReviewParseError(
                "attempted_case_not_object", f"got {type(item).__name__}",
            )
        desc = item.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise CounterexampleHuntReviewParseError(
                "attempted_case_missing_description",
                "every attempted_case needs a non-empty description",
            )
        outcome = coerce_optional_string(
            item.get("outcome", ""), "attempted_case.outcome",
            error_cls=CounterexampleHuntReviewParseError,
        )
        out.append(AttemptedCase(description=desc, outcome=outcome))
    return out


__all__ = [
    "AttemptedCase",
    "CounterexampleHuntReview",
    "CounterexampleHuntReviewParseError",
    "VALID_DECISIONS",
    "Witness",
    "parse",
]
