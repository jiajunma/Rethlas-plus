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
