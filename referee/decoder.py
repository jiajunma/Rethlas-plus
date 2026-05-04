"""Referee report decoder for Phase 3 review workflows."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


REFEREE_OUTPUT_SCHEMA = "referee_report_v1"

REASON_NO_REPORT_JSON = "no_referee_report_json"
REASON_SCHEMA = "schema"
REASON_CITATION_EVIDENCE_REQUIRED = "citation_evidence_required"
REASON_REQUESTED_DETAIL_BLOCKS = "requested_detail_blocks_acceptance"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_VALID_VERDICTS = frozenset(
    {
        "accepted",
        "accepted_with_minor_gaps",
        "needs_revision",
        "major_gap",
        "wrong",
        "needs_external_reference_access",
    }
)
_ACCEPTING_VERDICTS = frozenset({"accepted", "accepted_with_minor_gaps"})
_VALID_ISSUE_SEVERITIES = frozenset(
    {
        "blocker",
        "major",
        "minor",
        "citation",
        "extraction",
        "expository",
        "editorial",
        "reconstructed_jump",
    }
)
_BLOCKING_SEVERITIES = frozenset({"blocker", "major", "citation", "extraction"})
_CHECKED_CITATION_STATUSES = frozenset(
    {
        "kb_resolved",
        "downloaded_public_pdf",
        "downloaded_public_tex",
        "user_uploaded_reference",
        "user_approved_external_premise",
        "resolved_exact",
        "resolved_partial",
        "statement_mismatch",
        "not_applicable",
    }
)


@dataclass(frozen=True, slots=True)
class RefereeReport:
    review_id: str
    target: str
    workspace_path: str
    target_hashes: dict[str, Any]
    verdict: str
    checked_claims: tuple[dict[str, Any], ...]
    reconstructed_jumps: tuple[dict[str, Any], ...]
    generated_repairs: tuple[dict[str, Any], ...]
    verified_repairs: tuple[dict[str, Any], ...]
    unresolved_gaps: tuple[dict[str, Any], ...]
    requested_details: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]
    counterexample_attempts: tuple[dict[str, Any], ...]
    external_reference_checks: tuple[dict[str, Any], ...]
    extraction_quality_checks: tuple[dict[str, Any], ...]
    recommended_kb_updates: tuple[dict[str, Any], ...]
    summary: str

    @property
    def blocks_acceptance(self) -> bool:
        if self.requested_details:
            return True
        for issue in self.issues:
            severity = issue.get("severity", "")
            issue_type = issue.get("issue_type") or issue.get("type")
            if issue.get("blocks_verdict") or issue.get("blocks_acceptance"):
                return True
            if severity in _BLOCKING_SEVERITIES:
                return True
            if issue_type == "requested_detail":
                return True
        return False

    def event_payload(self, *, report_hash: str = "") -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "workspace_path": self.workspace_path,
            "target_hashes": dict(self.target_hashes),
            "verdict": self.verdict,
            "issue_summary": issue_summary(list(self.issues), list(self.requested_details)),
            "report_hash": report_hash,
            "report": self.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_schema": REFEREE_OUTPUT_SCHEMA,
            "review_id": self.review_id,
            "target": self.target,
            "workspace_path": self.workspace_path,
            "target_hashes": self.target_hashes,
            "verdict": self.verdict,
            "checked_claims": list(self.checked_claims),
            "reconstructed_jumps": list(self.reconstructed_jumps),
            "generated_repairs": list(self.generated_repairs),
            "verified_repairs": list(self.verified_repairs),
            "unresolved_gaps": list(self.unresolved_gaps),
            "requested_details": list(self.requested_details),
            "issues": list(self.issues),
            "counterexample_attempts": list(self.counterexample_attempts),
            "external_reference_checks": list(self.external_reference_checks),
            "extraction_quality_checks": list(self.extraction_quality_checks),
            "recommended_kb_updates": list(self.recommended_kb_updates),
            "summary": self.summary,
        }


class RefereeDecodeError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def parse_referee_report(raw: str) -> RefereeReport:
    cleaned = unicodedata.normalize("NFC", _ANSI_RE.sub("", raw))
    data = _find_last_object_with_schema(cleaned, REFEREE_OUTPUT_SCHEMA)
    if data is None:
        raise RefereeDecodeError(
            REASON_NO_REPORT_JSON,
            f"could not locate JSON object with output_schema={REFEREE_OUTPUT_SCHEMA!r}",
        )
    return _validate_report(data)


def _validate_report(data: dict[str, Any]) -> RefereeReport:
    review_id = _require_str(data, "review_id")
    if not review_id.startswith("review_"):
        raise RefereeDecodeError(REASON_SCHEMA, "review_id must start with review_")
    target = _require_str(data, "target")
    workspace_path = _require_str(data, "workspace_path")
    if workspace_path.startswith("/") or not workspace_path.startswith("reviews/"):
        raise RefereeDecodeError(
            REASON_SCHEMA,
            "workspace_path must be relative and start with reviews/",
        )
    target_hashes = data.get("target_hashes", {})
    if not isinstance(target_hashes, dict):
        raise RefereeDecodeError(REASON_SCHEMA, "target_hashes must be an object")
    verdict = _require_str(data, "verdict")
    if verdict not in _VALID_VERDICTS:
        raise RefereeDecodeError(REASON_SCHEMA, f"invalid verdict {verdict!r}")

    checked_claims = _optional_list(data, "checked_claims")
    reconstructed_jumps = _optional_list(data, "reconstructed_jumps")
    generated_repairs = _optional_list(data, "generated_repairs")
    verified_repairs = _optional_list(data, "verified_repairs")
    unresolved_gaps = _optional_list(data, "unresolved_gaps")
    requested_details = _optional_list(data, "requested_details")
    issues = _optional_list(data, "issues")
    counterexample_attempts = _optional_list(data, "counterexample_attempts")
    external_reference_checks = _optional_list(data, "external_reference_checks")
    extraction_quality_checks = _optional_list(data, "extraction_quality_checks")
    recommended_kb_updates = _optional_list(data, "recommended_kb_updates")
    summary = _optional_str(data, "summary")

    _validate_issues(issues)
    _validate_requested_details(requested_details)
    _validate_citation_checks(external_reference_checks)

    report = RefereeReport(
        review_id=review_id,
        target=target,
        workspace_path=workspace_path,
        target_hashes=target_hashes,
        verdict=verdict,
        checked_claims=tuple(checked_claims),
        reconstructed_jumps=tuple(reconstructed_jumps),
        generated_repairs=tuple(generated_repairs),
        verified_repairs=tuple(verified_repairs),
        unresolved_gaps=tuple(unresolved_gaps),
        requested_details=tuple(requested_details),
        issues=tuple(issues),
        counterexample_attempts=tuple(counterexample_attempts),
        external_reference_checks=tuple(external_reference_checks),
        extraction_quality_checks=tuple(extraction_quality_checks),
        recommended_kb_updates=tuple(recommended_kb_updates),
        summary=summary,
    )
    if verdict in _ACCEPTING_VERDICTS and report.blocks_acceptance:
        raise RefereeDecodeError(
            REASON_REQUESTED_DETAIL_BLOCKS,
            f"verdict {verdict!r} is incompatible with blocking/requested-detail issues",
        )
    return report


def issue_summary(
    issues: list[dict[str, Any]], requested_details: list[dict[str, Any]]
) -> dict[str, Any]:
    by_severity: dict[str, int] = {}
    for issue in issues:
        sev = str(issue.get("severity", "") or "unknown")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return {
        "issue_count": len(issues),
        "requested_detail_count": len(requested_details),
        "by_severity": by_severity,
        "blocks_acceptance": bool(requested_details)
        or any(
            issue.get("blocks_verdict")
            or issue.get("blocks_acceptance")
            or issue.get("severity") in _BLOCKING_SEVERITIES
            or (issue.get("issue_type") or issue.get("type")) == "requested_detail"
            for issue in issues
        ),
    }


def _validate_issues(issues: list[Any]) -> None:
    for issue in issues:
        if not isinstance(issue, dict):
            raise RefereeDecodeError(REASON_SCHEMA, "issues[] must be objects")
        severity = issue.get("severity")
        if isinstance(severity, str) and severity not in _VALID_ISSUE_SEVERITIES:
            raise RefereeDecodeError(REASON_SCHEMA, f"invalid issue severity {severity!r}")
        issue_type = issue.get("issue_type") or issue.get("type")
        if issue_type == "requested_detail" and not (
            issue.get("blocks_verdict") or issue.get("blocks_acceptance")
        ):
            raise RefereeDecodeError(
                REASON_REQUESTED_DETAIL_BLOCKS,
                "requested_detail issues must block the verdict",
            )


def _validate_requested_details(requested_details: list[Any]) -> None:
    for item in requested_details:
        if not isinstance(item, dict):
            raise RefereeDecodeError(REASON_SCHEMA, "requested_details[] must be objects")
        if not item.get("requested_detail"):
            raise RefereeDecodeError(
                REASON_SCHEMA,
                "requested_details[] entries must include requested_detail",
            )
        if item.get("blocks_verdict") is False and item.get("blocks_acceptance") is False:
            raise RefereeDecodeError(
                REASON_REQUESTED_DETAIL_BLOCKS,
                "requested detail must block acceptance",
            )


def _validate_citation_checks(checks: list[Any]) -> None:
    for check in checks:
        if not isinstance(check, dict):
            raise RefereeDecodeError(
                REASON_SCHEMA, "external_reference_checks[] must be objects"
            )
        status = check.get("applicability") or check.get("status")
        if status in _CHECKED_CITATION_STATUSES:
            evidence_hash = check.get("evidence_hash")
            statement = check.get("quoted_or_paraphrased_statement") or check.get(
                "retrieved_statement_summary"
            )
            if not isinstance(evidence_hash, str) or not evidence_hash:
                raise RefereeDecodeError(
                    REASON_CITATION_EVIDENCE_REQUIRED,
                    "checked citation requires evidence_hash",
                )
            if status not in {"missing_access", "awaiting_user_reference"} and not isinstance(
                statement, str
            ):
                raise RefereeDecodeError(
                    REASON_CITATION_EVIDENCE_REQUIRED,
                    "checked citation requires quoted/paraphrased statement summary",
                )


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RefereeDecodeError(REASON_SCHEMA, f"{key} must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RefereeDecodeError(REASON_SCHEMA, f"{key} must be a string")
    return value


def _optional_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise RefereeDecodeError(REASON_SCHEMA, f"{key} must be a list")
    for item in value:
        if not isinstance(item, dict):
            raise RefereeDecodeError(REASON_SCHEMA, f"{key}[] must contain objects")
    return value


def _find_last_object_with_schema(text: str, schema: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for blob in _json_object_blobs(text):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("output_schema") == schema:
            found = data
    return found


def _json_object_blobs(text: str) -> list[str]:
    blobs: list[str] = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        end = _matching_brace(text, i)
        if end is not None:
            blobs.append(text[i : end + 1])
    return blobs


def _matching_brace(text: str, start: int) -> int | None:
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
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


__all__ = [
    "REFEREE_OUTPUT_SCHEMA",
    "RefereeDecodeError",
    "RefereeReport",
    "issue_summary",
    "parse_referee_report",
]
