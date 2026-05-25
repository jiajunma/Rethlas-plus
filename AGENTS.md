# AGENTS.md

Working contract for coding agents in the `rethlas-kb` branch of
Rethlas-plus. Written for Codex, Claude Code, OpenCode, and similar
tools. If your agent does not auto-load this file, open it manually
before editing.

## Project Purpose

Rethlas-KB is the **agent layer** for doing new research mathematics
on an [mdblueprint](https://github.com/jiajunma/mdblueprint) knowledge
base.

The boundary is strict:

- **mdblueprint** owns the durable knowledge: markdown nodes under
  `docs/knowledge/{nodes,staged,reviews,requests,sources}/`,
  deterministic Python validation, static-site publishing.
- **Rethlas-KB** owns the agents: codex / claude / opencode CLIs
  wrapped behind a uniform `AgentBackend` Protocol, composed into
  five agent roles + a `--depth`-aware verification pipeline.
- Admission to `nodes/` follows mdblueprint's own admission flow.
  Rethlas-KB writes only into `staged/`, `reviews/`, and `requests/`.

## Repository Structure (target, not yet built)

```text
rethlas_kb/
  cli.py             entry point (rethlas-kb {verify-stmt, verify-proof, ...})
  adapter.py         single boundary to mdblueprint KB I/O
  config.py          agent ↔ backend wiring + cross-backend isolation validation
  project.py         (v1.3) project manifest + closure
  backends/
    base.py          AgentBackend Protocol + AgentResult + registry
    codex.py         codex CLI subprocess wrapper
    claude.py        claude CLI subprocess wrapper
    opencode.py      (beyond v1) opencode CLI subprocess wrapper

rethlas_kb_agents/
  statement_verifier/        {role, prompt, decoder}.py
  proof_gap_filler/          {role, prompt, decoder}.py
  proof_verifier/            QED-style 3-stage (judge / structural / detailed)
  counterexample_hunter/     {role, prompt, decoder}.py
  source_claim_verifier/     PDF extractor + alignment + reuses proof_verifier

tests/
  adapter/                   mdblueprint KB I/O integration
  agents/                    per-agent role unit tests (mock backend)
  backends/                  per-backend wrapper unit tests
```

## Development Commands

```bash
# Install
uv sync

# Test suite
uv run pytest -q

# Single agent CLI smoke
uv run rethlas-kb verify-stmt <node-id> --backend claude

# Lint / format
uv run ruff check .
uv run ruff format .
```

mdblueprint is referenced as an **editable local source** via
`pyproject.toml::tool.uv.sources`. mdblueprint must be cloned at
`/Users/hoxide/mycodes/mdblueprint`.

## Agent Contracts (Rethlas-KB v1)

Five Rethlas-KB agents, each maps to one mdblueprint agent contract
(or implements an extension specific to research mathematics).

| Agent | mdblueprint role | Decision vocabulary | Writes to |
|---|---|---|---|
| `statement-verifier` | Statement Verifier | `accepted, needs_definition, generality_concern, formulation_issue` | `reviews/` |
| `proof-gap-filler` | Proof-Fill Generator | `filled, partial, cannot_fill` | `staged/` (updated proof), `requests/` (new sub-lemmas), `reviews/` (decision log) |
| `proof-verifier` | Proof Verifier | (per stage) `easy / hard`, `pass / fail`, `accepted / gap / critical / uncertain` | `reviews/proof-<id>-{judge,structural,detailed}-*.md` |
| `counterexample-hunter` | (Rethlas-KB extension) | `counterexample_found, no_counterexample_found, inconclusive` | `reviews/` |
| `source-claim-verifier` | (Rethlas-KB extension on `external-theorem` kind) | `accepted, mismatch, proof_gap, proof_critical, cannot_verify` | `reviews/source-audit-*.md` |

### Backend Isolation Constraint

The following pairs of agents **must use different LLM backends** in
production (enforced at startup by `rethlas_kb/config.py`):

- `proof-gap-filler` ≠ `proof-verifier`
- `proof-gap-filler` ≠ `source-claim-verifier`

This is a same-source-bias avoidance constraint. A single-machine debug
override is available via `--allow-same-backend`.

### QED-Style Difficulty-Adaptive Verification

`proof-verifier` and `source-claim-verifier` each implement an internal
3-stage pipeline:

```text
judge(cheap)
  ├─ if easy:  one-call full verification → done
  └─ if hard:  structural(medium)
                ├─ if FAIL:  short-circuit, no detailed run
                └─ if PASS:  detailed(deep) → done
```

CLI exposes `--depth {auto|easy|structural|detailed}`. Admission
credentials require `--depth detailed` passes. `structural` is for the
creative phase; `detailed` is for the convergence phase.

Pattern borrowed from
[QED](https://arxiv.org/abs/2604.24021) `verify/verify.py`.

## Forbidden / Be Careful

- Do not write to `nodes/` directly; mdblueprint admission flow only.
- Do not silently degrade on backend errors — surface them.
- Do not bypass `validate_backend_isolation` without a clearly logged
  `--allow-same-backend` reason.
- Do not import `mdblueprint` internals beyond the documented public
  API; if something is missing, raise it upstream in mdblueprint.
- Do not commit `~/mycodes/mdblueprint` into this repo; it is an
  editable dependency, not a submodule and not vendored.

## Reference Reading

- Design rationale: this conversation's archive + [ROADMAP.md](ROADMAP.md)
- mdblueprint architecture: `~/mycodes/mdblueprint/docs/architecture.md`
- mdblueprint agent contracts: `~/mycodes/mdblueprint/docs/agent-contracts.md`
- QED verification pipeline: `~/mycodes/QED/verify/verify.py`
- Test fixture KB: `~/mydoc/sheavesonbuilding/` (Ma-Wang-Yu in-progress paper)
