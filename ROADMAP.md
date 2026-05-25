# Rethlas-KB Roadmap

Rethlas-KB 是 Rethlas-plus 的重新设计 —— **作为 agent 层工作在 mdblueprint
KB 上**,不再持续运行的 daemon + Kuzu DB,而是 batch CLI 对 markdown 节点
做证明、验证、反例搜索的 codex/claude/opencode 多 backend 编排器。

> 设计决策来源:对话 archive(2026-05-25)的 Path D 路径 + QED 风格
> difficulty-adaptive 验证 + multi-backend 隔离 + 研究数学(非形式化已有)
> 工作场景。

## v1 — Walking skeleton + 5 agents(本次工作)

目标:**在 `~/mydoc/sheavesonbuilding` 真实研究节点上,通过 CLI 跑通**
verify-stmt / verify-proof / fill-gap / hunt-counterexample / audit-source
五个核心命令,输出 mdblueprint 格式的 review 文件。

| # | Title | Track |
|---|---|---|
| 1 | scaffold rethlas-kb uv workspace + README + AGENTS.md | v1 |
| 2 | pyproject + mdblueprint editable source | v1 |
| 3 | backends: AgentBackend Protocol + registry + factory | v1 |
| 4 | backends: codex wrapper(port codex_runner) | v1 |
| 5 | backends: claude wrapper | v1 |
| 6 | adapter: mdblueprint KB read/write | v1 |
| 7 | agent: statement-verifier | v1 |
| 8 | CLI: rethlas-kb verify-stmt + sheavesonbuilding smoke | v1 |
| 9 | agent: proof-verifier(QED-style 3-stage + --depth) | v1 |
| 10 | agent: proof-gap-filler | v1 |
| 11 | agent: counterexample-hunter | v1 |
| 12 | agent: source-claim-verifier(PDF extractor + alignment + verify) | v1 |
| 13 | config: cross-backend constraint enforcement | v1 |

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

约束在 config 校验阶段强制(commit 13);违反 → 启动报错,不静默。

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
