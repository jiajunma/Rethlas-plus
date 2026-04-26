"""Apply a single truth event to the KB (ARCHITECTURE §3.1.6, §5.2, §5.4).

Each ``apply(event, event_bytes)`` call is wrapped in a Kuzu transaction:
on success we record ``AppliedEvent(status=applied)``; on a rule failure
we still record ``AppliedEvent(status=apply_failed, reason=…, detail=…)``
so the event is permanently decided (§3.1.6 "apply_failed is terminal")
and producers / dashboard can see the outcome.

Rules implemented here (§5.4):
- ``user.node_added`` — create a new Node; reject on ``label_conflict``.
- ``user.node_revised`` — replace the node's authored state; reject on
  ``kind_mutation`` / missing label / cycle.
- ``user.hint_attached`` — append to ``repair_hint``; reject on
  ``hint_target_missing`` / ``hint_target_unreachable``.
- ``generator.batch_committed`` — atomic multi-node write; reject on
  ``label_conflict`` / ``cycle`` / ``kind_mutation`` / hash mismatch.
- ``verifier.run_completed`` — update ``pass_count`` / ``repair_count``;
  reject on ``hash_mismatch`` when the verdict's hash is stale.

Merkle cascade: when a node's ``statement_hash`` changes, every dependent
node has its ``statement_hash`` + ``verification_hash`` recomputed and
its ``repair_count`` reset to 0; ``verification_hash`` changes clear the
dependent's ``repair_hint`` and ``verification_report``. The projector
walks dependents in BFS order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.events.io import event_sha256
from common.events.schema import SchemaError, validate_event_schema
from common.kb.hashing import DepRef, statement_hash, verification_hash
from common.kb.kuzu_backend import KuzuBackend, RawNodeRow, _bfs_path
from common.kb.types import (
    AppliedEvent,
    ApplyOutcome,
    KIND_PREFIX,
    LABEL_SLUG_RE,
    Node,
    NodeKind,
    PLACEHOLDER_LABELS,
    PROOF_REQUIRING_KINDS,
)
from librarian.validator import (
    AdmissionError,
    validate_producer_registration,
)


# ---------------------------------------------------------------------------
# Rejection-reason enum — keep in sync with ARCHITECTURE §5.2 + PHASE1 M2.
# ---------------------------------------------------------------------------
REASON_LABEL_CONFLICT = "label_conflict"
REASON_CYCLE = "cycle"
REASON_REF_MISSING = "ref_missing"
REASON_HINT_TARGET_MISSING = "hint_target_missing"
REASON_HINT_TARGET_UNREACHABLE = "hint_target_unreachable"
REASON_HASH_MISMATCH = "hash_mismatch"
REASON_KIND_MUTATION = "kind_mutation"
REASON_SELF_REFERENCE = "self_reference"
# §6.5 "workspace corruption": an event got past admission with a
# producer-registration or structural shape that should never have been
# allowed. Caller (daemon) treats this as a fatal halt-projection signal.
REASON_WORKSPACE_CORRUPTION = "workspace_corruption"


class ProjectionRejection(Exception):
    """Raised inside the projector to roll back and record an ``apply_failed``."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of :meth:`Projector.apply`. Mirrors the AppliedEvent row."""

    event_id: str
    status: ApplyOutcome
    reason: str | None
    detail: str | None


class Projector:
    """Apply events to :class:`KuzuBackend`, one at a time, transactionally."""

    def __init__(self, backend: KuzuBackend) -> None:
        self._kb = backend

    # ---- public entry --------------------------------------------
    def apply(self, event_body: dict[str, Any], event_bytes: bytes) -> ApplyResult:
        """Apply ``event_body`` to KB.

        Idempotent: if an ``AppliedEvent`` row already exists for this
        ``event_id``, returns the stored outcome without touching KB.

        Parameters
        ----------
        event_body:
            Parsed JSON event (already validated by the caller for basic
            structure; this method validates §3.4 envelope again defensively).
        event_bytes:
            The raw on-disk bytes of the event file. Used for
            ``AppliedEvent.event_sha256`` and for tampering detection
            on re-apply.
        """
        validate_event_schema(event_body)
        event_id = event_body["event_id"]

        # §6.5 step 1 "structural check": producer-registration must hold
        # at apply time too. A canonical event whose ``(actor, type)`` is
        # not in producers.toml means admission was bypassed (manual file
        # drop, git revert, etc.) — projection halts as workspace
        # corruption per §3.1.6.
        try:
            validate_producer_registration(
                event_body.get("actor", ""), event_body.get("type", "")
            )
        except AdmissionError as exc:
            raise ProjectionRejection(
                REASON_WORKSPACE_CORRUPTION,
                f"unregistered producer at apply time: {exc}",
            ) from exc

        existing = self._kb.applied_event(event_id)
        sha = event_sha256(event_bytes)
        if existing is not None:
            # Idempotent re-apply (§5.5.0 #3). Detect tampering per §3.1.6
            # workspace-corruption contract.
            if existing.event_sha256 != sha:
                raise ProjectionRejection(
                    REASON_WORKSPACE_CORRUPTION,
                    (
                        f"event_id {event_id!r} already applied with "
                        f"sha={existing.event_sha256[:12]}; re-apply sees "
                        f"sha={sha[:12]} — event file tampered with"
                    ),
                )
            return ApplyResult(
                event_id=event_id,
                status=existing.status,
                reason=existing.reason,
                detail=existing.detail,
            )

        self._kb.begin()
        try:
            target_label = self._dispatch(event_body)
            self._kb.record_applied_event(
                event_id=event_id,
                status=ApplyOutcome.APPLIED,
                event_sha256=sha,
                target_label=target_label,
            )
            self._kb.commit()
            return ApplyResult(
                event_id=event_id, status=ApplyOutcome.APPLIED, reason=None, detail=None
            )
        except ProjectionRejection as rej:
            self._kb.rollback()
            # Record the apply_failed in its own one-shot transaction so the
            # decision is durable even though KB state stays unchanged.
            self._kb.begin()
            self._kb.record_applied_event(
                event_id=event_id,
                status=ApplyOutcome.APPLY_FAILED,
                reason=rej.reason,
                detail=rej.detail,
                event_sha256=sha,
                target_label=event_body.get("target"),
            )
            self._kb.commit()
            return ApplyResult(
                event_id=event_id,
                status=ApplyOutcome.APPLY_FAILED,
                reason=rej.reason,
                detail=rej.detail,
            )
        except Exception:
            self._kb.rollback()
            raise

    # ---- per-type handlers --------------------------------------
    def _dispatch(self, event: dict[str, Any]) -> str | None:
        etype: str = event["type"]
        payload = event["payload"]
        target = event.get("target")
        if etype == "user.node_added":
            return self._apply_node_added(target, payload)
        if etype == "user.node_revised":
            return self._apply_node_revised(target, payload)
        if etype == "user.hint_attached":
            return self._apply_hint_attached(target, payload, ts=event.get("ts", ""))
        if etype == "generator.batch_committed":
            return self._apply_generator_batch(payload)
        if etype == "verifier.run_completed":
            return self._apply_verifier_run(event, payload)
        raise ProjectionRejection(
            "unknown_event_type", f"type {etype!r} has no projector handler"
        )

    # -- user.node_added --
    def _apply_node_added(self, target: str | None, payload: dict[str, Any]) -> str:
        if not isinstance(target, str):
            raise ProjectionRejection("schema", "user.node_added requires a target label")
        self._require_payload_fields(
            payload, {"kind", "statement", "remark", "source_note"}, etype="user.node_added"
        )
        kind = self._parse_kind(payload["kind"])
        _check_label_prefix(target, kind)
        if kind not in PROOF_REQUIRING_KINDS and payload.get("proof"):
            # Phase I: axioms (def / ext) must have empty proof.
            raise ProjectionRejection(
                "schema",
                f"kind {kind.value} must not carry a proof",
            )
        proof = payload.get("proof", "")
        statement = payload["statement"]
        if not statement:
            raise ProjectionRejection("schema", "statement must be non-empty")
        if kind is NodeKind.EXTERNAL_THEOREM and not (payload.get("source_note") or "").strip():
            raise ProjectionRejection(
                "schema", "external_theorem requires non-empty source_note"
            )

        existing = self._kb.node_by_label(target)
        if existing is not None:
            raise ProjectionRejection(
                REASON_LABEL_CONFLICT,
                f"label {target!r} already exists",
            )

        deps = _extract_refs(statement + "\n" + proof)
        if target in deps:
            raise ProjectionRejection(
                REASON_SELF_REFERENCE,
                f"node {target!r} references itself",
            )
        for dep in deps:
            if self._kb.node_by_label(dep) is None:
                raise ProjectionRejection(
                    REASON_REF_MISSING, f"\\ref{{{dep}}} not found"
                )

        node = self._assemble_node(
            label=target,
            kind=kind,
            statement=statement,
            proof=proof,
            depends_on=deps,
            remark=payload.get("remark", ""),
            source_note=payload.get("source_note", ""),
        )
        cycle = self._kb.would_introduce_cycle(target, deps)
        if cycle is not None:
            raise ProjectionRejection(REASON_CYCLE, _cycle_detail(cycle))
        self._kb.create_node(node)
        return target

    # -- user.node_revised --
    def _apply_node_revised(self, target: str | None, payload: dict[str, Any]) -> str:
        if not isinstance(target, str):
            raise ProjectionRejection("schema", "user.node_revised requires a target label")
        existing = self._kb.node_by_label(target)
        if existing is None:
            raise ProjectionRejection(
                REASON_REF_MISSING, f"target {target!r} not found"
            )

        self._require_payload_fields(
            payload, {"kind", "statement", "remark", "source_note"}, etype="user.node_revised"
        )
        new_kind = self._parse_kind(payload["kind"])
        old_kind = NodeKind(existing.kind)
        if new_kind is not old_kind:
            raise ProjectionRejection(
                REASON_KIND_MUTATION,
                f"kind {old_kind.value} -> {new_kind.value} not allowed",
            )
        _check_label_prefix(target, new_kind)
        statement = payload["statement"]
        if not statement:
            raise ProjectionRejection("schema", "statement must be non-empty")
        proof = payload.get("proof", "")
        if new_kind not in PROOF_REQUIRING_KINDS and proof:
            raise ProjectionRejection(
                "schema", f"kind {new_kind.value} must not carry a proof"
            )
        if new_kind is NodeKind.EXTERNAL_THEOREM and not (payload.get("source_note") or "").strip():
            raise ProjectionRejection(
                "schema", "external_theorem requires non-empty source_note"
            )

        deps = _extract_refs(statement + "\n" + proof)
        if target in deps:
            raise ProjectionRejection(REASON_SELF_REFERENCE, target)
        for dep in deps:
            if self._kb.node_by_label(dep) is None:
                raise ProjectionRejection(
                    REASON_REF_MISSING, f"\\ref{{{dep}}} not found"
                )
        cycle = self._kb.would_introduce_cycle(target, deps)
        if cycle is not None:
            raise ProjectionRejection(REASON_CYCLE, _cycle_detail(cycle))

        # Compute new hashes.
        new_node = self._assemble_node(
            label=target,
            kind=new_kind,
            statement=statement,
            proof=proof,
            depends_on=deps,
            remark=payload.get("remark", ""),
            source_note=payload.get("source_note", ""),
        )

        # §5.4 revision rules: pass_count reset via initial_count; if
        # statement_hash changed, repair_count -> 0; if verification_hash
        # changed, clear repair_hint / verification_report.
        if new_node.statement_hash != existing.statement_hash:
            clear_repair_count = True
        else:
            clear_repair_count = False
        if new_node.verification_hash != existing.verification_hash:
            clear_hint_and_report = True
        else:
            clear_hint_and_report = False

        self._kb.update_node(
            Node(
                label=new_node.label,
                kind=new_node.kind,
                statement=new_node.statement,
                proof=new_node.proof,
                remark=new_node.remark,
                source_note=new_node.source_note,
                pass_count=new_node.initial_count(),
                repair_count=0 if clear_repair_count else existing.repair_count,
                statement_hash=new_node.statement_hash,
                verification_hash=new_node.verification_hash,
                verification_report="" if clear_hint_and_report else existing.verification_report,
                repair_hint="" if clear_hint_and_report else existing.repair_hint,
                depends_on=tuple(deps),
            )
        )
        if new_node.statement_hash != existing.statement_hash:
            self._cascade_statement_change(target)
        return target

    # -- user.hint_attached --
    def _apply_hint_attached(
        self,
        target: str | None,
        payload: dict[str, Any],
        *,
        ts: str = "",
    ) -> str:
        if not isinstance(target, str):
            raise ProjectionRejection(
                "schema", "user.hint_attached requires a target label"
            )
        hint = payload.get("hint", "")
        if not isinstance(hint, str) or not hint.strip():
            raise ProjectionRejection("schema", "hint must be a non-empty string")

        existing = self._kb.node_by_label(target)
        if existing is None:
            raise ProjectionRejection(
                REASON_HINT_TARGET_MISSING, f"target {target!r} does not exist"
            )
        if existing.pass_count >= 1:
            raise ProjectionRejection(
                REASON_HINT_TARGET_UNREACHABLE,
                f"target {target!r} pass_count={existing.pass_count}",
            )

        # Append a user section to repair_hint (§5.4 L1246). The event's
        # top-level ``ts`` is preferred; ``payload.ts`` is a legacy
        # fallback, and a literal ``"user"`` keeps prior behaviour for
        # hand-rolled events that omit both.
        #
        # The ``---`` divider is the *separator* between sections, so it
        # only appears when there is something to separate from. When
        # ``repair_hint`` is empty the new section stands alone. Otherwise
        # the merge in :func:`_merge_verifier_section` would split the
        # field on ``\n---\n`` and find a single block that begins with
        # ``---`` rather than ``[user @ ``, dropping the hint silently.
        section_ts = ts or payload.get("ts", "") or "user"
        new_section = f"[user @ {section_ts}]\n{hint.rstrip()}\n"
        if existing.repair_hint:
            updated = existing.repair_hint + "\n---\n" + new_section
        else:
            updated = new_section
        self._kb.set_node_fields(target, repair_hint=updated)
        return target

    # -- generator.batch_committed --
    def _apply_generator_batch(self, payload: dict[str, Any]) -> str:
        target = payload.get("target")
        nodes = payload.get("nodes") or []
        if not target or not isinstance(nodes, list) or not nodes:
            raise ProjectionRejection("schema", "generator.batch_committed requires target + nodes[]")
        labels = [n.get("label") for n in nodes]
        if target not in labels:
            raise ProjectionRejection(
                "schema", f"batch target {target!r} missing from nodes[]"
            )
        if len(labels) != len(set(labels)):
            raise ProjectionRejection("schema", "duplicate labels within batch")

        # Pre-validate every batch node has required fields + kind.
        parsed: list[tuple[str, NodeKind, dict[str, Any]]] = []
        for entry in nodes:
            self._require_payload_fields(
                entry,
                {"label", "kind", "statement", "proof", "remark", "source_note"},
                etype="generator.batch_committed.node",
            )
            kind = self._parse_kind(entry["kind"])
            if kind is NodeKind.EXTERNAL_THEOREM:
                raise ProjectionRejection(
                    "schema", "generator cannot introduce external_theorem nodes"
                )
            _check_label_prefix(entry["label"], kind)
            parsed.append((entry["label"], kind, entry))

        # Write-scope invariant: each batch label is either the target
        # (can already exist) or brand-new (must not exist).
        batch_label_set = set(labels)
        for lbl, _kind, _ in parsed:
            if lbl == target:
                continue
            if self._kb.node_by_label(lbl) is not None:
                raise ProjectionRejection(
                    REASON_LABEL_CONFLICT,
                    f"batch label {lbl!r} already exists (not the target)",
                )

        # Dispatch order: brand-new labels first (in batch-internal
        # topological order) so that when we get to the target, all
        # intra-batch references resolve to freshly inserted rows.
        order = _batch_topological_order(parsed)

        # First pass: build the staged Node list (for hashes + kind checks).
        staged: dict[str, Node] = {}
        for lbl in order:
            entry = next(e for (l, _, e) in parsed if l == lbl)
            kind = next(k for (l, k, _) in parsed if l == lbl)
            stmt = entry["statement"]
            proof = entry["proof"]
            deps = _extract_refs(stmt + "\n" + proof)
            if lbl in deps:
                raise ProjectionRejection(REASON_SELF_REFERENCE, lbl)
            for dep in deps:
                if dep in batch_label_set:
                    continue  # satisfied by batch
                if self._kb.node_by_label(dep) is None:
                    raise ProjectionRejection(
                        REASON_REF_MISSING, f"\\ref{{{dep}}} not found"
                    )
            node = self._assemble_node_with_staged(
                label=lbl,
                kind=kind,
                statement=stmt,
                proof=proof,
                depends_on=deps,
                remark=entry.get("remark", ""),
                source_note=entry.get("source_note", ""),
                staged=staged,
            )
            staged[lbl] = node

        # Kind immutability for target when it already exists.
        existing_target = self._kb.node_by_label(target)
        target_node = staged[target]
        if existing_target is not None:
            old_kind = NodeKind(existing_target.kind)
            if old_kind is not target_node.kind:
                raise ProjectionRejection(
                    REASON_KIND_MUTATION,
                    f"kind {old_kind.value} -> {target_node.kind.value} not allowed",
                )

        # Cross-batch cycle check. After the staged batch lands, does any
        # dep edge close a cycle? We build a working adjacency map of the
        # current KB edges, then walk the batch in topological order
        # adding each label's NEW outgoing edges (full deps, including
        # batch-internal references). For the target — which already
        # exists and is being revised — we drop its old edges first
        # since update_node will replace them. Per-label deps are checked
        # against the running map, so an intra-batch edge that, combined
        # with another already-staged batch edge, closes a cycle through
        # existing KB nodes is caught here. Without this, a multi-node
        # batch could land a real cycle (e.g. KB has b->c; batch revises
        # c to ref new a, and a refs existing b — closing c->a->b->c).
        adjacency: dict[str, list[str]] = {}
        res = self._kb._conn.execute(
            "MATCH (u:Node)-[:DependsOn]->(v:Node) RETURN u.label, v.label"
        )
        while res.has_next():
            src, dst = res.get_next()
            adjacency.setdefault(src, []).append(dst)
        if existing_target is not None:
            adjacency.pop(target, None)

        for lbl in order:
            node = staged[lbl]
            for dep in node.depends_on:
                if dep == lbl:
                    raise ProjectionRejection(REASON_SELF_REFERENCE, lbl)
                path = _bfs_path(adjacency, dep, lbl)
                if path is not None:
                    raise ProjectionRejection(
                        REASON_CYCLE, _cycle_detail([lbl, *path])
                    )
            if node.depends_on:
                adjacency.setdefault(lbl, []).extend(node.depends_on)

        # Apply in order: brand-new nodes with CREATE, target node with
        # UPDATE if it already exists else CREATE.
        for lbl in order:
            node = staged[lbl]
            if lbl == target and existing_target is not None:
                # Preserve repair_count when statement_hash unchanged;
                # reset when changed (§5.4 row for generator.batch_committed).
                clear_repair_count = node.statement_hash != existing_target.statement_hash
                clear_hint_and_report = (
                    node.verification_hash != existing_target.verification_hash
                )
                self._kb.update_node(
                    Node(
                        label=node.label,
                        kind=node.kind,
                        statement=node.statement,
                        proof=node.proof,
                        remark=node.remark,
                        source_note=node.source_note,
                        pass_count=node.initial_count(),
                        repair_count=0 if clear_repair_count else existing_target.repair_count,
                        statement_hash=node.statement_hash,
                        verification_hash=node.verification_hash,
                        verification_report="" if clear_hint_and_report else existing_target.verification_report,
                        repair_hint="" if clear_hint_and_report else existing_target.repair_hint,
                        depends_on=node.depends_on,
                    )
                )
            else:
                self._kb.create_node(node)

        # Cascade Merkle updates for any target whose statement_hash changed.
        if existing_target is not None and (
            target_node.statement_hash != existing_target.statement_hash
        ):
            self._cascade_statement_change(target)
        return target

    # -- verifier.run_completed --
    def _apply_verifier_run(
        self, event: dict[str, Any], payload: dict[str, Any]
    ) -> str | None:
        target = event.get("target")
        if not isinstance(target, str):
            raise ProjectionRejection(
                "schema", "verifier.run_completed requires a target label"
            )
        self._require_payload_fields(
            payload,
            {"verdict", "verification_hash", "verification_report", "repair_hint"},
            etype="verifier.run_completed",
        )
        verdict = payload["verdict"]
        if verdict not in ("accepted", "gap", "critical"):
            raise ProjectionRejection(
                "schema", f"invalid verdict {verdict!r}"
            )
        carried_vh = payload["verification_hash"]
        existing = self._kb.node_by_label(target)
        if existing is None:
            raise ProjectionRejection(REASON_REF_MISSING, target)
        if carried_vh != existing.verification_hash:
            raise ProjectionRejection(
                REASON_HASH_MISMATCH,
                f"stale={carried_vh[:12]} current={existing.verification_hash[:12]}",
            )

        report = _stringify(payload["verification_report"])
        hint = payload.get("repair_hint", "")
        if verdict == "accepted":
            self._kb.set_node_fields(
                target,
                pass_count=existing.pass_count + 1 if existing.pass_count >= 0 else 1,
                verification_report=report,
            )
        else:
            # gap / critical: pass_count -> -1, repair_count += 1,
            # overwrite verifier section of repair_hint, preserve user sections.
            new_hint = _merge_verifier_section(existing.repair_hint, hint)
            self._kb.set_node_fields(
                target,
                pass_count=-1,
                repair_count=existing.repair_count + 1,
                verification_report=report,
                repair_hint=new_hint,
            )
        return target

    # ---- helpers -------------------------------------------------
    @staticmethod
    def _parse_kind(raw: Any) -> NodeKind:
        if not isinstance(raw, str):
            raise ProjectionRejection("schema", f"kind must be a string, got {type(raw).__name__}")
        try:
            return NodeKind(raw)
        except ValueError:
            raise ProjectionRejection("schema", f"unknown kind {raw!r}")

    @staticmethod
    def _require_payload_fields(
        payload: dict[str, Any], required: set[str], *, etype: str
    ) -> None:
        missing = required - set(payload)
        if missing:
            raise ProjectionRejection(
                "schema", f"{etype} payload missing: {sorted(missing)}"
            )

    def _assemble_node(
        self,
        *,
        label: str,
        kind: NodeKind,
        statement: str,
        proof: str,
        depends_on: list[str],
        remark: str,
        source_note: str,
    ) -> Node:
        dep_refs: list[DepRef] = []
        for d in depends_on:
            row = self._kb.node_by_label(d)
            if row is None:
                raise ProjectionRejection(REASON_REF_MISSING, d)
            dep_refs.append(DepRef(label=d, statement_hash=row.statement_hash))
        sh = statement_hash(
            label=label,
            kind=kind.value,
            statement=statement,
            depends_on=dep_refs,
        )
        vh = verification_hash(statement_hash_hex=sh, proof=proof)
        return Node(
            label=label,
            kind=kind,
            statement=statement,
            proof=proof,
            remark=remark,
            source_note=source_note,
            pass_count=0 if (kind not in PROOF_REQUIRING_KINDS or proof) else -1,
            repair_count=0,
            statement_hash=sh,
            verification_hash=vh,
            depends_on=tuple(depends_on),
        )

    def _assemble_node_with_staged(
        self,
        *,
        label: str,
        kind: NodeKind,
        statement: str,
        proof: str,
        depends_on: list[str],
        remark: str,
        source_note: str,
        staged: dict[str, Node],
    ) -> Node:
        dep_refs: list[DepRef] = []
        for d in depends_on:
            if d in staged:
                dep_refs.append(
                    DepRef(label=d, statement_hash=staged[d].statement_hash)
                )
                continue
            row = self._kb.node_by_label(d)
            if row is None:
                raise ProjectionRejection(REASON_REF_MISSING, d)
            dep_refs.append(DepRef(label=d, statement_hash=row.statement_hash))
        sh = statement_hash(
            label=label, kind=kind.value, statement=statement, depends_on=dep_refs
        )
        vh = verification_hash(statement_hash_hex=sh, proof=proof)
        return Node(
            label=label,
            kind=kind,
            statement=statement,
            proof=proof,
            remark=remark,
            source_note=source_note,
            pass_count=0 if (kind not in PROOF_REQUIRING_KINDS or proof) else -1,
            repair_count=0,
            statement_hash=sh,
            verification_hash=vh,
            depends_on=tuple(depends_on),
        )

    def _cascade_statement_change(self, start_label: str) -> None:
        """BFS up dependents; recompute statement_hash / verification_hash."""
        seen: set[str] = set()
        frontier = list(self._kb.dependents_of(start_label))
        while frontier:
            lbl = frontier.pop(0)
            if lbl in seen:
                continue
            seen.add(lbl)
            row = self._kb.node_by_label(lbl)
            if row is None:
                continue
            deps = self._kb.dependencies_of(lbl)
            dep_refs = []
            for d in deps:
                d_row = self._kb.node_by_label(d)
                if d_row is None:
                    continue
                dep_refs.append(DepRef(label=d, statement_hash=d_row.statement_hash))
            new_sh = statement_hash(
                label=lbl, kind=row.kind, statement=row.statement, depends_on=dep_refs
            )
            new_vh = verification_hash(
                statement_hash_hex=new_sh, proof=row.proof or ""
            )
            if new_sh == row.statement_hash and new_vh == row.verification_hash:
                continue
            updates: dict[str, Any] = {
                "statement_hash": new_sh,
                "verification_hash": new_vh,
            }
            if new_sh != row.statement_hash:
                updates["repair_count"] = 0
            if new_vh != row.verification_hash:
                updates["repair_hint"] = ""
                updates["verification_report"] = ""
                # pass_count falls back to initial_count(kind, proof) since
                # prior verdicts no longer apply.
                kind = NodeKind(row.kind)
                if kind not in PROOF_REQUIRING_KINDS or row.proof:
                    updates["pass_count"] = 0
                else:
                    updates["pass_count"] = -1
            self._kb.set_node_fields(lbl, **updates)
            frontier.extend(self._kb.dependents_of(lbl))


# ---------------------------------------------------------------------------
# Helpers shared by rules + tests.
# ---------------------------------------------------------------------------
def _check_label_prefix(label: str, kind: NodeKind) -> None:
    expected = KIND_PREFIX[kind]
    if ":" not in label:
        raise ProjectionRejection("schema", f"label {label!r} missing ``prefix:slug`` form")
    prefix, _, slug = label.partition(":")
    if prefix != expected:
        raise ProjectionRejection(
            "schema", f"label {label!r} prefix does not match kind {kind.value}"
        )
    if not slug or not LABEL_SLUG_RE.match(slug):
        raise ProjectionRejection("schema", f"label {label!r} has invalid slug")
    if label in PLACEHOLDER_LABELS:
        raise ProjectionRejection("schema", f"label {label!r} is a reserved placeholder")


def _stringify(obj: Any) -> str:
    import json

    if isinstance(obj, str):
        return obj
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _merge_verifier_section(existing: str, new_verifier: str) -> str:
    """Overwrite the verifier section; preserve any user sections.

    The ``repair_hint`` structure is documented in §5.2 / §5.4:
    a verifier-maintained section followed by zero-or-more user sections
    separated by lines containing ``---``. The user sections are always
    preceded by an ``[user @ ...]`` header, so we can safely split on
    ``---`` and keep every section whose body begins with that header.
    """
    new_section = (new_verifier or "").rstrip()
    if not existing:
        return new_section + ("\n" if new_section else "")
    sections = [s for s in existing.split("\n---\n")]
    user_sections = [s for s in sections if s.lstrip().startswith("[user @ ")]
    pieces: list[str] = []
    if new_section:
        pieces.append(new_section)
    pieces.extend(user_sections)
    joined = "\n---\n".join(p.rstrip() for p in pieces)
    return joined + ("\n" if joined else "")


def _extract_refs(text: str) -> list[str]:
    """Extract labels from ``\\ref{...}`` occurrences in ``text``.

    Returned order is the first-appearance order with duplicates removed.
    """
    import re

    seen: list[str] = []
    for m in re.finditer(r"\\ref\{([^}]+)\}", text):
        label = m.group(1).strip()
        if label and label not in seen:
            seen.append(label)
    return seen


def _cycle_detail(path: list[str]) -> str:
    return "would close cycle: " + " -> ".join(path)


def _batch_topological_order(
    parsed: list[tuple[str, NodeKind, dict[str, Any]]],
) -> list[str]:
    """Order batch labels so brand-new nodes come before nodes that reference them.

    Uses a simple Kahn-style sort on intra-batch ``\\ref{}`` edges.
    Edges to labels outside the batch are ignored (they must already
    exist in KB; the caller verifies this separately).
    """
    labels = {lbl for (lbl, _, _) in parsed}
    graph: dict[str, set[str]] = {lbl: set() for lbl in labels}
    entries = {lbl: entry for (lbl, _, entry) in parsed}
    for lbl in labels:
        refs = _extract_refs(
            entries[lbl]["statement"] + "\n" + entries[lbl].get("proof", "")
        )
        for ref in refs:
            if ref in labels and ref != lbl:
                graph[lbl].add(ref)

    # Kahn: repeatedly pop labels with no outstanding deps in the batch.
    order: list[str] = []
    remaining = {lbl: set(deps) for lbl, deps in graph.items()}
    while remaining:
        ready = sorted(lbl for lbl, deps in remaining.items() if not deps)
        if not ready:
            # Batch-internal cycle detected here as a safety net; the
            # decoder should have caught it in M6, but libreprojector
            # should still refuse rather than loop.
            raise ProjectionRejection(
                REASON_CYCLE,
                "batch-internal cycle among: "
                + ", ".join(sorted(remaining)),
            )
        for lbl in ready:
            order.append(lbl)
            del remaining[lbl]
            for deps in remaining.values():
                deps.discard(lbl)
    return order
