"""Read side of the markdown KB — the inverse of :func:`librarian.renderer.render_node`.

The §13 design stores the knowledge base as plain markdown node files on the
filesystem (no Kuzu). This module parses such a file back into a :class:`Node`,
covering only the *math* fields that markdown actually carries:

    label, kind, pass_count, statement_hash, verification_hash, depends_on
    (frontmatter) + statement / proof / remark / source_note (body).

Operational fields (``repair_count``, ``verification_report``, ``repair_hint``,
``introduced_by_actor``) are **not** stored in markdown — they live in the
events-derived in-memory projection — so the parsed :class:`Node` carries them
at their defaults. Callers that need operational fields overlay them from that
projection.

Pure functions: no Kuzu, no network. The only I/O is reading the paths passed
to :func:`read_node_file` / :func:`read_nodes_dir`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from common.kb.types import Node, NodeKind

# Inverse of ``librarian.renderer._SECTION_ORDER``. Header text -> Node field.
_HEADER_KEY: dict[str, str] = {
    "Source Note.": "source_note",
    "Remark.": "remark",
    "Statement.": "statement",
    "Proof.": "proof",
}

# Matches any of the four fixed body section headers, e.g. ``**Statement.**``.
_SECTION_RE = re.compile(r"\*\*(Source Note\.|Remark\.|Statement\.|Proof\.)\*\*")


class MarkdownParseError(ValueError):
    """Raised when a node markdown file is structurally invalid."""


def parse_node_markdown(data: str | bytes) -> Node:
    """Parse the canonical bytes of a ``nodes/{prefix}_{slug}.md`` into a Node.

    Inverse of :func:`librarian.renderer.render_node` over the math fields.
    Operational fields are returned at their :class:`Node` defaults.
    """
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    # renderer writes ``---\n{frontmatter}---\n\n{body}`` (or ``---\n{fm}---\n``).
    parts = text.split("---\n", 2)
    if len(parts) < 3 or parts[0].strip() != "":
        raise MarkdownParseError("missing leading `---` frontmatter fence")
    frontmatter_raw, body = parts[1], parts[2]

    try:
        meta = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise MarkdownParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise MarkdownParseError("frontmatter is not a mapping")

    for required in ("label", "kind"):
        if required not in meta:
            raise MarkdownParseError(f"frontmatter missing required key {required!r}")

    try:
        kind = NodeKind(meta["kind"])
    except ValueError as exc:
        raise MarkdownParseError(f"unknown kind {meta['kind']!r}") from exc

    sections = _split_sections(body)
    depends_on = tuple(meta.get("depends_on") or ())

    return Node(
        label=str(meta["label"]),
        kind=kind,
        statement=sections.get("statement", ""),
        proof=sections.get("proof", ""),
        remark=sections.get("remark", ""),
        source_note=sections.get("source_note", ""),
        pass_count=int(meta.get("pass_count", -1)),
        repair_count=0,  # operational — not carried in markdown
        statement_hash=str(meta.get("statement_hash", "")),
        verification_hash=str(meta.get("verification_hash", "")),
        depends_on=depends_on,
    )


def _split_sections(body: str) -> dict[str, str]:
    """Split a rendered body into ``{field: text}`` by its ``**Header.**`` markers."""
    matches = list(_SECTION_RE.finditer(body))
    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        field = _HEADER_KEY[match.group(1)]
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[field] = body[start:end].strip()
    return out


def read_node_file(path: str | Path) -> Node:
    """Read and parse a single node markdown file."""
    return parse_node_markdown(Path(path).read_bytes())


def read_nodes_dir(nodes_dir: str | Path) -> dict[str, Node]:
    """Parse every ``*.md`` under ``nodes_dir`` into a ``{label: Node}`` map."""
    base = Path(nodes_dir)
    out: dict[str, Node] = {}
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.md")):
        node = read_node_file(path)
        out[node.label] = node
    return out


def query_verified(nodes_dir: str | Path) -> list[Node]:
    """Verified nodes (``pass_count >= 1``) — the admissible proof basis."""
    nodes = read_nodes_dir(nodes_dir)
    return sorted(
        (n for n in nodes.values() if n.pass_count >= 1),
        key=lambda n: n.label,
    )


def list_staged(nodes_dir: str | Path) -> list[Node]:
    """Staged nodes (``pass_count <= 0``) — Goal / subgoal candidates."""
    nodes = read_nodes_dir(nodes_dir)
    return sorted(
        (n for n in nodes.values() if n.pass_count <= 0),
        key=lambda n: n.label,
    )


def dependencies_of(nodes_dir: str | Path, label: str) -> list[str]:
    """Direct dependency labels of ``label`` (ASCII-sorted), or ``[]`` if absent."""
    node = read_nodes_dir(nodes_dir).get(label)
    return sorted(node.depends_on) if node is not None else []


__all__ = [
    "MarkdownParseError",
    "parse_node_markdown",
    "read_node_file",
    "read_nodes_dir",
    "query_verified",
    "list_staged",
    "dependencies_of",
]
