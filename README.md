# Rethlas-KB

**Rethlas-KB** is the agent layer that operates on top of an
[mdblueprint](https://github.com/jiajunma/mdblueprint) knowledge base
to do **new research mathematics** — propose lemmas, verify proofs,
hunt counterexamples, audit cited papers.

It is a redesign of Rethlas-plus. The previous Kuzu-backed daemon
architecture is replaced by **batch CLI commands** that invoke
**multi-backend LLM agents** (codex / claude / opencode) on a
markdown-first knowledge base.

> **Status**: v1 design frozen 2026-05-25; implementation in progress.
> See [ROADMAP.md](ROADMAP.md) for the full plan and
> [GitHub issues](https://github.com/jiajunma/Rethlas-plus/issues?q=label%3Arethlas-kb)
> for current work.

---

## What you can do (v1)

```bash
rethlas-kb verify-stmt  <node-id>              # statement well-formed?
rethlas-kb verify-proof <node-id>              # QED-style 3-stage (judge → structural → detailed)
rethlas-kb verify-proof <node-id> --depth structural   # shallow only (creative phase)
rethlas-kb verify-proof <node-id> --depth detailed     # deep verification (convergence phase)
rethlas-kb fill-gap     <node-id>              # complete a partial proof
rethlas-kb hunt-counterexample <node-id>       # actively try to refute the statement
rethlas-kb audit-source <node-id>              # verify a cited external paper (alignment + math)
```

Every command writes its output as a `reviews/*.md` file into the
mdblueprint KB. Admission to `nodes/` follows mdblueprint's standard
flow.

---

## Architecture in one paragraph

mdblueprint owns the **knowledge layer** — markdown nodes under
`docs/knowledge/{nodes,staged,reviews,requests,sources}/`. Rethlas-KB
owns the **agent layer** — Python orchestrators that compose prompts,
invoke an LLM backend via subprocess (codex / claude / opencode CLI),
parse the result, and write a review file. There is **no DB**, **no
daemon**, **no event bus** — every command is a one-shot CLI
invocation that reads + writes markdown.

Verification uses the **QED-style difficulty-adaptive pipeline**:
a cheap *judge* classifies the proof as Easy or Hard; Easy gets a
one-call verification, Hard goes through a *structural* check (high-
level architecture) that gates a *detailed* step-by-step check (deep
math). Cheap-first, gate-after. See [QED](https://arxiv.org/abs/2604.24021)
for the original pattern.

The differentiator for **research mathematics** (vs formalizing
known textbook results): a first-class `counterexample-hunter` agent
because in research, statements really might be wrong, and finding a
concrete witness is a primary deliverable, not a secondary signal.

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

- [`ROADMAP.md`](ROADMAP.md) — 18-issue v1 / v1.3 / v1.5 plan
- [`AGENTS.md`](AGENTS.md) — agent contracts (mdblueprint-style)
- [`CLAUDE.md`](CLAUDE.md) — Claude Code shim → AGENTS.md
- [`docs/`](docs/) — design notes (forthcoming)

---

## Install (forthcoming)

```bash
git clone git@github.com:jiajunma/Rethlas-plus.git -b rethlas-kb rethlas-kb
cd rethlas-kb
uv sync
```

mdblueprint must be available at `~/mycodes/mdblueprint` (editable
local dep). See [`pyproject.toml`](pyproject.toml) `tool.uv.sources`
section.

At least one of `codex` / `claude` / `opencode` CLIs must be
installed and authenticated. See backend wrappers in
`rethlas_kb/backends/` for per-backend setup.
