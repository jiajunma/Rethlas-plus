"""Verifier verdict decoder (PHASE1 M7).

Codex emits a single JSON verdict at the end of its stdout. The parser
finds the last ``{...}`` block that parses as valid JSON and contains
the expected keys. ANSI codes, reasoning prose, and MCP tool traces
before the verdict are tolerated.

Required verdict shape (§3.5.1):

```json
{
  "verdict": "accepted | gap | critical",
  "verification_hash": "sha256:...",
  "verification_report": {
    "summary": "...",
    "checked_items": [],
    "gaps": [],
    "critical_errors": [],
    "external_reference_checks": []
  },
  "repair_hint": "..."
}
```

Consistency rules (§3.5.1):
- ``accepted`` ⇒ ``gaps == []`` AND ``critical_errors == []``
- ``gap`` ⇒ ``gaps`` non-empty
- ``critical`` ⇒ ``critical_errors`` non-empty
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VALID_VERDICTS = frozenset({"accepted", "gap", "critical"})

# §3.5.1 verification_report subfields.
_REQUIRED_REPORT_FIELDS = (
    "summary",
    "checked_items",
    "gaps",
    "critical_errors",
    "external_reference_checks",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    verification_hash: str
    verification_report: dict[str, Any]
    repair_hint: str

    @property
    def is_accepted(self) -> bool:
        return self.verdict == "accepted"


class VerdictParseError(Exception):
    """Raised when verdict parsing or schema validation fails."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def parse_verdict(raw: str) -> Verdict:
    """Find the last well-shaped JSON verdict in ``raw`` and validate it."""
    cleaned = _ANSI_RE.sub("", raw)
    cleaned = unicodedata.normalize("NFC", cleaned)

    candidate = _find_last_verdict_blob(cleaned)
    if candidate is None:
        raise VerdictParseError(
            "no_verdict_json",
            "could not locate a JSON object with verdict + verification_hash keys",
        )
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise VerdictParseError("json_decode_error", str(exc)) from exc
    return _validate_verdict(data)


def _find_last_verdict_blob(text: str) -> str | None:
    """Walk the text right-to-left for the last balanced ``{...}`` that
    parses as JSON with ``verdict`` + ``verification_hash`` keys.
    """
    candidates: list[str] = []
    # Sweep through every ``{`` and try to find a matching ``}``. Use
    # depth tracking so nested braces (e.g. inside a JSON string) are
    # respected. Quotes within JSON are also handled.
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
            and "verification_hash" in data
        ):
            candidates.append(blob)
        i = end + 1
    if not candidates:
        return None
    return candidates[-1]


def _matching_brace(text: str, start: int) -> int | None:
    """Return the index of the ``}`` that matches ``text[start] == '{'``.

    Tracks JSON string boundaries so braces inside strings are ignored.
    Returns ``None`` if no balanced match is found.
    """
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


def _validate_verdict(data: dict[str, Any]) -> Verdict:
    verdict = data.get("verdict")
    if verdict not in _VALID_VERDICTS:
        raise VerdictParseError(
            "invalid_verdict", f"verdict {verdict!r} not in {sorted(_VALID_VERDICTS)}"
        )
    vh = data.get("verification_hash")
    if not isinstance(vh, str) or not vh:
        raise VerdictParseError("missing_verification_hash", "must be a non-empty string")
    report = data.get("verification_report")
    if not isinstance(report, dict):
        raise VerdictParseError(
            "missing_verification_report", "must be a JSON object"
        )
    for field in _REQUIRED_REPORT_FIELDS:
        if field not in report:
            raise VerdictParseError(
                "verification_report_field_missing",
                f"verification_report missing {field!r}",
            )
    if not isinstance(report["summary"], str):
        raise VerdictParseError("verification_report_summary_not_string", "")
    for list_field in ("checked_items", "gaps", "critical_errors", "external_reference_checks"):
        if not isinstance(report[list_field], list):
            raise VerdictParseError(
                "verification_report_field_not_list", list_field
            )
    repair_hint = data.get("repair_hint", "")
    if not isinstance(repair_hint, str):
        raise VerdictParseError("repair_hint_not_string", "")

    # Consistency rules.
    gaps = report["gaps"]
    crit = report["critical_errors"]
    if verdict == "accepted" and (gaps or crit):
        raise VerdictParseError(
            "verdict_consistency",
            "verdict=accepted requires gaps=[] and critical_errors=[]",
        )
    if verdict == "gap" and not gaps:
        raise VerdictParseError(
            "verdict_consistency", "verdict=gap requires non-empty gaps[]"
        )
    if verdict == "critical" and not crit:
        raise VerdictParseError(
            "verdict_consistency",
            "verdict=critical requires non-empty critical_errors[]",
        )

    return Verdict(
        verdict=verdict,
        verification_hash=vh,
        verification_report=report,
        repair_hint=repair_hint,
    )


__all__ = ["Verdict", "VerdictParseError", "parse_verdict"]
