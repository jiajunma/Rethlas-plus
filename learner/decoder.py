"""Learner output decoder for Phase 3 source-to-KB batches.

The learner proposes source-backed candidate nodes and requests verifier work.
It does not directly mutate ``knowledge_base/nodes``; the decoded batch becomes
a compact ``learner.batch_proposed`` event for librarian admission.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from common.kb.types import KIND_PREFIX, LABEL_SLUG_RE, NodeKind, PLACEHOLDER_LABELS


LEARNER_OUTPUT_SCHEMA = "learner_batch_v1"

REASON_NO_BATCH_JSON = "no_learner_batch_json"
REASON_SCHEMA = "schema"
REASON_MISSING_SPAN_HASH = "missing_source_span_hash"
REASON_SPAN_HASH_MISMATCH = "source_span_hash_mismatch"
REASON_VERIFICATION_REQUEST_REQUIRED = "verification_request_required"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VALID_ISSUE_TYPES = frozenset(
    {
        "notation_ambiguous",
        "implicit_dependency",
        "missing_proof_step",
        "external_reference_needed",
        "source_claim_suspect",
        "extraction_low_confidence",
        "ocr_low_confidence",
        "needs_visual_check",
        "needs_manual_transcription",
    }
)
_REQUIRED_VERIFICATION_BY_KIND = {
    NodeKind.DEFINITION.value: "verify_definition",
    NodeKind.EXTERNAL_THEOREM.value: "verify_external_theorem",
}
_PROOF_REQUIRING_KINDS = frozenset(
    {
        NodeKind.LEMMA.value,
        NodeKind.PROPOSITION.value,
        NodeKind.THEOREM.value,
    }
)
_VALID_PROOF_STATUS = frozenset(
    {
        "source_proof_extracted",
        "proof_sketch_extracted",
        "proof_incomplete",
        "statement_only",
    }
)


@dataclass(frozen=True, slots=True)
class LearnerBatch:
    source_id: str
    run_id: str
    context_hash: str
    source_spans: tuple[dict[str, Any], ...]
    notation_contexts: tuple[dict[str, Any], ...]
    candidate_nodes: tuple[dict[str, Any], ...]
    dependency_edges: tuple[dict[str, Any], ...]
    bridge_requests: tuple[dict[str, Any], ...]
    verification_requests: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]
    summary: str

    def event_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "learner_run": self.run_id,
            "context_hash": self.context_hash,
            "source_spans": list(self.source_spans),
            "notation_contexts": list(self.notation_contexts),
            "candidate_nodes": list(self.candidate_nodes),
            "dependency_edges": list(self.dependency_edges),
            "bridge_requests": list(self.bridge_requests),
            "verification_requests": list(self.verification_requests),
            "issues": list(self.issues),
            "summary": self.summary,
        }


class LearnerDecodeError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def parse_learner_batch(raw: str) -> LearnerBatch:
    cleaned = unicodedata.normalize("NFC", _ANSI_RE.sub("", raw))
    data = _find_last_object_with_schema(cleaned, LEARNER_OUTPUT_SCHEMA)
    if data is None:
        raise LearnerDecodeError(
            REASON_NO_BATCH_JSON,
            f"could not locate JSON object with output_schema={LEARNER_OUTPUT_SCHEMA!r}",
        )
    return _validate_batch(data)


def _validate_batch(data: dict[str, Any]) -> LearnerBatch:
    if data.get("output_schema") != LEARNER_OUTPUT_SCHEMA:
        raise LearnerDecodeError(REASON_SCHEMA, "output_schema must be learner_batch_v1")
    source_id = _require_str(data, "source_id")
    if not source_id.startswith("src:"):
        raise LearnerDecodeError(REASON_SCHEMA, "source_id must start with src:")
    run_id = _require_str(data, "run_id")
    if not run_id.startswith("learn_"):
        raise LearnerDecodeError(REASON_SCHEMA, "run_id must start with learn_")
    context_hash = _optional_str(data, "context_hash")

    source_spans = _require_list(data, "source_spans")
    notation_contexts = _optional_list(data, "notation_contexts")
    candidate_nodes = _require_list(data, "candidate_nodes")
    dependency_edges = _optional_list(data, "dependency_edges")
    bridge_requests = _first_list(data, "bridge_requests", "generated_bridge_requests")
    verification_requests = _require_list(data, "verification_requests")
    issues = _optional_list(data, "issues")
    summary = _optional_str(data, "summary")

    span_hashes = _span_hash_map(source_spans)
    for node in candidate_nodes:
        _validate_candidate_node(node, source_id=source_id, span_hashes=span_hashes)
    _validate_verification_requests(candidate_nodes, verification_requests)
    _validate_issues(issues, source_id=source_id, span_hashes=span_hashes)

    return LearnerBatch(
        source_id=source_id,
        run_id=run_id,
        context_hash=context_hash,
        source_spans=tuple(source_spans),
        notation_contexts=tuple(notation_contexts),
        candidate_nodes=tuple(candidate_nodes),
        dependency_edges=tuple(dependency_edges),
        bridge_requests=tuple(bridge_requests),
        verification_requests=tuple(verification_requests),
        issues=tuple(issues),
        summary=summary,
    )


def _validate_candidate_node(
    node: Any, *, source_id: str, span_hashes: dict[str, str]
) -> None:
    if not isinstance(node, dict):
        raise LearnerDecodeError(REASON_SCHEMA, "candidate_nodes[] must be objects")
    label = _require_str(node, "label")
    kind_raw = _require_str(node, "kind")
    try:
        kind = NodeKind(kind_raw)
    except ValueError as exc:
        raise LearnerDecodeError(REASON_SCHEMA, f"unknown node kind {kind_raw!r}") from exc
    _check_label(label, kind)
    _require_str(node, "statement")
    source_refs = _source_refs(node)
    if not source_refs:
        raise LearnerDecodeError(
            REASON_MISSING_SPAN_HASH,
            f"candidate node {label!r} has no source_refs/provenance.source_spans",
        )
    for ref in source_refs:
        _validate_source_ref(ref, source_id=source_id, span_hashes=span_hashes, owner=label)
    _normalize_proof_fields(node, kind=kind.value)


def _validate_verification_requests(
    candidate_nodes: list[Any], verification_requests: list[Any]
) -> None:
    by_label: dict[str, set[str]] = {}
    for req in verification_requests:
        if not isinstance(req, dict):
            raise LearnerDecodeError(REASON_SCHEMA, "verification_requests[] must be objects")
        target = req.get("target") or req.get("label") or req.get("node_label")
        kind = req.get("kind")
        if isinstance(target, str) and isinstance(kind, str):
            by_label.setdefault(target, set()).add(kind)

    for node in candidate_nodes:
        if not isinstance(node, dict):
            continue
        label = node.get("label")
        kind = node.get("kind")
        required = _REQUIRED_VERIFICATION_BY_KIND.get(kind)
        if required and (not isinstance(label, str) or required not in by_label.get(label, set())):
            raise LearnerDecodeError(
                REASON_VERIFICATION_REQUEST_REQUIRED,
                f"{kind} candidate {label!r} requires verification request kind={required}",
            )


def _validate_issues(
    issues: list[Any], *, source_id: str, span_hashes: dict[str, str]
) -> None:
    for issue in issues:
        if not isinstance(issue, dict):
            raise LearnerDecodeError(REASON_SCHEMA, "issues[] must be objects")
        issue_type = issue.get("issue_type") or issue.get("type")
        if isinstance(issue_type, str) and issue_type not in _VALID_ISSUE_TYPES:
            raise LearnerDecodeError(REASON_SCHEMA, f"unknown issue type {issue_type!r}")
        ref = issue.get("source_ref") or issue.get("source_span")
        if isinstance(ref, dict):
            _validate_source_ref(
                ref,
                source_id=source_id,
                span_hashes=span_hashes,
                owner=issue.get("issue_id", "issue"),
            )


def _source_refs(node: dict[str, Any]) -> list[dict[str, Any]]:
    refs = node.get("source_refs")
    if isinstance(refs, list):
        return [r for r in refs if isinstance(r, dict)]
    provenance = node.get("provenance")
    if isinstance(provenance, dict):
        refs = provenance.get("source_refs")
        if isinstance(refs, list):
            return [r for r in refs if isinstance(r, dict)]
        spans = provenance.get("source_spans")
        if isinstance(spans, list):
            out: list[dict[str, Any]] = []
            for span in spans:
                if isinstance(span, dict):
                    out.append(span)
                elif isinstance(span, str):
                    out.append({"span_id": span, "span_hash": provenance.get("span_hash", "")})
            return out
    return []


def _validate_source_ref(
    ref: dict[str, Any], *, source_id: str, span_hashes: dict[str, str], owner: Any
) -> None:
    ref_source_id = ref.get("source_id", source_id)
    if ref_source_id != source_id:
        raise LearnerDecodeError(
            REASON_SCHEMA,
            f"{owner!r} references source_id {ref_source_id!r}, expected {source_id!r}",
        )
    span_id = ref.get("span_id") or ref.get("id")
    span_hash = ref.get("span_hash") or ref.get("text_hash") or ref.get("hash")
    if not isinstance(span_id, str) or not isinstance(span_hash, str) or not span_hash:
        raise LearnerDecodeError(
            REASON_MISSING_SPAN_HASH,
            f"{owner!r} source reference must include span_id and span_hash",
        )
    expected = span_hashes.get(span_id)
    if expected is None:
        raise LearnerDecodeError(
            REASON_MISSING_SPAN_HASH,
            f"{owner!r} references unknown source span {span_id!r}",
        )
    if span_hash != expected:
        raise LearnerDecodeError(
            REASON_SPAN_HASH_MISMATCH,
            f"{owner!r} span {span_id!r} hash {span_hash!r} != dispatched {expected!r}",
        )


def _normalize_proof_fields(node: dict[str, Any], *, kind: str) -> None:
    proof = node.get("proof", "")
    if proof is None:
        proof = ""
    if not isinstance(proof, str):
        raise LearnerDecodeError(REASON_SCHEMA, "candidate proof must be a string")
    node["proof"] = proof

    if kind not in _PROOF_REQUIRING_KINDS:
        return

    proof_status = node.get("proof_status", "")
    if proof_status is None or proof_status == "":
        proof_status = "proof_sketch_extracted" if proof.strip() else "statement_only"
    if not isinstance(proof_status, str) or proof_status not in _VALID_PROOF_STATUS:
        raise LearnerDecodeError(
            REASON_SCHEMA,
            "proof_status must be one of "
            + ", ".join(sorted(_VALID_PROOF_STATUS)),
        )
    if not proof.strip() and proof_status != "statement_only":
        raise LearnerDecodeError(
            REASON_SCHEMA,
            "empty proof for lemma/proposition/theorem requires proof_status=statement_only",
        )
    node["proof_status"] = proof_status

    proof_steps = node.get("proof_steps", [])
    if proof_steps is None:
        proof_steps = []
    if not isinstance(proof_steps, list):
        raise LearnerDecodeError(REASON_SCHEMA, "proof_steps must be a list")
    for step in proof_steps:
        if not isinstance(step, dict):
            raise LearnerDecodeError(REASON_SCHEMA, "proof_steps[] must be objects")
        if not isinstance(step.get("step", ""), str):
            raise LearnerDecodeError(REASON_SCHEMA, "proof_steps[].step must be a string")
    node["proof_steps"] = proof_steps

    depends_on = node.get("depends_on", [])
    if depends_on is None:
        depends_on = []
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        raise LearnerDecodeError(REASON_SCHEMA, "depends_on must be a list of labels")
    node["depends_on"] = depends_on


def _span_hash_map(spans: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for span in spans:
        if not isinstance(span, dict):
            raise LearnerDecodeError(REASON_SCHEMA, "source_spans[] must be objects")
        span_id = span.get("span_id") or span.get("id")
        span_hash = span.get("span_hash") or span.get("text_hash") or span.get("hash")
        if not isinstance(span_id, str) or not isinstance(span_hash, str) or not span_hash:
            raise LearnerDecodeError(
                REASON_MISSING_SPAN_HASH,
                "every source_spans[] entry must include span_id and span_hash/text_hash",
            )
        out[span_id] = span_hash
    return out


def _check_label(label: str, kind: NodeKind) -> None:
    expected = KIND_PREFIX[kind]
    prefix, sep, slug = label.partition(":")
    if sep != ":" or prefix != expected:
        raise LearnerDecodeError(
            REASON_SCHEMA,
            f"label {label!r} prefix does not match kind {kind.value}",
        )
    if not slug or not LABEL_SLUG_RE.match(slug):
        raise LearnerDecodeError(REASON_SCHEMA, f"label {label!r} has invalid slug")
    if label in PLACEHOLDER_LABELS:
        raise LearnerDecodeError(REASON_SCHEMA, f"label {label!r} is reserved")


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise LearnerDecodeError(REASON_SCHEMA, f"{key} must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LearnerDecodeError(REASON_SCHEMA, f"{key} must be a string")
    return value


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise LearnerDecodeError(REASON_SCHEMA, f"{key} must be a list")
    return value


def _optional_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise LearnerDecodeError(REASON_SCHEMA, f"{key} must be a list")
    return value


def _first_list(data: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        if key in data:
            return _optional_list(data, key)
    return []


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
    "LEARNER_OUTPUT_SCHEMA",
    "LearnerBatch",
    "LearnerDecodeError",
    "parse_learner_batch",
]
