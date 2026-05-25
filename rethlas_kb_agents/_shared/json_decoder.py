"""Shared "last balanced JSON blob" decoder + coercion helpers.

Ported from the proven ``verifier/decoder.py`` in Rethlas-original
(see ``rethlas_kb_agents/statement_verifier/decoder.py`` docstring
for the full rationale). Every agent decoder uses the same algorithm:

1. ``strip_for_parse(raw)`` cleans ANSI escapes + NFC-normalizes.
2. ``find_last_json_blob(cleaned, required_keys=(...))`` sweeps
   left-to-right and returns the **last** balanced ``{...}`` that
   parses as JSON and contains every named required key.
3. Caller json-loads the blob and runs its own schema validation
   (using ``coerce_*`` helpers below where useful).
"""

from __future__ import annotations

import json
import re
import unicodedata

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class DecoderError(Exception):
    """Generic decoder error — subclassed per agent for typed handling."""

    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


# ---------------------------------------------------------------------------
# Text cleanup + blob sweep
# ---------------------------------------------------------------------------
def strip_for_parse(raw: str) -> str:
    """Strip ANSI escape codes + NFC-normalise so curly quotes don't break parse."""
    cleaned = _ANSI_RE.sub("", raw or "")
    return unicodedata.normalize("NFC", cleaned)


def find_last_json_blob(text: str, *, required_keys: tuple[str, ...]) -> str | None:
    """Return the last balanced ``{...}`` that parses AND has all required keys.

    Sweeps left-to-right (so multiple competing blobs from LLM "let me try
    again" reasoning all get collected; the rightmost one wins). Respects
    JSON string boundaries so braces inside ``"..."`` don't confuse depth
    tracking.
    """
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
        if isinstance(data, dict) and all(k in data for k in required_keys):
            candidates.append(blob)
        i = end + 1
    return candidates[-1] if candidates else None


def _matching_brace(text: str, start: int) -> int | None:
    """Index of the ``}`` matching ``text[start] == '{'``; ``None`` if unbalanced."""
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
# Coercion helpers (typed-list parsing with consistent error reasons)
# ---------------------------------------------------------------------------
def coerce_confidence(
    value: object, *, error_cls: type[Exception] = DecoderError,
) -> float:
    """Coerce to float, clamp to [0, 1], or raise ``error_cls``."""
    if value is None:
        return 0.0
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise error_cls("confidence_not_numeric", f"got {value!r}") from exc
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def coerce_optional_string(
    value: object,
    field_name: str,
    *,
    error_cls: type[Exception] = DecoderError,
) -> str:
    """Return the string, or "" for None / missing; raise on wrong type."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise error_cls(
            f"{field_name}_not_string", f"got {type(value).__name__}",
        )
    return value


def coerce_str_list(
    value: object,
    field_name: str,
    *,
    error_cls: type[Exception] = DecoderError,
) -> list[str]:
    """Return a ``list[str]``; raise on non-list or non-string items."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise error_cls(
            f"{field_name}_not_list", f"got {type(value).__name__}",
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise error_cls(
                f"{field_name}_item_not_string",
                f"got {type(item).__name__}",
            )
        out.append(item)
    return out
