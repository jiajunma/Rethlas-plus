"""Deterministic node hashes (ARCHITECTURE §5.3).

Hash inputs use canonical JSON: UTF-8, sorted keys, compact separators,
Unicode NFC normalisation, and LF line endings in string fields. Two
independent implementations of these helpers must produce the same
bytes — that's the contract the Merkle cascade relies on.

Only ``statement_hash`` of deps propagates up the cascade; proof changes
update ``verification_hash`` but stop there. See §5.3 rationale.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_STATEMENT_SCHEMA = "rethlas-statement-v1"
_VERIFICATION_SCHEMA = "rethlas-verification-v1"


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON encoding for hash inputs.

    Rules:
    - UTF-8
    - sorted keys (``sort_keys=True``)
    - compact separators (no whitespace)
    - ``ensure_ascii=False`` so non-ASCII glyphs survive round-trip
    - Strings (leaves) are NFC-normalised and have CRLF / CR → LF.

    The normalisation pass is applied recursively before serialisation so
    that ``\\n`` vs ``\\r\\n`` line endings and equivalent Unicode forms
    produce identical bytes — a prerequisite for the "same state → same
    hash on every machine" guarantee.
    """
    normalised = _normalise(obj)
    return json.dumps(
        normalised,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _normalise(obj: Any) -> Any:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj).replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(obj, Mapping):
        return {str(k): _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(x) for x in obj]
    return obj


@dataclass(frozen=True, slots=True)
class DepRef:
    """Minimal view of a dep for Merkle input: ``(label, statement_hash)``."""

    label: str
    statement_hash: str


def statement_hash(
    *,
    label: str,
    kind: str,
    statement: str,
    depends_on: Iterable[DepRef],
) -> str:
    """Compute a node's ``statement_hash`` per §5.3."""
    deps_sorted = sorted(depends_on, key=lambda d: d.label)
    payload = {
        "schema": _STATEMENT_SCHEMA,
        "label": label,
        "kind": kind,
        "statement": statement,
        "depends_on": [
            {"label": d.label, "statement_hash": d.statement_hash}
            for d in deps_sorted
        ],
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def verification_hash(*, statement_hash_hex: str, proof: str | None) -> str:
    """Compute a node's ``verification_hash`` per §5.3.

    For axioms (empty proof) this equals :func:`statement_hash` prefixed
    with the verification schema — the equality is documented in §5.3.
    """
    payload = {
        "schema": _VERIFICATION_SCHEMA,
        "statement_hash": statement_hash_hex,
        "proof": proof or "",
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()
