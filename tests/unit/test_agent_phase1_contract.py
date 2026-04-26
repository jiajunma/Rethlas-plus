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
