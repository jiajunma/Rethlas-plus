"""Render a :class:`Node` to its canonical ``nodes/{prefix}_{label}.md`` form.

ARCHITECTURE §4.2 "Rendering contract" — librarian's per-event re-render,
startup reconciliation (§6.5), linter category E ``--repair-nodes``, and
``rethlas rebuild``'s final render pass MUST all use this function so that
"drift" between on-disk bytes and Kuzu state is a real signal, not a
formatting disagreement between two render paths.

Rules implemented here (line-for-line copy of the §4.2 contract):

- Line endings: Unix ``\\n`` only.
- Encoding: UTF-8, NFC-normalised before write.
- Trailing newline: exactly one ``\\n`` at EOF.
- YAML frontmatter key order is fixed: ``label``, ``kind``, ``pass_count``,
  ``statement_hash``, ``verification_hash``, ``depends_on``.
- ``depends_on``: ASCII-sorted, deduplicated, YAML block-list form.
- Body sections in fixed order: ``Source Note.``, ``Remark.``,
  ``Statement.``, ``Proof.``. Empty sections are omitted entirely.
- No timestamps, no generation version, no host-specific data.

The function is a *pure* mapping ``Node -> bytes``. It performs no I/O —
callers (``write_node_file`` below) handle filesystem placement.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Final

import yaml

from common.kb.types import KIND_PREFIX, Node, NodeKind


# Body section header order (§4.2 "Body section order (fixed)").
_SECTION_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("source_note", "Source Note."),
    ("remark", "Remark."),
    ("statement", "Statement."),
    ("proof", "Proof."),
)


def render_node(node: Node) -> bytes:
    """Return the canonical bytes for ``nodes/{prefix}_{label}.md``.

    The output is deterministic — the same ``Node`` always produces the
    same bytes. Callers should pass an *already projected* :class:`Node`
    pulled from Kuzu; the renderer does not validate ``pass_count``
    semantics (the caller filters by ``pass_count >= 1``).
    """
    body_text = _build_text(node)
    nfc = unicodedata.normalize("NFC", body_text)
    return nfc.encode("utf-8")


def _build_text(node: Node) -> str:
    frontmatter = _render_frontmatter(node)
    body = _render_body(node)
    if body:
        text = f"---\n{frontmatter}---\n\n{body}"
    else:
        text = f"---\n{frontmatter}---\n"
    if not text.endswith("\n"):
        text += "\n"
    return text


def _render_frontmatter(node: Node) -> str:
    kind = node.kind.value if isinstance(node.kind, NodeKind) else node.kind
    deps_sorted = sorted(set(node.depends_on))
    # PyYAML respects insertion order when sort_keys=False; build an explicit
    # dict in the §4.2 fixed order.
    data = {
        "label": node.label,
        "kind": kind,
        "pass_count": int(node.pass_count),
        "statement_hash": node.statement_hash,
        "verification_hash": node.verification_hash,
        "depends_on": deps_sorted,
    }
    return yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,  # avoid PyYAML re-flowing long strings
    )


def _render_body(node: Node) -> str:
    sections: list[str] = []
    fields = {
        "source_note": (node.source_note or "").strip(),
        "remark": (node.remark or "").strip(),
        "statement": (node.statement or "").strip(),
        "proof": (node.proof or "").strip(),
    }
    for key, header in _SECTION_ORDER:
        text = fields[key]
        if not text:
            continue
        sections.append(f"**{header}**\n\n{text}\n")
    return "\n".join(sections)


def node_filename(node: Node | str, kind: NodeKind | str | None = None) -> str:
    """Return the canonical filename for the node.

    Accepts either a full :class:`Node` or a ``(label, kind)`` pair. The
    ``kind`` overload exists so reconciliation can compute target paths
    without rebuilding the full :class:`Node` object.
    """
    if isinstance(node, Node):
        label = node.label
        kind_enum = node.kind if isinstance(node.kind, NodeKind) else NodeKind(node.kind)
    else:
        label = node
        if kind is None:
            raise ValueError("kind required when label is passed alone")
        kind_enum = kind if isinstance(kind, NodeKind) else NodeKind(kind)
    prefix = KIND_PREFIX[kind_enum]
    if ":" not in label:
        raise ValueError(f"label {label!r} missing prefix:slug form")
    actual_prefix, _, slug = label.partition(":")
    if actual_prefix != prefix:
        raise ValueError(
            f"label {label!r} prefix mismatch (expected {prefix} for {kind_enum.value})"
        )
    return f"{prefix}_{slug}.md"


def write_node_file(nodes_dir: Path, node: Node) -> Path:
    """Render and atomically write ``node`` into ``nodes_dir``.

    Returns the path written. The write uses tmp + rename so partial
    files are never observed by readers; we do not fsync the parent
    directory because ``nodes/`` is a derived projection (recoverable
    from ``events/``), not part of the truth layer.
    """
    nodes_dir.mkdir(parents=True, exist_ok=True)
    path = nodes_dir / node_filename(node)
    payload = render_node(node)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
    return path


__all__ = [
    "node_filename",
    "render_node",
    "write_node_file",
]
