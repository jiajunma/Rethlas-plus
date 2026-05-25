"""Decoder for statement-verifier LLM output (issue #7, hardened with QED lessons).

Both Codex and Claude have a habit of wrapping their JSON answer in
reasoning prose — sometimes multiple JSON-looking objects appear in
the same response. The proven-robust strategy from the legacy
``verifier/decoder.py`` is:

1. Strip ANSI escape codes (codex CLI sometimes leaks them).
2. NFC-normalise the text so curly quotes don't break parsing.
3. Sweep the cleaned text **left-to-right** for every balanced
   ``{...}`` blob (respecting strings + escapes so braces inside
   ``"..."`` don't confuse depth tracking).
4. Keep only blobs that parse as JSON and contain BOTH ``decision``
   and ``rationale`` keys.
5. Return the **last** such blob — that's the agent's final answer.
6. Validate the decision token against the allowed set, clamp the
   confidence to [0, 1], and enforce **per-decision required-field
   rules** (e.g. ``decision=needs_definition`` requires a non-empty
   ``missing_definitions`` list).

The per-decision rules turn the JSON into a discriminated union: the
LLM can't say "context_insufficient" without explaining what's missing.

If no candidate blob is found, raise :class:`StatementReviewParseError`
so the role layer can decide whether to retry, fall back, or surface
the failure to the user.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

VALID_DECISIONS = frozenset({
    "accepted",
    "needs_definition",
    "generality_concern",
    "formulation_issue",
    "context_insufficient",
})


@dataclass(frozen=True, slots=True)
class StatementReview:
    """Typed view of one statement-verifier verdict.

    ``raw`` carries the original LLM stdout so callers writing a
    review file to disk can preserve the full reasoning trace
    alongside the parsed verdict. ``quoted_statement`` is the
    verbatim text the agent claims it judged — comparing it against
    the actual node body lets us detect paraphrase drift.
    """

    decision: str
    rationale: str
    confidence: float = 0.0
    quoted_statement: str = ""
    missing_definitions: list[str] = field(default_factory=list)
    formulation_issues: list[str] = field(default_factory=list)
    generality_notes: str = ""
    context_gap_notes: str = ""
    raw: str = ""

    @property
    def is_accepted(self) -> bool:
        return self.decision == "accepted"

    @property
    def flagged(self) -> bool:
        """True when the decision indicates a problem (anything but accepted)."""
        return self.decision != "accepted"


class StatementReviewParseError(Exception):
    """Raised when the LLM output cannot be parsed into a review.

    ``raw`` carries the original backend stdout so callers (smoke tests,
    debug loggers) can distinguish a malformed-but-real verdict from
    upstream errors (rate limits, transient 5xx) where the LLM never
    actually responded.
    """

    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(raw: str) -> StatementReview:
    """Parse a backend's raw stdout into a :class:`StatementReview`."""
    cleaned = _ANSI_RE.sub("", raw or "")
    cleaned = unicodedata.normalize("NFC", cleaned)

    blob = _find_last_review_blob(cleaned)
    if blob is None:
        raise StatementReviewParseError(
            "no_review_json",
            "could not locate a JSON object with decision + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        # Should be unreachable — _find_last_review_blob filters on parse —
        # but defensive in case the algorithm changes.
        raise StatementReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc
    try:
        return _validate(data, raw=raw)
    except StatementReviewParseError as exc:
        # Re-raise with raw attached so downstream skip / debug logic can
        # see the original LLM output.
        exc.raw = raw or ""
        raise


# ---------------------------------------------------------------------------
# Sweep helpers (ported from verifier/decoder.py)
# ---------------------------------------------------------------------------
def _find_last_review_blob(text: str) -> str | None:
    """Return the last balanced ``{...}`` that parses + has the right keys."""
    candidates: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        end = _matching_brace(text, i)
        if end is None:
            i += 1
            continue
        blob = text[i : end + 1]
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(data, dict) and "decision" in data and "rationale" in data:
            candidates.append(blob)
        i = end + 1
    return candidates[-1] if candidates else None


def _matching_brace(text: str, start: int) -> int | None:
    """Index of the ``}`` matching ``text[start] == '{'``; ``None`` if unbalanced.

    Tracks JSON string boundaries so braces inside ``"..."`` are ignored.
    """
    if text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
def _validate(data: dict[str, Any], *, raw: str) -> StatementReview:
    decision = data.get("decision")
    if decision not in VALID_DECISIONS:
        raise StatementReviewParseError(
            "invalid_decision",
            f"{decision!r} not in {sorted(VALID_DECISIONS)}",
        )
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise StatementReviewParseError(
            "missing_rationale", "rationale must be a non-empty string"
        )

    confidence = _coerce_confidence(data.get("confidence", 0.0))

    quoted_statement = _coerce_optional_string(
        data.get("quoted_statement", ""), "quoted_statement"
    )
    missing = _coerce_str_list(
        data.get("missing_definitions", []), "missing_definitions"
    )
    issues = _coerce_str_list(
        data.get("formulation_issues", []), "formulation_issues"
    )
    generality_notes = _coerce_optional_string(
        data.get("generality_notes", ""), "generality_notes"
    )
    context_gap_notes = _coerce_optional_string(
        data.get("context_gap_notes", ""), "context_gap_notes"
    )

    # ----- Discriminated-union constraints --------------------------------
    # The prompt promises each non-accepted decision comes with the field
    # that justifies it. Enforce here so callers can trust the shape.
    if decision == "needs_definition" and not missing:
        raise StatementReviewParseError(
            "needs_definition_requires_missing_definitions",
            "decision=needs_definition but missing_definitions is empty",
        )
    if decision == "formulation_issue" and not issues:
        raise StatementReviewParseError(
            "formulation_issue_requires_formulation_issues",
            "decision=formulation_issue but formulation_issues is empty",
        )
    if decision == "generality_concern" and not generality_notes.strip():
        raise StatementReviewParseError(
            "generality_concern_requires_generality_notes",
            "decision=generality_concern but generality_notes is empty",
        )
    if decision == "context_insufficient" and not context_gap_notes.strip():
        raise StatementReviewParseError(
            "context_insufficient_requires_context_gap_notes",
            "decision=context_insufficient but context_gap_notes is empty",
        )

    return StatementReview(
        decision=decision,
        rationale=rationale,
        confidence=confidence,
        quoted_statement=quoted_statement,
        missing_definitions=missing,
        formulation_issues=issues,
        generality_notes=generality_notes,
        context_gap_notes=context_gap_notes,
        raw=raw,
    )


def _coerce_confidence(value: object) -> float:
    if value is None:
        return 0.0
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise StatementReviewParseError(
            "confidence_not_numeric", f"got {value!r}"
        ) from exc
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _coerce_optional_string(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise StatementReviewParseError(
            f"{field_name}_not_string",
            f"got {type(value).__name__}",
        )
    return value


def _coerce_str_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StatementReviewParseError(
            f"{field_name}_not_list", f"got {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise StatementReviewParseError(
                f"{field_name}_item_not_string",
                f"got {type(item).__name__}",
            )
        out.append(item)
    return out


__all__ = [
    "StatementReview",
    "StatementReviewParseError",
    "VALID_DECISIONS",
    "parse",
]
