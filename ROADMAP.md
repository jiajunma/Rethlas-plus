# Rethlas-KB Roadmap

Rethlas-KB 是 Rethlas-plus 的重新设计 —— **作为 agent 层工作在 mdblueprint
KB 上**,不再持续运行的 daemon + Kuzu DB,而是 batch CLI 对 markdown 节点
做证明、验证、反例搜索的 codex/claude/opencode 多 backend 编排器。

> 设计决策来源:对话 archive(2026-05-25)的 Path D 路径 + QED 风格
> difficulty-adaptive 验证 + multi-backend 隔离 + 研究数学(非形式化已有)
> 工作场景。

## ✅ v1 — Walking skeleton + 5 agents — **SHIPPED 2026-05-25**

12 issues closed. 352 tests passing. `rethlas-kb install-commands`
+ Mode A slash commands for claude / codex / opencode.

目标已达成:**在 `~/mydoc/sheavesonbuilding` 真实研究节点上,通过 CLI 跑通**
verify-stmt / verify-proof / fill-gap / hunt-counterexample / audit-source
五个核心命令,输出 mdblueprint 格式的 review 文件。同时 Mode A
(agentic CLI 编排) + Mode B (Python 编排) 双轨。

| # | Title | Status |
|---|---|---|
| 1 | scaffold rethlas-kb uv workspace + README + AGENTS.md | ✅ closed |
| 2 | pyproject + mdblueprint editable source | ✅ closed |
| 3 | backends: AgentBackend Protocol + registry + factory | ✅ closed |
| 4 | backends: codex wrapper (port codex_runner) | ✅ closed |
| 5 | backends: claude wrapper | ✅ closed |
| 6 | adapter: mdblueprint KB read/write | ✅ closed |
| 7 | agent: statement-verifier (hardened with QED discipline) | ✅ closed |
| 8 | CLI: rethlas-kb verify-stmt + sheavesonbuilding smoke | ✅ closed |
| 9 | agent: proof-verifier (QED-style 3-stage + --depth) | ✅ closed |
| 10 | agent: proof-gap-filler (generator discipline) | ✅ closed |
| 11 | agent: counterexample-hunter (inverse search) | ✅ closed |
| 12 | agent: source-claim-verifier (PDF extractor deferred — see notes) | ✅ closed |
| 13 | config: cross-backend isolation library hook | ✅ closed |
| 19 | CLI: primitives for Mode A orchestration | ✅ closed |
| 20 | commands: slash templates + install-commands | ✅ closed |
| 21 | docs: pivot README + AGENTS.md to Mode A primary path | ✅ closed |
| 22 | adapter: project-rules sidecar | ✅ closed |

### v1 architectural pivots (mid-flight)

- **Mode A primary, Mode B batch/CI** (after design discussion):
  rethlas-kb became a toolkit for agentic CLIs (codex / claude /
  opencode) to call, not a Python orchestrator that treats LLMs as
  inference services. Mode B remains for batch / CI / determinism.
  See `AGENTS.md` "Two Orchestration Modes".
- **Generator vs Verifier taxonomy** (after studying QED + Rethlas-original):
  the 5 agents split into two categories with opposing discipline.
  Verifiers are scope-restricted + conservative-by-default + anti-paraphrase.
  Generators are repair-aware + anti-handwave + counterexample-first.
- **QED-learned prompt discipline** baked into every verifier prompt:
  conservative stance, verbatim-quote discipline, anti-pattern catalog,
  discriminated-union outputs (decoder enforces), context_insufficient
  escape hatch, confidence calibration bands.

### v1 deferrals (intentional)

- **PDF extractor in source-claim-verifier** — v1 takes pre-extracted
  source passages as input; deterministic extractor deferred to v1.5+
- **ProofVerifier chaining inside source-claim-verifier** — v1 emits one
  combined verdict; for deeper checking run `verify-proof` separately
  on a staged copy
- **Cross-backend isolation enforcement** — library hook ships in
  `rethlas_kb/config.py` for v1; startup enforcement waits for v1.3's
  config file (issues #14/#15) since today there's no single CLI
  invocation that activates both roles in a constrained pair

## v1.3 — Project 概念

| # | Title | Track |
|---|---|---|
| 14 | project: manifest schema + closure computation | v1.3 |
| 15 | project: --project flag on all CLI commands | v1.3 |

## v1.5 — Literature scout + 维护

| # | Title | Track |
|---|---|---|
| 16 | literature: arXiv MCP integration | v1.5 |
| 17 | agent: literature-scout | v1.5 |
| 18 | maintenance: refresh-sources(arxiv version drift detection) | v1.5 |

## Beyond v1.5

- admission-referee agent
- revoke command(带 counterexample/source-retraction 触发的撤销)
- opencode backend wrapper
- lemma-refactor / statement-generalizer / sketch-expander agents

## 核心架构契约

### 5 个 v1 agents 跨 backend 隔离约束

| Agent | Backend 默认 | 必须跟谁不同源 |
|---|---|---|
| statement-verifier | claude | — |
| proof-gap-filler | claude opus-thinking | proof-verifier, source-claim-verifier |
| proof-verifier | codex gpt-5.5-pro / o3-mini | proof-gap-filler |
| counterexample-hunter | claude opus-thinking | — |
| source-claim-verifier | codex(detailed)/ claude(structural) | proof-gap-filler |

约束位于 `rethlas_kb/config.py::validate_backend_isolation()` —
Mode B 通过 issue #14/#15 引入 config 文件时挂钩启动校验。Mode A
靠用户约定(选哪个 agentic CLI 跑哪个 /verb),工具不强制 —
Mode A 的价值就来自 agentic CLI 的行动自由。

### QED 风格 difficulty-adaptive 验证

`proof-verifier` 和 `source-claim-verifier` **不是单 LLM 调用**,是内部 pipeline:

```
judge(轻) → if easy: 一次完整验证 done
             if hard: structural(中)→ if PASS: detailed(深)
                                     → if FAIL: 短路
```

CLI 出 `--depth {auto|easy|structural|detailed}` 参数。
admission 凭据**仅** `--depth detailed` 通过的 review 算数。

### 仓库布局

```
/Users/hoxide/mycodes/rethlas-kb/          (本 worktree, branch rethlas-kb)
├── pyproject.toml                          uv workspace, dep on mdblueprint
├── README.md
├── AGENTS.md                               mdblueprint AGENTS.md 风格
├── CLAUDE.md                               -> AGENTS.md shim
├── ROADMAP.md                              本文件
│
├── rethlas_kb/                             主 package
│   ├── __init__.py
│   ├── cli.py
│   ├── adapter.py                          mdblueprint KB I/O
│   ├── config.py                           backend / agent config
│   └── backends/
│       ├── __init__.py
│       ├── base.py                         Protocol + registry
│       ├── codex.py
│       └── claude.py
│
├── rethlas_kb_agents/                      LLM agent 实现
│   ├── statement_verifier/{role,prompt,decoder}.py
│   ├── proof_verifier/{role,prompt_judge,prompt_structural,prompt_detailed,decoder}.py
│   ├── proof_gap_filler/{role,prompt,decoder}.py
│   ├── counterexample_hunter/{role,prompt,decoder}.py
│   └── source_claim_verifier/
│       ├── role.py
│       ├── pdf_extractor.py                确定性 PDF 段落抽取
│       ├── prompt_alignment.py
│       ├── prompt_structural.py            (复用 proof_verifier?)
│       ├── prompt_detailed.py              (复用 proof_verifier?)
│       └── decoder.py
│
└── tests/
    ├── adapter/                            mdblueprint I/O 集成
    ├── agents/                             单 agent role 单测
    └── backends/                           mock backend 单测
```

### mdblueprint 依赖路径

`pyproject.toml` 用:

```toml
[tool.uv.sources]
mdblueprint = { path = "/Users/hoxide/mycodes/mdblueprint", editable = true }
```

Solo dev OK。CI / share 阶段未来要 vendoring or publish to PyPI。

### sheavesonbuilding 作 walking skeleton 真实测试场景

不用 EconCSLib(那是形式化已有教科书结果,与 Rethlas-KB 目标不一致)。
用 `~/mydoc/sheavesonbuilding`(Ma-Wang-Yu 论文 in progress)的 lemma /
proposition 作 staged 节点测试。
