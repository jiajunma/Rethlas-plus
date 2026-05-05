"""Refute task — DESIGN §8.

Two pieces:

1. ``RefuteTask`` — a callable that takes a claim, returns ``RefuteVerdict``.
   Concrete implementations wrap an LLM call. Tests use ``StubRefuteTask``.

2. ``EQUIVALENCE_PROMPT`` — the prompt template ``bridge.py`` uses to ask
   "are these two claim_texts equivalent?". Lives here because both
   prompts share the same critic-style framing.

Pure stdlib. No LLM dependency at import time — callers inject the
backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class RefuteVerdictKind(str, Enum):
    COUNTEREXAMPLE = "counterexample"
    FRAGILE = "fragile"
    HIDDEN_ASSUMPTION = "hidden_assumption"
    NO_ISSUE = "no_issue"


@dataclass(frozen=True, slots=True)
class RefuteVerdict:
    """Decoded refute task output. JSON schema mirrors DESIGN §8."""

    verdict_kind: RefuteVerdictKind
    severity: float  # ∈ [0, 1]
    details: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.severity <= 1.0):
            object.__setattr__(self, "severity", max(0.0, min(1.0, self.severity)))

    @property
    def found_counterexample(self) -> bool:
        return self.verdict_kind is RefuteVerdictKind.COUNTEREXAMPLE


REFUTE_PROMPT = """\
You are a hostile reviewer of a mathematical claim.
Find ONE of:
  (a) a concrete counterexample,
  (b) edge cases where the claim is fragile,
  (c) hidden assumptions used implicitly,
  (d) "no apparent issue" + brief justification.

CLAIM:
{claim}

Reply with strict JSON:
{{
  "verdict": "counterexample" | "fragile" | "hidden_assumption" | "no_issue",
  "details": "...",
  "severity": 0.0..1.0
}}
"""


EQUIVALENCE_PROMPT = """\
Decide whether claim A and claim B are mathematically equivalent.
Allow renaming of bound variables and trivial reformulations.
Reply with strict JSON:
{{
  "equivalent": "YES_strict" | "YES_modulo_renaming" | "NO",
  "reason": "..."
}}

CLAIM A:
{a}

CLAIM B:
{b}
"""


# ---------------------------------------------------------------------------
# Callable Protocol + adapters.
# ---------------------------------------------------------------------------
class RefuteTask(Protocol):
    def __call__(self, claim_text: str) -> RefuteVerdict: ...


@dataclass(frozen=True, slots=True)
class CallableRefuteTask:
    """Adapter wrapping any ``(str) -> RefuteVerdict`` callable."""

    fn: Callable[[str], RefuteVerdict]

    def __call__(self, claim_text: str) -> RefuteVerdict:
        return self.fn(claim_text)


@dataclass(frozen=True, slots=True)
class StubRefuteTask:
    """Constant-verdict refute task for tests."""

    verdict: RefuteVerdict

    def __call__(self, claim_text: str) -> RefuteVerdict:
        return self.verdict


__all__ = [
    "CallableRefuteTask",
    "EQUIVALENCE_PROMPT",
    "REFUTE_PROMPT",
    "RefuteTask",
    "RefuteVerdict",
    "RefuteVerdictKind",
    "StubRefuteTask",
]
