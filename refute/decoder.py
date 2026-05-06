"""Refute LLM-output decoder (S4-light).

Mirrors :mod:`verifier.decoder`'s right-most-balanced-brace strategy:
strip ANSI, NFC-normalise, walk the cleaned text for ``{...}`` blocks,
keep the last one whose JSON shape matches the refute schema (DESIGN
§8). Maps the parsed dict to
:class:`rethlas_scoring.refute.RefuteVerdict`.

Tolerates:

- ANSI escape codes (Codex / shell colouring)
- Reasoning prose / MCP traces around the JSON
- Multiple JSON blocks (last well-shaped one wins)
- ``severity`` outside [0, 1] (clamped via ``RefuteVerdict.__post_init__``)

Required JSON schema (DESIGN §8 / ``rethlas_scoring/refute.py``)::

    {
      "verdict": "counterexample" | "fragile" | "hidden_assumption" | "no_issue",
      "details": "...",
      "severity": 0.0..1.0
    }
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from rethlas_scoring.refute import RefuteVerdict, RefuteVerdictKind


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VALID_VERDICTS = frozenset(v.value for v in RefuteVerdictKind)


class RefuteParseError(Exception):
    """Raised when refute output parsing or schema validation fails."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def parse_refute_verdict(raw: str) -> RefuteVerdict:
    """Find the last well-shaped JSON refute verdict in ``raw`` and validate it."""
    cleaned = _ANSI_RE.sub("", raw)
    cleaned = unicodedata.normalize("NFC", cleaned)

    blob = _find_last_refute_blob(cleaned)
    if blob is None:
        raise RefuteParseError(
            "no_refute_json",
            "could not locate a JSON object with verdict + details + severity keys",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise RefuteParseError("json_decode_error", str(exc)) from exc
    return _validate(data)


# ---------------------------------------------------------------------------
# Brace walker — copy of verifier.decoder helpers, scoped to refute keys.
# ---------------------------------------------------------------------------
def _find_last_refute_blob(text: str) -> str | None:
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
        if (
            isinstance(data, dict)
            and "verdict" in data
            and "details" in data
            and "severity" in data
        ):
            candidates.append(blob)
        i = end + 1
    if not candidates:
        return None
    return candidates[-1]


def _matching_brace(text: str, start: int) -> int | None:
    if text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    n = len(text)
    for i in range(start, n):
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


def _validate(data: dict[str, Any]) -> RefuteVerdict:
    verdict_str = data.get("verdict")
    if verdict_str not in _VALID_VERDICTS:
        raise RefuteParseError(
            "invalid_verdict",
            f"verdict {verdict_str!r} not in {sorted(_VALID_VERDICTS)}",
        )
    details = data.get("details", "")
    if not isinstance(details, str):
        raise RefuteParseError(
            "details_not_string",
            f"details must be a string, got {type(details).__name__}",
        )
    severity = data.get("severity")
    if isinstance(severity, bool) or not isinstance(severity, (int, float)):
        raise RefuteParseError(
            "severity_not_number",
            f"severity must be a number, got {type(severity).__name__}",
        )
    kind = RefuteVerdictKind(verdict_str)
    return RefuteVerdict(
        verdict_kind=kind,
        severity=float(severity),  # clamped to [0,1] inside the dataclass
        details=details,
    )


__all__ = [
    "RefuteParseError",
    "parse_refute_verdict",
]
