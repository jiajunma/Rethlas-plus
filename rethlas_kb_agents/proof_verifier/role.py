"""ProofVerifier role — 3-stage pipeline with short-circuit (issue #9).

The role exposes:

- ``run_judge / run_structural / run_detailed`` — one-shot per-stage
  calls. Mode A uses these directly via separate slash commands.
- ``run(node_id, adapter, *, depth='auto')`` — pipeline that
  composes the per-stage calls per the depth flag. Mode B's
  ``verify-proof`` subcommand uses this.

Depth semantics:

  ``auto``        — judge → if easy stop; if hard → structural → if
                     pass → detailed
  ``easy``        — only judge; even if it returns hard, the verdict
                     stays as "judge says hard"
  ``structural``  — skip judge, run structural only
  ``detailed``    — skip judge and structural; run detailed alone
                     (assumes caller has already verified structure)

Cross-backend isolation (#13) is enforced one level up. This role
does not know which LLM it is calling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import AgentBackend

from .decoder import (
    DetailedVerdict,
    JudgeVerdict,
    ProofReview,
    StructuralVerdict,
    parse_detailed,
    parse_judge,
    parse_structural,
)
from .prompts import compose_detailed, compose_judge, compose_structural


AGENT_ROLE = "proof-verifier"

Depth = Literal["auto", "easy", "structural", "detailed"]
_VALID_DEPTHS: frozenset[str] = frozenset({"auto", "easy", "structural", "detailed"})


@dataclass
class ProofVerifier:
    """Bind an ``AgentBackend`` to the proof-verifier role.

    ``backend_per_stage`` lets Mode B route different stages to
    different backends (e.g. cheap codex for judge, opus for
    detailed). When ``None`` the same ``backend`` is used for all
    stages. Mode A doesn't use this — the agentic CLI is the same
    process across stage calls.
    """

    backend: AgentBackend
    timeout_seconds: int = 600
    include_staged_context: bool = True
    backend_per_stage: dict[str, AgentBackend] | None = None

    @property
    def name(self) -> str:
        return AGENT_ROLE

    # -- single-stage entry points ------------------------------------------
    def run_judge(self, node_id: str, adapter: KbAdapter) -> JudgeVerdict:
        node, context, rules = self._load_inputs(node_id, adapter)
        prompt = compose_judge(node, context, project_rules=rules)
        result = self._backend_for("judge").run(
            agent_role=AGENT_ROLE,
            prompt=prompt,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse_judge(result.stdout)

    def run_structural(
        self, node_id: str, adapter: KbAdapter,
    ) -> StructuralVerdict:
        node, context, rules = self._load_inputs(node_id, adapter)
        prompt = compose_structural(node, context, project_rules=rules)
        result = self._backend_for("structural").run(
            agent_role=AGENT_ROLE,
            prompt=prompt,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse_structural(result.stdout)

    def run_detailed(
        self,
        node_id: str,
        adapter: KbAdapter,
        *,
        structural_report: str = "",
    ) -> DetailedVerdict:
        node, context, rules = self._load_inputs(node_id, adapter)
        prompt = compose_detailed(
            node, context,
            project_rules=rules,
            structural_report=structural_report,
        )
        result = self._backend_for("detailed").run(
            agent_role=AGENT_ROLE,
            prompt=prompt,
            timeout_seconds=self.timeout_seconds,
            output_format="json",
        )
        return parse_detailed(result.stdout)

    # -- pipeline ------------------------------------------------------------
    def run(
        self,
        node_id: str,
        adapter: KbAdapter,
        *,
        depth: Depth = "auto",
    ) -> ProofReview:
        if depth not in _VALID_DEPTHS:
            raise ValueError(
                f"depth {depth!r} not in {sorted(_VALID_DEPTHS)}"
            )

        judge: JudgeVerdict | None = None
        structural: StructuralVerdict | None = None
        detailed: DetailedVerdict | None = None
        short_circuited_at: str | None = None

        if depth in ("auto", "easy"):
            judge = self.run_judge(node_id, adapter)

            # Easy and depth='easy': always stop at judge
            if depth == "easy":
                return _aggregate(
                    depth=depth, judge=judge,
                    decisive_stage="judge",
                    short_circuited_at="depth_easy_cap",
                )
            # Easy verdict short-circuits the pipeline
            if judge.is_easy:
                return _aggregate(
                    depth=depth, judge=judge,
                    decisive_stage="judge",
                    short_circuited_at="judge_easy",
                )

        if depth in ("auto", "structural"):
            structural = self.run_structural(node_id, adapter)
            if depth == "structural":
                return _aggregate(
                    depth=depth, judge=judge, structural=structural,
                    decisive_stage="structural",
                )
            if not structural.passed:
                return _aggregate(
                    depth=depth, judge=judge, structural=structural,
                    decisive_stage="structural",
                    short_circuited_at="structural_fail",
                )

        # depth=detailed reaches here directly; auto/structural fall through
        # only when structural passed.
        detailed = self.run_detailed(
            node_id, adapter,
            structural_report=structural.raw if structural else "",
        )
        return _aggregate(
            depth=depth, judge=judge, structural=structural, detailed=detailed,
            decisive_stage="detailed",
        )

    # -- internals -----------------------------------------------------------
    def _load_inputs(
        self, node_id: str, adapter: KbAdapter,
    ):
        node = adapter.read_node(node_id)
        context = adapter.context_pack(
            target_id=node_id,
            include_staged=self.include_staged_context,
        )
        rules = adapter.read_project_rules_combined(AGENT_ROLE)
        return node, context, rules

    def _backend_for(self, stage: str) -> AgentBackend:
        if self.backend_per_stage and stage in self.backend_per_stage:
            return self.backend_per_stage[stage]
        return self.backend


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _aggregate(
    *,
    depth: Depth,
    judge: JudgeVerdict | None = None,
    structural: StructuralVerdict | None = None,
    detailed: DetailedVerdict | None = None,
    decisive_stage: str,
    short_circuited_at: str | None = None,
) -> ProofReview:
    final_verdict, rationale = _final_verdict(
        judge=judge,
        structural=structural,
        detailed=detailed,
        decisive_stage=decisive_stage,
    )
    return ProofReview(
        final_verdict=final_verdict,
        rationale=rationale,
        depth_requested=depth,
        judge=judge,
        structural=structural,
        detailed=detailed,
        decisive_stage=decisive_stage,
        short_circuited_at=short_circuited_at,
    )


def _final_verdict(
    *,
    judge: JudgeVerdict | None,
    structural: StructuralVerdict | None,
    detailed: DetailedVerdict | None,
    decisive_stage: str,
) -> tuple[str, str]:
    """Return ``(final_verdict, rationale)`` for the decisive stage."""
    if decisive_stage == "judge":
        if judge is None:
            return "uncertain", "judge stage requested but did not produce a result"
        if judge.is_easy and judge.verdict:
            return judge.verdict, f"judge (easy): {judge.rationale}"
        # Hard but capped at depth=easy: surface as uncertain
        return "uncertain", f"judge classified as {judge.difficulty}; deeper stages not run"

    if decisive_stage == "structural":
        if structural is None:
            return "uncertain", "structural stage requested but did not produce a result"
        if structural.passed:
            return "uncertain", (
                "structural pass — detailed stage not run "
                "(depth=structural)"
            )
        return "gap", f"structural fail: {structural.rationale}"

    if decisive_stage == "detailed":
        if detailed is None:
            return "uncertain", "detailed stage requested but did not produce a result"
        return detailed.verdict, f"detailed: {detailed.rationale}"

    return "uncertain", f"unknown decisive_stage {decisive_stage!r}"


__all__ = ["AGENT_ROLE", "Depth", "ProofVerifier"]
