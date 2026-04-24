"""Admission-side (pre-publish) structural validator.

Librarian's projector is authoritative at apply time, but the user CLI
(and later the generator wrapper) need a pre-publish check to avoid
admitting events that we already know will fail. This module collects
those shape-level checks so they have a single home.

The checks split in two:

- :func:`validate_producer_registration` — the ``(actor, type)`` pair
  must match an entry in the packaged ``producers.toml`` (§3.5).
- :func:`validate_admission` — per-type shape checks that can be
  decided from the event body alone (§3.1.6 admission layer). Includes
  label-prefix consistency and, for ``user.node_revised``, the kind-
  immutability rule against a caller-supplied current-kind callback.

Projection-only checks (label uniqueness against KB, Merkle cascade,
verdict hash-match) remain in :mod:`librarian.projector`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from common.events.schema import SchemaError, validate_event_schema
from common.kb.types import KIND_PREFIX, NodeKind, PROOF_REQUIRING_KINDS
from common.producers import producers_toml_bytes


class AdmissionError(ValueError):
    """Raised when a structural admission check fails."""


@dataclass(frozen=True, slots=True)
class ProducerSpec:
    kind: str
    actor_pattern: re.Pattern[str]
    allowed_event_types: frozenset[str]


@lru_cache(maxsize=1)
def _load_producers() -> tuple[ProducerSpec, ...]:
    raw = tomllib.loads(producers_toml_bytes().decode("utf-8"))
    entries = raw.get("producer", [])
    specs = []
    for entry in entries:
        specs.append(
            ProducerSpec(
                kind=entry["kind"],
                actor_pattern=re.compile(entry["actor_pattern"]),
                allowed_event_types=frozenset(entry["allowed_event_types"]),
            )
        )
    return tuple(specs)


def validate_producer_registration(actor: str, etype: str) -> None:
    """Raise :class:`AdmissionError` if ``(actor, etype)`` is not a registered pair."""
    for spec in _load_producers():
        if spec.actor_pattern.match(actor) and etype in spec.allowed_event_types:
            return
    raise AdmissionError(
        f"(actor={actor!r}, type={etype!r}) does not match any producer in "
        f"producers.toml"
    )


def validate_admission(
    body: Mapping[str, Any],
    *,
    current_kind_of: Callable[[str], str | None] | None = None,
) -> None:
    """Full pre-publish check. ``current_kind_of(label)`` returns the node's
    existing ``kind`` as a string (so the validator can enforce the
    kind-immutability rule on ``user.node_revised`` without opening the KB
    itself)."""
    try:
        validate_event_schema(body)  # type: ignore[arg-type]
    except SchemaError as exc:
        raise AdmissionError(str(exc)) from exc

    validate_producer_registration(body["actor"], body["type"])

    etype = body["type"]
    payload = body["payload"]
    target = body.get("target")

    if etype in {"user.node_added", "user.node_revised"}:
        kind_raw = payload.get("kind")
        if not isinstance(kind_raw, str):
            raise AdmissionError("payload.kind must be a string")
        try:
            kind = NodeKind(kind_raw)
        except ValueError as exc:
            raise AdmissionError(f"unknown kind {kind_raw!r}") from exc
        if not isinstance(target, str):
            raise AdmissionError(f"{etype} requires a target label")
        _check_label_prefix(target, kind)
        if kind not in PROOF_REQUIRING_KINDS and payload.get("proof"):
            raise AdmissionError(
                f"kind {kind.value} must not carry a proof"
            )
        if etype == "user.node_revised" and current_kind_of is not None:
            existing_kind = current_kind_of(target)
            if existing_kind is not None and existing_kind != kind.value:
                raise AdmissionError(
                    f"kind_mutation: {existing_kind} -> {kind.value}"
                )

    if etype == "user.hint_attached":
        if not isinstance(target, str):
            raise AdmissionError("user.hint_attached requires target label")
        hint = payload.get("hint")
        if not isinstance(hint, str) or not hint.strip():
            raise AdmissionError("hint must be a non-empty string")


def _check_label_prefix(label: str, kind: NodeKind) -> None:
    expected = KIND_PREFIX[kind]
    if ":" not in label:
        raise AdmissionError(f"label {label!r} missing prefix:slug form")
    prefix, _, slug = label.partition(":")
    if prefix != expected:
        raise AdmissionError(
            f"label {label!r} prefix does not match kind {kind.value}"
        )
    if not slug or not re.match(r"^[a-zA-Z0-9_]+$", slug):
        raise AdmissionError(f"label {label!r} has invalid slug")
