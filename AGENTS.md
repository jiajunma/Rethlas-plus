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
- **Rethlas-KB** owns the agents: a uniform CLI toolkit
  (`rethlas-kb`) that agentic LLMs (codex / claude / opencode)
  call as tools while *they* do the orchestration. A Python
  end-to-end orchestration path (`StatementVerifier` etc.) also
  exists for batch / CI.
- Admission to `nodes/` follows mdblueprint's own admission flow.
  Rethlas-KB writes only into `staged/`, `reviews/`, and `requests/`.

## Two Orchestration Modes

> **Critical mental model.** Read this before changing anything.

| | **Mode A — agentic** *(primary)* | **Mode B — Python** *(batch/CI)* |
|---|---|---|
| Orchestrator | codex / claude / opencode (the LLM CLI itself) | Python (`StatementVerifier(backend).run(...)`) |
| Entry point | `claude /verify-stmt <node-id>` etc. | `rethlas-kb verify-stmt <node-id> --backend codex` |
| How LLM is reached | The agentic CLI IS the LLM; it calls `rethlas-kb` primitives as tools | Python shells out to `codex exec` / `claude -p` as subprocess |
| Decoder needed | No — LLM calls `rethlas-kb write-review` directly | Yes — Python parses JSON-from-prose |
| Slash-command file | `commands/{claude,codex,opencode}/verify-stmt.md` (#20) | n/a |
| Use when | Interactive research; agent needs to reason / search / cite / loop | Reproducible scripted runs; CI; benchmark grids |

**Rule of thumb**: when in doubt, use Mode A. Mode B exists so
results are reproducible, not because it's the better path.

## Repository Structure

```text
rethlas_kb/                    infrastructure (NEVER imports an agent)
  cli/
    __init__.py                public surface re-exports
    _constants.py              version / exit codes / DEFAULT_BACKEND
    _io.py                     project resolution / stdin / csv parsing
    main.py                    argparse dispatcher (`main`)
    workflows.py               Mode B subcommands (verify-stmt, …)
    primitives.py              Mode A subcommands (get-node, write-review, …)
  adapter.py                   ONE boundary to mdblueprint (KbAdapter)
  config.py                    (issue #13) agent ↔ backend wiring + isolation
  project.py                   (v1.3) project manifest + closure
  backends/
    base.py                    AgentBackend Protocol + AgentResult + BackendError
    codex.py                   codex CLI subprocess wrapper
    claude.py                  claude CLI subprocess wrapper
    mock.py                    MockBackend (tests only)
    opencode.py                (beyond v1)

rethlas_kb_agents/             agent prompts + decoders (Mode B uses these)
  statement_verifier/          done (#7, hardened from QED in commit a2844de)
  proof_verifier/              (#9) QED-style 3-stage
  proof_gap_filler/            (#10)
  counterexample_hunter/       (#11)
  source_claim_verifier/       (#12)

commands/                      (#20) slash-command templates per agentic CLI
  claude/                        verify-stmt.md / verify-proof.md / …
  codex/                         (same names)
  opencode/                      (same names)

tests/
  agents/                      per-agent role unit tests (MockBackend)
  backends/                    per-backend wrapper unit tests
  integration/                 @pytest.mark.slow real-LLM smoke tests
  test_adapter.py              tmp_path KB fixture
  test_cli.py                  Mode B end-to-end
  test_cli_primitives.py       Mode A primitives end-to-end
```

## Development Commands

```bash
# Install
uv sync --extra dev

# Test suite (slow tests skipped by default)
uv run pytest -q

# Slow real-LLM smoke tests (requires codex on PATH + sheaves repo)
uv run pytest -m slow

# Mode A: agentic CLI calls primitives
rethlas-kb compose-prompt statement-verifier <node-id> --project <kb>
rethlas-kb write-review <node-id> --agent statement-verifier \
    --decision accepted --rationale "…" --project <kb>

# Mode B: Python orchestrates end-to-end
rethlas-kb verify-stmt <node-id> --backend codex --project <kb>

# Lint / format
uv run ruff check .
uv run ruff format .
```

mdblueprint is referenced as an **editable local source** via
`pyproject.toml::tool.uv.sources`. mdblueprint must be cloned at
`/Users/hoxide/mycodes/mdblueprint`.

## Generator vs Verifier (taxonomy)

The five v1 agents split into **two abstract categories**. Every
agent prompt must respect the category's tone and discipline; don't
mix patterns across categories.

### Verifiers — judge existing content, never produce it

| Agent | Issue | Judges | Writes |
|---|---|---|---|
| `statement-verifier` | #7 ✅ | statement well-formedness | `reviews/` |
| `proof-verifier` | #9 | step-by-step proof correctness (3-stage) | `reviews/proof-…-{judge,structural,detailed}-*.md` |
| `source-claim-verifier` | #12 | external-theorem extraction fidelity | `reviews/source-audit-*.md` |

**Verifier discipline** (from QED `prompt_verify_*.md`):

- **Scope restriction**: each prompt opens with what the agent is
  *not* responsible for (e.g. statement-verifier explicitly says
  "you are NOT asked to verify the proof").
- **Conservative-by-default**: under uncertainty, pick the decision
  that *flags* a problem. Research math = unknown ground truth =
  "looks fine" is the dangerous default.
- **Verbatim-quote discipline**: the output schema requires a literal
  copy of the text being judged, so the model can't paraphrase
  itself into a false agreement.
- **Anti-pattern catalog**: explicit enumeration of the defects to
  scan for (quantifier drift, domain restriction, missing
  hypotheses, …).
- **Discriminated-union outputs**: every non-accepted decision has a
  required companion field (the decoder rejects bare `formulation_issue`
  without `formulation_issues`).
- **Confidence calibration**: numeric `confidence` is meaningless
  without anchors. Each band has a named meaning (0.9-1.0 obvious,
  0.7-0.9 defensible, …).
- **Context escape hatch**: a `context_insufficient` decision so the
  caller can widen the context pack instead of getting a low-confidence
  guess.

### Generators — produce new content from a target + context

| Agent | Issue | Produces | Writes |
|---|---|---|---|
| `proof-gap-filler` | #10 | filled / patched proof for a failing node | `staged/` (updated proof), `requests/` (sub-lemmas), `reviews/` |
| `counterexample-hunter` | #11 | refutation attempt with witness or impossibility argument | `reviews/` |
| *(future)* `statement-staging` | v1.5 lit-scout | new staged node extracted from arXiv / source PDF | `staged/`, `sources/` |

**Generator discipline** (from Rethlas-original `generator/prompt.py`
+ QED `super_math_skill.md`):

- **Stateful / repair-aware**: receives the failing verification
  report and acts on it. The prompt is parameterized on
  `repair_count` / `verification_report` / `repair_hint`.
- **Phase II reroute**: after N failed local patches, drop the
  previous proof from context entirely and demand a "materially
  different proof strategy". Anti-anchoring.
- **Anti-handwave**: explicit ban on "clearly / obviously / it is
  easy to see / by a standard argument" — those phrases are signals
  the agent is dodging the hard part.
- **Computational verification**: where feasible, the generator should
  verify intermediate claims with SymPy / NumPy / Z3 and save the
  scripts alongside the output.
- **Counterexample-first**: for any to-be-proved claim, actively
  search for counterexamples before attempting a proof. Failed
  counterexample attempts often reveal the proof strategy.

### "Additional rules" sidecar (planned, not yet built)

QED's pattern: every verifier reads
`additional_verify_rule_global.md` as Phase 5 — project-specific
constraints (e.g. "`Fun(-,-)` defaults to enriched in this paper",
"do not cite paper X") get injected at runtime without editing the
base prompt. We will adopt this via a `KbAdapter.read_project_rules(role)`
method that loads `docs/knowledge/rules/{role}.md` if present, and
`compose()` appends it to the prompt.

## Backend Isolation Constraint

The following pairs of agents **must use different LLM backends** in
production (avoids same-source bias):

- `proof-gap-filler` ≠ `proof-verifier`
- `proof-gap-filler` ≠ `source-claim-verifier`

| Mode | Enforcement |
|---|---|
| Mode B | Hard-fail at startup in `rethlas_kb/config.py` (issue #13). Override: `--allow-same-backend`. |
| Mode A | Convention only: the user chooses which agentic CLI to invoke for each `/verb`. Slash-command file names and docs flag the recommended pairing. |

## QED-Style Difficulty-Adaptive Verification

`proof-verifier` and `source-claim-verifier` each implement an internal
3-stage pipeline:

```text
judge(cheap)
  ├─ if easy:  one-call full verification → done
  └─ if hard:  structural(medium)
                ├─ if FAIL:  short-circuit, no detailed run
                └─ if PASS:  detailed(deep) → done
```

Each stage's prompt scopes itself explicitly:

- **judge** (`prompt_judge.md` in QED) — classifies difficulty and,
  for easy items, does the full verification in one shot.
- **structural** — high-level architecture checks ONLY (no
  step-by-step). Explicitly: "Do NOT verify whether individual
  logical steps are mathematically correct — that is the
  responsibility of the detailed verifier."
- **detailed** — step-by-step correctness, but **inherits and trusts**
  the structural report. It does not re-check structural claims.

Mode B CLI exposes `--depth {auto|easy|structural|detailed}`.
Admission credentials require `--depth detailed` passes.
`structural` is for the creative phase; `detailed` is for the
convergence phase.

In Mode A the agentic CLI decides escalation itself — the slash
command tells it "run judge first; if hard, escalate via
`rethlas-kb compose-prompt proof-verifier-structural …` etc."

Pattern reference: [QED](https://arxiv.org/abs/2604.24021)
`verify/verify.py` + `verify/prompt_{judge,verify_structural,verify_detailed}.md`.

## Forbidden / Be Careful

- Do not write to `nodes/` directly; mdblueprint admission flow only.
- Do not silently degrade on backend errors — surface them.
- Do not bypass `validate_backend_isolation` without a clearly logged
  `--allow-same-backend` reason.
- Do not import `mdblueprint` internals beyond what `KbAdapter`
  already enumerates at the top of `adapter.py`; if something is
  missing, add it to `KbAdapter` rather than importing in three
  places.
- Do not commit `~/mycodes/mdblueprint` into this repo; it is an
  editable dependency, not a submodule and not vendored.
- Do not mix **verifier discipline** with **generator discipline** when
  writing a new agent's prompt — they have opposite conservatism stances.
  Verifiers flag under uncertainty; generators try anyway.

## Reference Reading

- Roadmap + issue mapping: [ROADMAP.md](ROADMAP.md)
- mdblueprint architecture: `~/mycodes/mdblueprint/docs/architecture.md`
- mdblueprint agent contracts: `~/mycodes/mdblueprint/docs/agent-contracts.md`
- QED verification pipeline: `~/mycodes/QED/verify/verify.py` +
  `~/mycodes/QED/verify/prompt_*.md` + `~/mycodes/QED/skill/super_math_skill.md`
- Rethlas-original agent prompts:
  `~/mycodes/Rethlas/{generator,verifier,referee,learner}/prompt.py`
- Test fixture KB: `~/mydoc/sheavesonbuilding/` (Ma-Wang-Yu in-progress paper)
- Prompt discipline lessons applied to statement-verifier:
  see commit `a2844de` message and module docstring of
  `rethlas_kb_agents/statement_verifier/prompt.py`
