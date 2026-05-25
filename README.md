# Rethlas-KB

**Rethlas-KB** is the agent layer that operates on top of an
[mdblueprint](https://github.com/jiajunma/mdblueprint) knowledge base
to do **new research mathematics** — propose lemmas, verify proofs,
hunt counterexamples, audit cited papers.

It's a toolkit that agentic CLIs (codex / claude / opencode) call as
**tools** while *they* do the orchestration. A Python end-to-end
orchestration path is also available for batch / CI use.

> **Status**: v1 shipped 2026-05-25. 5 agents, 2 backends (codex + claude),
> 352 passing tests. See [ROADMAP.md](ROADMAP.md) and the
> [closed issues](https://github.com/jiajunma/Rethlas-plus/issues?q=label%3Arethlas-kb+is%3Aclosed).

---

## Quick start

```bash
# Install
git clone git@github.com:jiajunma/Rethlas-plus.git -b rethlas-kb rethlas-kb
cd rethlas-kb
uv sync --extra dev

# Install slash commands into your blueprint's project dir
cd ~/mydoc/sheavesonbuilding
rethlas-kb install-commands --scope project

# Mode A: invoke an agent from inside claude / codex / opencode
claude
> /verify-stmt cellular_categories.sheaves_cosheaves
> /verify-proof-judge equivariant_sheaves.qfd_orbit_lemma
> /verify-proof-structural equivariant_sheaves.qfd_orbit_lemma   # if hard
> /verify-proof-detailed equivariant_sheaves.qfd_orbit_lemma     # if structural passes
> /fill-gap algebra.lagrange
> /hunt-counterexample algebra.suspicious_claim
> /audit-source references.lagrange_external
```

The agentic CLI fetches the prompt with `rethlas-kb compose-prompt
<role> <node-id>`, reasons (using its full tool box — Read, Grep,
WebFetch, SymPy via Bash, etc.), then persists with `rethlas-kb
write-review <node-id> --agent <role> --decision <X> --rationale
"..." --raw -`.

---

## Two modes of use

| | **Mode A — agentic** *(primary)* | **Mode B — Python** *(batch/CI)* |
|---|---|---|
| Orchestrator | codex / claude / opencode itself | Python (`StatementVerifier(backend).run(...)`) |
| Entry point | `claude /verify-stmt <id>` | `rethlas-kb verify-stmt <id> --backend codex` |
| How LLM is reached | The agentic CLI IS the LLM; calls `rethlas-kb` primitives as tools | Python shells out to `codex exec` / `claude -p` as subprocess |
| Decoder needed | No — LLM calls `rethlas-kb write-review` directly | Yes — Python parses JSON-from-prose |
| Use when | Interactive research; agent needs to reason / search / cite / loop | Reproducible scripted runs; CI; benchmark grids |

**Rule of thumb**: when in doubt, use Mode A. Mode B exists so
results are reproducible, not because it's the better path.

---

## What you can do (v1)

### Verifiers (judge existing content)

```bash
rethlas-kb verify-stmt  <node-id>               # statement well-formed?
rethlas-kb verify-proof <node-id>               # QED-style 3-stage
rethlas-kb verify-proof <node-id> --depth easy        # judge only
rethlas-kb verify-proof <node-id> --depth structural  # skip judge
rethlas-kb verify-proof <node-id> --depth detailed    # skip judge + structural
rethlas-kb audit-source <node-id> --source-passage extract.md
```

### Generators (produce new content)

```bash
rethlas-kb fill-gap     <node-id> --prior-review <verifier-output.md>
rethlas-kb hunt-counterexample <node-id>        # actively try to refute
```

### Primitives (Mode A toolkit — agentic CLIs call these)

```bash
rethlas-kb get-node     <node-id>               # raw markdown / json / frontmatter
rethlas-kb get-context  <node-id>               # dependency closure
rethlas-kb compose-prompt <role> <node-id>      # ready-to-send LLM prompt
rethlas-kb list-staged   [--topic T]
rethlas-kb list-admitted [--topic T]
rethlas-kb write-review  <node-id> --agent X --decision Y --rationale Z --raw -
rethlas-kb write-request <node-id> --kind K --payload -
rethlas-kb write-staged-node       --from-file -
rethlas-kb update-staged-node-body <node-id> --from-file -
rethlas-kb validate-frontmatter    --from-file -
rethlas-kb install-commands [--target claude|codex|opencode|all]
                            [--scope user|project]
```

Every command writes its output as a `reviews/*.md` /
`requests/*.md` / `staged/*.md` file in the mdblueprint KB.
Admission to `nodes/` follows mdblueprint's standard flow.

---

## Agent set (v1)

The five v1 agents split into two abstract categories with opposing
discipline (see [AGENTS.md](AGENTS.md) for full taxonomy).

### Verifiers — conservative, scope-restricted, anti-paraphrase

| Agent | Decisions |
|---|---|
| `statement-verifier` | accepted / needs_definition / generality_concern / formulation_issue / context_insufficient |
| `proof-verifier` (3-stage) | easy/hard (judge) → pass/fail (structural) → accepted/gap/critical/uncertain (detailed) |
| `source-claim-verifier` | accepted / mismatch / proof_gap / proof_critical / cannot_verify |

Verifier prompts apply QED-learned discipline: **conservative
stance** (under uncertainty, flag), **verbatim-quote** (output
requires literal copy of judged text to prevent
paraphrase-then-agree), **anti-pattern catalog** (explicit defects
to scan for), **discriminated-union outputs** (every non-accepted
decision needs its companion field — decoder enforces).

### Generators — repair-aware, anti-handwave, counterexample-first

| Agent | Decisions |
|---|---|
| `proof-gap-filler` | filled / partial / cannot_fill |
| `counterexample-hunter` | counterexample_found / no_counterexample_found / inconclusive |

Generator prompts ported from Rethlas-original + QED's
super_math_skill: **repair-aware** (consumes prior verifier
report), **Phase II reroute** (after `repair_count >= 2`, drop
previous proof from context to prevent anchoring),
**counterexample-first** (refute before proving;
verify-computationally before believing), **anti-handwave** (banned
phrases: "clearly", "obviously", "WLOG without justification",
"similarly").

---

## Architecture in one paragraph

mdblueprint owns the **knowledge layer** — markdown nodes under
`docs/knowledge/{nodes,staged,reviews,requests,sources,rules}/`,
deterministic Python validation, static-site publishing. Rethlas-KB
owns the **agent layer** — a uniform `rethlas-kb` CLI that exposes
primitives (`get-node` / `compose-prompt` / `write-review`) and
end-to-end Python workflows (`verify-stmt` / `verify-proof` /
`fill-gap` / `hunt-counterexample` / `audit-source`). All KB I/O
goes through one file: `rethlas_kb/adapter.py::KbAdapter`.

Verifier prompts apply QED's difficulty-adaptive 3-stage pattern:
`judge` (cheap) → `structural` (medium, high-level checks only) →
`detailed` (deep, step-by-step, trusts structural). Each stage's
prompt explicitly says what it is *not* responsible for. Pipelines
short-circuit at `judge=easy` or `structural=fail` so cheap work
doesn't burn deep-reasoning tokens.

There is **no DB**, **no daemon**, **no event bus** — every
command is a one-shot CLI invocation that reads + writes markdown.

---

## Project-specific rules (issue #22)

QED-style sidecar: drop a markdown file at
`docs/knowledge/rules/_global.md` (applies to every agent) or
`docs/knowledge/rules/<role>.md` (applies to one agent role). Every
agent's prompt automatically appends those rules as hard
requirements — no need to fork the base prompts.

Example for sheavesonbuilding:

```markdown
<!-- docs/knowledge/rules/_global.md -->
- `Fun(-,-)` defaults to **enriched functors** in this paper unless
  context explicitly says underlying.
- Citations to Fintzen–Yu refer to the published version with the
  May 2025 correction.
```

```markdown
<!-- docs/knowledge/rules/proof-verifier.md -->
- All inequality bounds must state whether strict or non-strict
  explicitly. "≤" without justification is a gap.
```

---

## Use case

Designed against `~/mydoc/sheavesonbuilding` ("Sheaves on Buildings
and Representations of p-adic Reductive Groups", Ma-Wang-Yu, in
progress) and similar **work-in-progress research papers** with
partial proofs, uncertain statements, citations into recent arXiv
preprints. NOT designed for formalizing already-proven textbook
results (use mdblueprint's other agent contracts for that).

---

## Documents

- [`AGENTS.md`](AGENTS.md) — agent contracts, generator/verifier
  taxonomy, prompt discipline patterns
- [`ROADMAP.md`](ROADMAP.md) — v1 (shipped) / v1.3 / v1.5 plan
- [`CLAUDE.md`](CLAUDE.md) — Claude Code shim → AGENTS.md
- [`rethlas_kb/_commands/`](rethlas_kb/_commands/) — slash command
  templates installed by `rethlas-kb install-commands`

---

## Install

```bash
git clone git@github.com:jiajunma/Rethlas-plus.git -b rethlas-kb rethlas-kb
cd rethlas-kb
uv sync --extra dev

# Run the test suite
uv run pytest -q
# 352 passing, 1 deselected (real-LLM smoke test, opt in with -m slow)
```

**Dependencies:**

- mdblueprint must be available at `~/mycodes/mdblueprint` as an
  editable local source (see [`pyproject.toml`](pyproject.toml)
  `tool.uv.sources`). For a different location, edit the path.
- At least one of `codex` / `claude` / `opencode` CLIs must be
  installed and authenticated. Per-backend setup is in the
  module docstrings under `rethlas_kb/backends/`.

---

## What's NOT in v1

Deferred to **v1.3** (project manifest concept):
- Project closure computation across the dependency graph
- Per-project agent ↔ backend pinning config file (will hook
  `rethlas_kb/config.py::validate_backend_isolation` at startup)

Deferred to **v1.5** (literature scout):
- arXiv MCP integration
- `literature-scout` agent (statement-staging from papers)
- `refresh-sources` for arXiv version drift

Deferred to **future** (within source-claim-verifier scope):
- Deterministic PDF extractor (currently v1 requires
  pre-extracted source passage as input)
- ProofVerifier chaining inside source-claim-verifier (currently
  v1 emits one combined verdict; for deeper source-proof checking,
  run `verify-proof` separately on a staged copy)

See [ROADMAP.md](ROADMAP.md) for the issue tracker mapping.

---

## Tests

```bash
uv run pytest -q           # default — slow tests skipped
uv run pytest -m slow      # real-LLM smoke against ~/mydoc/sheavesonbuilding
                           # (auto-skips if codex CLI or sheaves repo missing)
```

352 unit tests cover every decoder discriminated-union case, prompt
section visibility, pipeline short-circuit semantics, adapter
behaviour, and CLI plumbing. The smoke test (`tests/integration/`)
exercises the full Mode B round-trip against a real research node
with a real codex CLI; it converts transient upstream failures
(rate limits, 5xx) to `pytest.skip` so a flaky LLM provider doesn't
break the local loop.
