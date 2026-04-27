"""Static checks for Phase I agent packaging/contracts."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_generator_codex_config_caps_recursive_depth() -> None:
    cfg = _load_toml(ROOT / "agents" / "generation" / ".codex" / "config.toml")
    assert cfg["features"]["multi_agent"] is True
    assert cfg["agents"]["max_depth"] == 2


def test_verifier_codex_config_has_no_mcp_server() -> None:
    cfg = _load_toml(ROOT / "agents" / "verification" / ".codex" / "config.toml")
    assert cfg["features"]["multi_agent"] is True
    assert "mcp_servers" not in cfg


def test_generator_mcp_server_exposes_exact_phase1_tools() -> None:
    text = (ROOT / "agents" / "generation" / "mcp" / "server.py").read_text(
        encoding="utf-8"
    )
    tools = set(re.findall(r'@app\.tool\(name="([^"]+)"\)', text))
    assert tools == {
        "search_arxiv_theorems",
        "memory_init",
        "memory_append",
        "memory_search",
        "branch_update",
    }


def test_verifier_agents_skill_order_matches_architecture() -> None:
    text = (ROOT / "agents" / "verification" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "1. `$check-referenced-statements`" in text
    assert "2. `$verify-sequential-statements`" in text
    assert "3. `$synthesize-verification-report`" in text


def test_generator_query_memory_skill_keeps_plan_and_attempt_channels() -> None:
    text = (
        ROOT / "agents" / "generation" / ".agents" / "skills" / "query-memory" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "`subgoals`" in text
    assert "`proof_steps`" in text


def test_generator_agent_contract_forbids_old_verification_workflow_terms() -> None:
    text = (ROOT / "agents" / "generation" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    forbidden = [
        "verify_proof_service",
        "blueprint_verified.md",
        "section_verification",
    ]
    for token in forbidden:
        assert token not in text
    assert "scratch_events" in text
    assert "external_theorem" in text


def test_generator_recursive_skill_is_explicitly_one_layer_bounded() -> None:
    text = (
        ROOT
        / "agents"
        / "generation"
        / ".agents"
        / "skills"
        / "recursive-proving"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "at most one bounded layer" in text
    assert "not to spawn further sub-agents" in text


def test_generator_search_skill_forbids_pdf_downloads() -> None:
    text = (
        ROOT
        / "agents"
        / "generation"
        / ".agents"
        / "skills"
        / "search-math-results"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Do not download PDFs or write files." in text


def test_verifier_agent_contract_forbids_mcp_and_external_search() -> None:
    text = (ROOT / "agents" / "verification" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    for token in ("- MCP tools", "- web search", "- arXiv / theorem search"):
        assert token in text
    assert "external_reference_checks" in text


def test_generator_agents_md_documents_memory_scope_section() -> None:
    """H20/F9: AGENTS.md must teach Codex to use the prompt's Memory scope
    section verbatim, otherwise sub-agents shard scratch memory."""
    text = (ROOT / "agents" / "generation" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "### Memory scope (`problem_id`)" in text
    assert "## Memory scope" in text


def test_generator_agents_md_documents_identifier_conventions() -> None:
    """H17: AGENTS.md must hold the canonical derivation rules for
    plan_id / subgoal_id / branch_id / decision_id; skill files defer
    to this table to avoid drift."""
    text = (ROOT / "agents" / "generation" / "AGENTS.md").read_text(
        encoding="utf-8"
    )
    assert "### Identifier conventions" in text
    for token in ("plan_id", "subgoal_id", "branch_id", "decision_id"):
        assert token in text


def test_init_materializes_agents_into_workspace(tmp_path) -> None:
    """H22: ``rethlas init`` must copy the Phase I agent tree into the
    workspace so codex worker invocations load AGENTS.md / .codex/
    config / skills from the workspace itself, not from the operator's
    home dir or a sibling project's old artifacts."""
    from cli.init import run_init

    rc = run_init(str(tmp_path))
    assert rc == 0, "rethlas init should succeed in a fresh workspace"

    gen_dir = tmp_path / "agents" / "generation"
    ver_dir = tmp_path / "agents" / "verification"
    assert gen_dir.is_dir(), "generation agent dir not materialized"
    assert ver_dir.is_dir(), "verification agent dir not materialized"

    for marker in (
        gen_dir / "AGENTS.md",
        gen_dir / ".codex" / "config.toml",
        gen_dir / ".agents" / "skills",
        gen_dir / "mcp" / "server.py",
        ver_dir / "AGENTS.md",
        ver_dir / ".codex" / "config.toml",
        ver_dir / ".agents" / "skills",
    ):
        assert marker.exists(), f"materialized agent tree missing {marker}"

    # H22 also requires that runtime-only directories are NOT carried
    # into the workspace copy — they pollute and may leak prior runs.
    for forbidden in (
        gen_dir / ".venv",
        gen_dir / "memory",
        gen_dir / "logs",
        gen_dir / "results",
        gen_dir / "data",
        gen_dir / "site",
        gen_dir / "scripts",
        ver_dir / ".venv",
        ver_dir / "results",
    ):
        assert not forbidden.exists(), (
            f"materialized agent tree should not include {forbidden}"
        )


def test_role_codex_invocation_is_workspace_bounded() -> None:
    """H22: generator/role.py and verifier/role.py must invoke codex with
    cwd inside the workspace agent dir (so .. cannot escape into the
    user's home dir) and ``-C agent_dir --add-dir workspace`` to limit
    the writable scope. H23: must not pass ``-m auto`` (rejected by
    ChatGPT-account login). Pure source-string assertion so we don't
    have to spin up a real subprocess to verify the contract."""
    for path_str in ("generator/role.py", "verifier/role.py"):
        text = (ROOT / path_str).read_text(encoding="utf-8")
        assert "agent_kind_dir" in text, (
            f"{path_str} must derive its codex cwd via "
            "common.runtime.agents_install.agent_kind_dir"
        )
        assert '"-C"' in text and '"--add-dir"' in text, (
            f"{path_str} must pass -C and --add-dir to codex"
        )
        assert "cwd=codex_cwd" in text, (
            f"{path_str} must pass cwd to run_codex"
        )
        # H23: -m auto is incompatible with ChatGPT-account login. The
        # default codex argv must rely on the agent .codex/config.toml's
        # ``model = "..."`` field instead.
        assert '"auto"' not in text, (
            f"{path_str} must not pass `-m auto` (H23 — ChatGPT-account "
            "login rejects it). Let the agent .codex/config.toml drive "
            "model selection instead."
        )


def test_workers_pass_dangerously_bypass_flag_for_mcp() -> None:
    """H28: under codex 0.125, ``codex exec`` running with any combination
    of ``--ask-for-approval ...`` + ``--sandbox workspace-write|read-only``
    auto-cancels MCP tool calls (``user cancelled MCP tool call``). A
    direct stdio probe of the Phase I MCP server returns valid JSON, and
    switching the worker invocation to
    ``--dangerously-bypass-approvals-and-sandbox`` makes the same call
    succeed and write the memory tree. The Phase I safety boundary is
    the three-layer validation surface (decoder structural ``REASON_*``
    constants + librarian projector physical-integrity checks +
    verifier content schema, per H29), not codex's sandbox, so the
    bypass is acceptable
    here. Both worker roles MUST pass the flag so MCP scratch memory
    and the verifier's tool calls keep flowing through exec dispatch."""
    for path_str in ("generator/role.py", "verifier/role.py"):
        text = (ROOT / path_str).read_text(encoding="utf-8")
        assert '"--dangerously-bypass-approvals-and-sandbox"' in text, (
            f"{path_str} must pass "
            "--dangerously-bypass-approvals-and-sandbox to codex exec (H28)"
        )


def test_materialize_pins_mcp_python_to_sys_executable() -> None:
    """H28: codex spawns the MCP server with the literal command from
    ``.codex/config.toml``. Upstream ships ``command = "python3"`` which
    resolves through PATH at codex-launch time — usually to a system
    Python without ``fastmcp``/``requests`` installed, producing
    ``user cancelled MCP tool call`` on every MCP call.
    ``materialize_agents`` must rewrite the command to the rethlas CLI's
    ``sys.executable`` so the MCP server runs in the same venv that
    imports ``fastmcp`` during the init-time preflight."""
    text = (ROOT / "common" / "runtime" / "agents_install.py").read_text(
        encoding="utf-8"
    )
    assert "_patch_mcp_python_command" in text, (
        "agents_install.py must patch the MCP command to sys.executable "
        "after copying the agent tree (H28)."
    )
    assert "sys.executable" in text, (
        "agents_install.py must reference sys.executable when patching "
        "the MCP command (H28)."
    )


def test_decoder_reason_constants_match_documented_count() -> None:
    """H21 (post-H29): after the three-layer validation revision the
    decoder is structural-only and exports exactly five ``REASON_*``
    constants. ARCH §6.2 calls them "five `reason` values" and PHASE1
    M6 lists "5 structural failure modes". This test pins the count so
    any new constant must update both documents and add a dedicated
    unit test, AND any new content judgment must instead land at the
    librarian projector (`apply_failed`) or the verifier (`gap` /
    `critical` verdict) per ARCH §3.1.6."""
    decoder_text = (ROOT / "generator" / "decoder.py").read_text(
        encoding="utf-8"
    )
    constants = re.findall(r"^REASON_[A-Z_]+ = \"[^\"]+\"", decoder_text, re.M)
    assert len(constants) == 5, (
        f"expected 5 REASON_* constants (post-H29), found {len(constants)}; "
        "either update ARCH §6.2 + PHASE1 M6, or move the new check to "
        "the projector / verifier per the H29 boundary"
    )

    arch_text = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "five `reason` values" in arch_text

    phase1_text = (ROOT / "docs" / "PHASE1.md").read_text(encoding="utf-8")
    assert "5 structural failure" in phase1_text


def test_verifier_skills_keep_phase1_status_vocabulary() -> None:
    text = (
        ROOT
        / "agents"
        / "verification"
        / ".agents"
        / "skills"
        / "check-referenced-statements"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    for token in (
        "verified_in_nodes",
        "verified_external_theorem_node",
        "missing_from_nodes",
        "insufficient_information",
        "not_applicable",
    ):
        assert token in text
