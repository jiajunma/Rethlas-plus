"""Single boundary to the mdblueprint knowledge base (issue #6).

Every other module in ``rethlas_kb`` reads / writes the KB through
:class:`KbAdapter` so that:

1. Frontmatter shape is validated once, against mdblueprint's own
   validator — no parallel schema definitions drift.
2. KB layout (the ``docs/knowledge/{nodes,staged,reviews,requests,
   sources}`` tree) is centralised here, not sprinkled across agents.
3. The mdblueprint API surface we depend on is enumerated explicitly,
   so an upstream breaking change shows up in one file instead of many.

The adapter is intentionally thin — it adds **no** business logic on
top of mdblueprint. Decisions like "what fields go in a review" or
"when to stage vs admit a node" live in the agents that own them.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- mdblueprint imports — enumerated here so the dependency footprint
#     is explicit and easy to audit.
from tools.knowledge.context_pack import build_context_pack
from tools.knowledge.export import (
    home_topic_for_node,
    leaf_topic_ids_for_node,
    topic_path,
)
from tools.knowledge.models import (
    ADMITTED_STATUSES,
    STAGED_STATUSES,
    Node,
)
from tools.knowledge.parser import parse_node, scan_directory
from tools.knowledge.validator import validate_node


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContextBundle:
    """A simplified view of mdblueprint's context_pack dict.

    Keeps the high-traffic fields (``target_id`` / ``topic`` / ``mode`` /
    ``nodes``) as named attributes for typed access; ``raw`` carries
    the full upstream dict for callers that need the rest
    (``allowed_inputs`` / ``forbidden_inputs`` / ``answer_contract`` /
    ``staged_evidence_ids``).
    """

    target_id: str | None
    topic: str | None
    mode: str
    nodes: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class KbAdapter:
    """Thin wrapper around the mdblueprint KB on disk.

    Parameters
    ----------
    kb_root:
        Project root that *contains* ``docs/knowledge/...`` — not the
        ``docs/knowledge`` dir itself. Mirrors how mdblueprint CLIs
        accept the project root.
    """

    def __init__(self, kb_root: Path | str) -> None:
        self.kb_root = Path(kb_root).resolve()
        self.knowledge_dir = self.kb_root / "docs" / "knowledge"
        self.nodes_dir = self.knowledge_dir / "nodes"
        self.staged_dir = self.knowledge_dir / "staged"
        self.reviews_dir = self.knowledge_dir / "reviews"
        self.requests_dir = self.knowledge_dir / "requests"
        self.sources_dir = self.knowledge_dir / "sources"

    # -- reads ------------------------------------------------------------
    def read_node(self, node_id: str) -> Node:
        """Find a node by id; search admitted then staged.

        Raises :class:`KeyError` if no node with that id exists.
        """
        for directory in (self.nodes_dir, self.staged_dir):
            if not directory.exists():
                continue
            for node in scan_directory(directory):
                if node.id == node_id:
                    return node
        raise KeyError(
            f"node {node_id!r} not found under {self.knowledge_dir}"
        )

    def list_admitted(self) -> list[Node]:
        """All nodes under ``docs/knowledge/nodes/`` with admitted status."""
        if not self.nodes_dir.exists():
            return []
        return [
            n for n in scan_directory(self.nodes_dir)
            if n.status in ADMITTED_STATUSES
        ]

    def list_staged(self) -> list[Node]:
        """All nodes under ``docs/knowledge/staged/`` with staged status."""
        if not self.staged_dir.exists():
            return []
        return [
            n for n in scan_directory(self.staged_dir)
            if n.status in STAGED_STATUSES
        ]

    def list_staged_by_topic(self, topic_id: str) -> list[Node]:
        """Staged nodes that claim membership in ``topic_id``.

        Membership uses mdblueprint's ``leaf_topic_ids_for_node`` (the
        explicit ``topics:`` list, falling back to the node's home topic).
        """
        return [
            n for n in self.list_staged()
            if topic_id in leaf_topic_ids_for_node(n)
        ]

    # -- writes -----------------------------------------------------------
    def write_review(
        self,
        *,
        node_id: str,
        agent_name: str,
        review: dict,
    ) -> Path:
        """Persist a review under ``docs/knowledge/reviews/``.

        ``review`` may contain a ``"body"`` key for the prose section;
        everything else lands in YAML frontmatter alongside the
        adapter-managed ``agent`` / ``target`` / ``created_at`` keys.
        """
        return self._write_payload(
            self.reviews_dir, node_id, agent_name, review, kind="review",
        )

    def write_request(
        self,
        *,
        node_id: str,
        request_kind: str,
        payload: dict,
    ) -> Path:
        """Persist a request under ``docs/knowledge/requests/``.

        ``request_kind`` describes *what* is being requested (e.g.
        ``"missing-dependency"``, ``"gap-fill"``). The file's frontmatter
        records it under ``kind`` for downstream consumers.
        """
        return self._write_payload(
            self.requests_dir, node_id, request_kind, payload,
            kind=request_kind,
        )

    def write_staged_node(
        self,
        *,
        frontmatter: dict,
        body: str = "",
        filename: str | None = None,
    ) -> Path:
        """Write a staged node, validating it via mdblueprint first.

        The compose-then-parse round-trip guarantees the on-disk file is
        valid mdblueprint frontmatter — agents can't write something
        ``parse_node`` would later reject.

        Raises
        ------
        ValueError
            If the frontmatter cannot be parsed or
            ``validate_node(is_staged_dir=True)`` returns any
            error-level diagnostic.
        """
        text = _compose_markdown(frontmatter, body)
        try:
            node = parse_node(text, file_path=None)
        except Exception as exc:  # mdblueprint raises ValueError; defensive
            raise ValueError(f"frontmatter parse failed: {exc}") from exc
        diags = validate_node(node, is_staged_dir=True)
        errors = [d for d in diags if d.level == "error"]
        if errors:
            raise ValueError(
                "frontmatter validation failed:\n  "
                + "\n  ".join(str(d) for d in errors)
            )

        target_dir = self.staged_dir / topic_path(home_topic_for_node(node))
        target_dir.mkdir(parents=True, exist_ok=True)
        if filename is None:
            local = node.id.split(".", 1)[-1] if "." in node.id else node.id
            filename = f"{local}.md"
        target_path = target_dir / filename
        target_path.write_text(text, encoding="utf-8")
        return target_path

    # -- context_pack -----------------------------------------------------
    def context_pack(
        self,
        *,
        target_id: str | None = None,
        topic: str | None = None,
        include_staged: bool = False,
    ) -> ContextBundle:
        """Wrap mdblueprint's ``build_context_pack`` in a typed bundle.

        Exactly one of ``target_id`` or ``topic`` is required (mirrors
        upstream's contract; upstream raises ``ValueError`` otherwise).
        """
        raw = build_context_pack(
            self.knowledge_dir,
            target_id=target_id,
            topic=topic,
            include_staged=include_staged,
        )
        return ContextBundle(
            target_id=raw.get("target_id"),
            topic=raw.get("topic"),
            mode=raw.get("mode", "admitted"),
            nodes=raw.get("nodes", []),
            raw=raw,
        )

    # -- internal ---------------------------------------------------------
    def _write_payload(
        self,
        directory: Path,
        node_id: str,
        agent_name: str,
        payload: dict,
        *,
        kind: str,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        slug = node_id.replace(".", "_")
        # Microsecond precision keeps timestamps unique across rapid writes.
        timestamp = _dt.datetime.now(_dt.UTC).isoformat()
        safe_ts = re.sub(r"[:+]", "_", timestamp)
        path = directory / f"{slug}__{agent_name}__{safe_ts}.md"

        body = ""
        meta = dict(payload)
        if "body" in meta:
            body = str(meta.pop("body") or "")

        frontmatter = {
            "agent": agent_name,
            "kind": kind,
            "target": {"node_id": node_id},
            "created_at": timestamp,
        }
        # Caller-supplied fields layer on top — but reserved keys win
        # (we control them so the file shape stays predictable).
        for k, v in meta.items():
            if k in frontmatter:
                continue
            frontmatter[k] = v

        path.write_text(_compose_markdown(frontmatter, body), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Helpers (module-level so they're independently testable)
# ---------------------------------------------------------------------------
def _compose_markdown(frontmatter: dict, body: str) -> str:
    """Render a YAML frontmatter block + markdown body to one string."""
    fm = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True,
    ).rstrip("\n")
    body = body.strip()
    if body:
        return f"---\n{fm}\n---\n\n{body}\n"
    return f"---\n{fm}\n---\n"


__all__ = [
    "ContextBundle",
    "KbAdapter",
    "Node",  # re-export for callers that need the type
]
