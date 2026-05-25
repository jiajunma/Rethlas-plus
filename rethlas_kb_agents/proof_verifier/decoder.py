"""Decoders for proof-verifier's three stages (issue #9).

Each stage emits its own JSON schema; parse functions live here.
The shared "last balanced JSON blob" sweep + coercion helpers are
in :mod:`rethlas_kb_agents._shared.json_decoder`.

Verdict vocabularies:

- :class:`JudgeVerdict.difficulty` — ``easy`` | ``hard``
- :class:`JudgeVerdict.verdict` (Easy only) — ``accepted`` | ``gap`` | ``critical``
- :class:`StructuralVerdict.verdict` — ``pass`` | ``fail``
- :class:`DetailedVerdict.verdict` — ``accepted`` | ``gap`` | ``critical`` | ``uncertain``
- :class:`StepVerdict.verdict` — ``pass`` | ``fail`` | ``uncertain``
- :class:`RigorIssue.severity` — ``fatal`` | ``minor``
- :class:`ProofReview.final_verdict` — ``accepted`` | ``gap`` | ``critical`` | ``uncertain``
"""

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

# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------
class ProofReviewParseError(Exception):
    """Raised when an LLM response cannot be parsed into the expected verdict."""

    def __init__(self, reason: str, detail: str = "", *, raw: str = "") -> None:
        msg = f"{reason}" + (f": {detail}" if detail else "")
        super().__init__(msg)
        self.reason = reason
        self.detail = detail
        self.raw = raw


VALID_JUDGE_DIFFICULTIES = frozenset({"easy", "hard"})
VALID_JUDGE_VERDICTS = frozenset({"accepted", "gap", "critical"})
VALID_STRUCTURAL_VERDICTS = frozenset({"pass", "fail"})
VALID_STRUCTURAL_CHECK_NAMES = frozenset({
    "statement_quality",
    "alignment",
    "completeness",
    "architecture",
})
VALID_DETAILED_VERDICTS = frozenset({"accepted", "gap", "critical", "uncertain"})
VALID_STEP_VERDICTS = frozenset({"pass", "fail", "uncertain"})
VALID_RIGOR_SEVERITIES = frozenset({"fatal", "minor"})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """Stage 1 — difficulty classification (+ optional Easy verdict)."""

    difficulty: str
    rationale: str
    # Populated only when difficulty == "easy":
    verdict: str | None = None
    gaps: list[str] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: str = ""

    @property
    def is_easy(self) -> bool:
        return self.difficulty == "easy"


@dataclass(frozen=True, slots=True)
class StructuralCheck:
    name: str
    verdict: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class StructuralVerdict:
    """Stage 2 — high-level architectural checks."""

    verdict: str  # "pass" | "fail"
    rationale: str
    checks: list[StructuralCheck] = field(default_factory=list)
    confidence: float = 0.0
    raw: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


@dataclass(frozen=True, slots=True)
class StepVerdict:
    step_id: str
    claim: str
    verdict: str  # "pass" | "fail" | "uncertain"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RigorIssue:
    severity: str  # "fatal" | "minor"
    claim: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DetailedVerdict:
    """Stage 3 — step-by-step verification."""

    verdict: str
    rationale: str
    step_verdicts: list[StepVerdict] = field(default_factory=list)
    rigor_issues: list[RigorIssue] = field(default_factory=list)
    confidence: float = 0.0
    raw: str = ""

    @property
    def is_accepted(self) -> bool:
        return self.verdict == "accepted"


@dataclass(frozen=True, slots=True)
class ProofReview:
    """Aggregate result from running the pipeline (some stages may be skipped)."""

    final_verdict: str  # "accepted" | "gap" | "critical" | "uncertain"
    rationale: str
    depth_requested: str  # "auto" | "easy" | "structural" | "detailed"
    judge: JudgeVerdict | None = None
    structural: StructuralVerdict | None = None
    detailed: DetailedVerdict | None = None
    # Which stage produced final_verdict; useful for review-file metadata.
    decisive_stage: str = ""  # "judge" | "structural" | "detailed"
    short_circuited_at: str | None = None  # "judge_easy" | "structural_fail" | None

    @property
    def is_accepted(self) -> bool:
        return self.final_verdict == "accepted"

    @property
    def stages_run(self) -> list[str]:
        return [
            name
            for name, stage in (
                ("judge", self.judge),
                ("structural", self.structural),
                ("detailed", self.detailed),
            )
            if stage is not None
        ]


# ---------------------------------------------------------------------------
# Stage 1 — judge
# ---------------------------------------------------------------------------
def parse_judge(raw: str) -> JudgeVerdict:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(
        cleaned, required_keys=("difficulty", "rationale"),
    )
    if blob is None:
        raise ProofReviewParseError(
            "no_judge_json",
            "no JSON with difficulty + rationale keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ProofReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc

    difficulty = data.get("difficulty")
    if difficulty not in VALID_JUDGE_DIFFICULTIES:
        raise ProofReviewParseError(
            "invalid_difficulty",
            f"{difficulty!r} not in {sorted(VALID_JUDGE_DIFFICULTIES)}",
            raw=raw or "",
        )
    rationale = _require_nonempty_str(data, "rationale", raw=raw)

    verdict = data.get("verdict")
    gaps = _coerce_str_list_pv(data.get("gaps", []), "gaps", raw=raw)
    critical = _coerce_str_list_pv(
        data.get("critical_errors", []), "critical_errors", raw=raw,
    )
    confidence = _coerce_confidence_pv(data.get("confidence", 0.0), raw=raw)

    if difficulty == "easy":
        if verdict not in VALID_JUDGE_VERDICTS:
            raise ProofReviewParseError(
                "easy_judge_requires_valid_verdict",
                f"{verdict!r} not in {sorted(VALID_JUDGE_VERDICTS)}",
                raw=raw or "",
            )
        # Sub-discriminated-union: gap ⇒ gaps non-empty, critical ⇒ critical_errors non-empty
        if verdict == "gap" and not gaps:
            raise ProofReviewParseError(
                "easy_judge_gap_requires_gaps",
                "easy judge with verdict=gap must list at least one gap",
                raw=raw or "",
            )
        if verdict == "critical" and not critical:
            raise ProofReviewParseError(
                "easy_judge_critical_requires_critical_errors",
                "easy judge with verdict=critical must list at least one critical_error",
                raw=raw or "",
            )
    else:
        # Hard: the verdict + gaps + critical fields should not appear, but if
        # they do, we tolerate them (the LLM might over-explain).
        verdict = None
        gaps = []
        critical = []

    return JudgeVerdict(
        difficulty=difficulty,
        rationale=rationale,
        verdict=verdict,
        gaps=gaps,
        critical_errors=critical,
        confidence=confidence,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Stage 2 — structural
# ---------------------------------------------------------------------------
def parse_structural(raw: str) -> StructuralVerdict:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(
        cleaned, required_keys=("verdict", "rationale", "checks"),
    )
    if blob is None:
        raise ProofReviewParseError(
            "no_structural_json",
            "no JSON with verdict + rationale + checks keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ProofReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc

    verdict = data.get("verdict")
    if verdict not in VALID_STRUCTURAL_VERDICTS:
        raise ProofReviewParseError(
            "invalid_structural_verdict",
            f"{verdict!r} not in {sorted(VALID_STRUCTURAL_VERDICTS)}",
            raw=raw or "",
        )
    rationale = _require_nonempty_str(data, "rationale", raw=raw)
    confidence = _coerce_confidence_pv(data.get("confidence", 0.0), raw=raw)

    checks_raw = data.get("checks", [])
    if not isinstance(checks_raw, list):
        raise ProofReviewParseError(
            "structural_checks_not_list",
            f"got {type(checks_raw).__name__}",
            raw=raw or "",
        )
    checks: list[StructuralCheck] = []
    seen_names: set[str] = set()
    for item in checks_raw:
        check = _parse_structural_check(item, raw=raw)
        if check.name in seen_names:
            raise ProofReviewParseError(
                "duplicate_structural_check",
                f"check {check.name!r} appears more than once",
                raw=raw or "",
            )
        seen_names.add(check.name)
        checks.append(check)

    # Consistency rule: verdict=fail ⇒ at least one sub-check must fail.
    if verdict == "fail" and not any(c.verdict == "fail" for c in checks):
        raise ProofReviewParseError(
            "structural_fail_requires_failed_check",
            "verdict=fail but every individual check passed",
            raw=raw or "",
        )

    return StructuralVerdict(
        verdict=verdict,
        rationale=rationale,
        checks=checks,
        confidence=confidence,
        raw=raw,
    )


def _parse_structural_check(item: object, *, raw: str) -> StructuralCheck:
    if not isinstance(item, dict):
        raise ProofReviewParseError(
            "structural_check_not_object",
            f"got {type(item).__name__}",
            raw=raw or "",
        )
    name = item.get("name")
    if name not in VALID_STRUCTURAL_CHECK_NAMES:
        raise ProofReviewParseError(
            "invalid_structural_check_name",
            f"{name!r} not in {sorted(VALID_STRUCTURAL_CHECK_NAMES)}",
            raw=raw or "",
        )
    verdict = item.get("verdict")
    if verdict not in VALID_STRUCTURAL_VERDICTS:
        raise ProofReviewParseError(
            "invalid_structural_check_verdict",
            f"check {name!r}: {verdict!r} not in {sorted(VALID_STRUCTURAL_VERDICTS)}",
            raw=raw or "",
        )
    notes = _coerce_optional_string_pv(item.get("notes", ""), "structural_check_notes", raw=raw)
    return StructuralCheck(name=name, verdict=verdict, notes=notes)


# ---------------------------------------------------------------------------
# Stage 3 — detailed
# ---------------------------------------------------------------------------
def parse_detailed(raw: str) -> DetailedVerdict:
    cleaned = strip_for_parse(raw)
    blob = find_last_json_blob(
        cleaned, required_keys=("verdict", "rationale", "step_verdicts"),
    )
    if blob is None:
        raise ProofReviewParseError(
            "no_detailed_json",
            "no JSON with verdict + rationale + step_verdicts keys",
            raw=raw or "",
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ProofReviewParseError(
            "json_decode_error", str(exc), raw=raw or "",
        ) from exc

    verdict = data.get("verdict")
    if verdict not in VALID_DETAILED_VERDICTS:
        raise ProofReviewParseError(
            "invalid_detailed_verdict",
            f"{verdict!r} not in {sorted(VALID_DETAILED_VERDICTS)}",
            raw=raw or "",
        )
    rationale = _require_nonempty_str(data, "rationale", raw=raw)
    confidence = _coerce_confidence_pv(data.get("confidence", 0.0), raw=raw)

    steps_raw = data.get("step_verdicts", [])
    if not isinstance(steps_raw, list):
        raise ProofReviewParseError(
            "step_verdicts_not_list",
            f"got {type(steps_raw).__name__}",
            raw=raw or "",
        )
    steps = [_parse_step_verdict(s, raw=raw) for s in steps_raw]

    issues_raw = data.get("rigor_issues", [])
    if not isinstance(issues_raw, list):
        raise ProofReviewParseError(
            "rigor_issues_not_list",
            f"got {type(issues_raw).__name__}",
            raw=raw or "",
        )
    issues = [_parse_rigor_issue(r, raw=raw) for r in issues_raw]

    # Consistency: verdict ∈ {gap, critical} requires at least one failed step OR fatal issue.
    if verdict in ("gap", "critical"):
        has_failed_step = any(s.verdict == "fail" for s in steps)
        has_fatal_issue = any(i.severity == "fatal" for i in issues)
        if not has_failed_step and not has_fatal_issue:
            raise ProofReviewParseError(
                "detailed_verdict_requires_evidence",
                f"verdict={verdict} but no failed step or fatal rigor issue listed",
                raw=raw or "",
            )

    return DetailedVerdict(
        verdict=verdict,
        rationale=rationale,
        step_verdicts=steps,
        rigor_issues=issues,
        confidence=confidence,
        raw=raw,
    )


def _parse_step_verdict(item: object, *, raw: str) -> StepVerdict:
    if not isinstance(item, dict):
        raise ProofReviewParseError(
            "step_verdict_not_object",
            f"got {type(item).__name__}",
            raw=raw or "",
        )
    step_id = item.get("step_id") or item.get("step") or ""
    if not isinstance(step_id, str) or not step_id.strip():
        raise ProofReviewParseError(
            "step_verdict_missing_step_id",
            "step_id must be a non-empty string",
            raw=raw or "",
        )
    claim = _coerce_optional_string_pv(item.get("claim", ""), "step_claim", raw=raw)
    verdict = item.get("verdict")
    if verdict not in VALID_STEP_VERDICTS:
        raise ProofReviewParseError(
            "invalid_step_verdict",
            f"step {step_id!r}: {verdict!r} not in {sorted(VALID_STEP_VERDICTS)}",
            raw=raw or "",
        )
    notes = _coerce_optional_string_pv(item.get("notes", ""), "step_notes", raw=raw)
    return StepVerdict(step_id=step_id, claim=claim, verdict=verdict, notes=notes)


def _parse_rigor_issue(item: object, *, raw: str) -> RigorIssue:
    if not isinstance(item, dict):
        raise ProofReviewParseError(
            "rigor_issue_not_object",
            f"got {type(item).__name__}",
            raw=raw or "",
        )
    severity = item.get("severity")
    if severity not in VALID_RIGOR_SEVERITIES:
        raise ProofReviewParseError(
            "invalid_rigor_severity",
            f"{severity!r} not in {sorted(VALID_RIGOR_SEVERITIES)}",
            raw=raw or "",
        )
    claim = _coerce_optional_string_pv(item.get("claim", ""), "rigor_claim", raw=raw)
    notes = _coerce_optional_string_pv(item.get("notes", ""), "rigor_notes", raw=raw)
    return RigorIssue(severity=severity, claim=claim, notes=notes)


# ---------------------------------------------------------------------------
# Small wrappers — route shared helpers through ProofReviewParseError
# ---------------------------------------------------------------------------
def _require_nonempty_str(data: dict[str, Any], field_name: str, *, raw: str) -> str:
    val = data.get(field_name)
    if not isinstance(val, str) or not val.strip():
        raise ProofReviewParseError(
            f"missing_{field_name}",
            f"{field_name} must be a non-empty string",
            raw=raw or "",
        )
    return val


def _coerce_confidence_pv(value: object, *, raw: str) -> float:
    try:
        return coerce_confidence(value, error_cls=ProofReviewParseError)
    except ProofReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _coerce_optional_string_pv(value: object, field_name: str, *, raw: str) -> str:
    try:
        return coerce_optional_string(
            value, field_name, error_cls=ProofReviewParseError,
        )
    except ProofReviewParseError as exc:
        exc.raw = raw or ""
        raise


def _coerce_str_list_pv(value: object, field_name: str, *, raw: str) -> list[str]:
    try:
        return coerce_str_list(value, field_name, error_cls=ProofReviewParseError)
    except ProofReviewParseError as exc:
        exc.raw = raw or ""
        raise


__all__ = [
    "DetailedVerdict",
    "JudgeVerdict",
    "ProofReview",
    "ProofReviewParseError",
    "RigorIssue",
    "StepVerdict",
    "StructuralCheck",
    "StructuralVerdict",
    "VALID_DETAILED_VERDICTS",
    "VALID_JUDGE_DIFFICULTIES",
    "VALID_JUDGE_VERDICTS",
    "VALID_RIGOR_SEVERITIES",
    "VALID_STEP_VERDICTS",
    "VALID_STRUCTURAL_CHECK_NAMES",
    "VALID_STRUCTURAL_VERDICTS",
    "parse_detailed",
    "parse_judge",
    "parse_structural",
]
