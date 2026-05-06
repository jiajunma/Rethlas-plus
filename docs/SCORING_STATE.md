# SCORING_STATE — 当前状态地图(2026-05-06)

> 一站式 maintainer 视图:每个 sprint 落地了什么、哪些是真生效、哪些是
> scaffold、哪些还没动。对照阅读 `SCORING_DESIGN.md`(数学规格)、
> `SCORING_SCHEDULING.md`(分层调度)、`SCORING_INTEGRATION.md`(集成路径)。

---

## 1. Sprint 状态总表

| Sprint | 范围 | 状态 | Commit | 真生效? |
|---|---|---|---|---|
| **S1** | tier-strict BFS dispatcher | ✅ landed | `9c36045` | ✅ 是(`use_voi_scoring=true` 时) |
| **S2** | `Evidence` + `classify` + `next_action` 纯函数状态机 | ✅ landed | `1da747b` | ✅ 是(被 S3 wire 调用) |
| **S3** | coordinator 调 policy `_action_for_candidate` scaffold | ✅ landed | `4623956` | ⚠️ 仅 log,Action≠DEFAULT_VERIFY 时不实派(KB schema 不支持 ledger) |
| **Hypothesis** | I1–I4 + 状态机 property tests | ✅ landed | `34bf0b2` | ✅ 测试时跑 |
| **S4-light** | `refute/` 包 prompt + decoder 原语 | ✅ landed | `a9d0076` | ⚠️ 模块独立可用,但 coordinator 没接 refute role 派工 |
| **S5** | PolicyBudget 配置 + strong_verifier_model_id | ✅ landed | `f4fe72c` | ✅ budget 进 dispatch,但 strong role 二进制 deferred |
| **S6-A** | EmbeddingProvider Protocol + `HashEmbeddingProvider` | ✅ landed | `dc66a30` | ✅ 是 |
| **S6-B** | `_build_priority_fn` 接 hash provider | ✅ landed | `42ed4df` | ✅ 是,cluster_susp 真有值 |
| **S6-C** | factory + `OpenAIEmbeddingProvider`(lazy)+ env override | ✅ landed | `e25ffce` | ✅ 是,`OPENAI_API_KEY` 设了就自动升级 |
| **S6-D** | `CachingEmbeddingProvider` LRU 包装 | ✅ landed | `d9fe961` | ✅ 是,跨 tick 复用 |
| **S7-math** | Dawid-Skene aggregator property tests | ✅ landed | `d993097` | ✅ 测试时跑;production aggregate 等 S7-full 接 ensemble dispatch |
| **S4-full** | refute role 二进制 + KB events + projector apply | 🔲 not started | — | 需 KB schema 改 |
| **S5-full** | strong verifier role 二进制 + dispatch 真派 | 🔲 not started | — | 半天 |
| **S7-full** | ensemble k=3 dispatch + Dawid-Skene 入库 | 🔲 not started | — | 需 `common/runtime/jobs.py` 改 |
| **S8** | certifying-set 终止 | 🔲 not started | — | 依赖 S7-full |

---

## 2. 用户可调的 toggles(rethlas.toml)

```toml
[scheduling]
use_voi_scoring = false                 # 默认 OFF;ON 时启用整套 VOI 路径
policy_max_refute_per_node = 1          # L5 升迁阶梯 refute 上限
policy_max_strong_per_node = 1          # L5 升迁阶梯 strong 上限
strong_verifier_model_id = ""           # S5-full 真派 strong 时的模型 ID
```

环境变量:

```bash
export RETHLAS_EMBEDDING_PROVIDER=hash    # 或 "openai"(强制),不设则 auto-detect
export OPENAI_API_KEY=sk-...              # 设了 + 装了 openai SDK ⇒ 自动用 OpenAI
```

---

## 3. 真生效 vs scaffold 一目了然

### 真生效(默认就有用):

- **S1 BFS-by-tier**:`coordinator/dispatcher.py::select_verifier_targets` 不论 toggle 都按 `(pass_count asc, label asc)` 走;`use_voi_scoring=true` 时 priority_fn 在每 tier 内排序,严格保证低 tier 先满
- **S6-B/C/D embedding pipeline**:`use_voi_scoring=true` 时,候选 statement 经 hash(或 OpenAI)provider 嵌入 → cluster_susp 真算非零值 → Pareto 前沿反映 cluster 信号;process-wide LRU 缓存避免重复计算
- **PolicyBudget 配置**:`policy_max_refute_per_node` / `policy_max_strong_per_node` 真进 `_action_for_candidate`

### Scaffold(代码就位但当前不实际改变行为):

- **S2 + S3 policy state machine**:在 dispatch 路径上,但 `_evidence_from_candidate` 只能从 `pass_count` 重构(KB 不存 Evidence ledger),故所有候选都判 PENDING → DEFAULT_VERIFY,等同既有行为
- **S4-light refute primitives**:`refute/{prompt,decoder}.py` 可单独使用,但 coordinator 没接 refute role 派工(`_action_for_candidate` 不会返回 REFUTE 因为 policy 看不到 critical evidence)
- **S5 strong_verifier_model_id**:配置字段已读,但 dispatch 不会真派 strong worker
- **S7-math aggregate**:Dawid-Skene 函数 + property tests OK,但 ensemble dispatch 没接 → production 路径不调用

### 完全没改的:

- KB schema(Kuzu)
- `verifier/role.py` —— 仍单调用、不输出 confidence、无 ground-truth 反馈
- `librarian/projector.py` —— 没接 cluster propagation,没收 refute / strong / ensemble events

---

## 4. 测试覆盖度

| 范围 | 文件数 | 用例数 | 备注 |
|---|---|---|---|
| `tests/scoring/` | 18 | ~330 | 包含 hypothesis property + 集成测试 |
| `tests/unit/` 旧有 | 33 | ~140 | 没改,仍全绿 |
| **总计** | 51 | **492** | 3.7 s 全跑完 |

排除:`tests/unit/test_m6_prompt.py`(`agents/generation/mcp/__init__.py` 先前 import 错误,不在本分支责任范围)。

---

## 5. 接下来三件最有价值的事(按 ROI 排)

1. **S7-full ensemble dispatch**(1.5 天)—— 直接削减 H11 假阳性。需要 `common/runtime/jobs.py` 支持单 target 多 worker;coordinator 派 k=3 verifier 后 Dawid-Skene 聚合再入 KB。这一步落地后 S2/S3/S4-full 全部"活"过来,因为 ensemble 内部分歧会产生 critical evidence → DISAGREEMENT → policy 走 REFUTE → STRONG_VERIFY → user_blocked
2. **S4-full refute role + KB events**(1.5 天)—— 需要新 KB event 类型 + projector apply + refute pool capacity。ensemble 之后做这条最自然
3. **S6-D cache 持久化到 KB / 磁盘**(1 天)—— 当前 process-wide cache 跨重启失效;若 OpenAI 路径常用,值得持久化到 ``runtime/embeddings.sqlite`` 或类似

---

## 6. 已知待办(非阻塞)

- `tests/unit/test_m6_prompt.py` collection error 是 `agents/generation/mcp/__init__.py` 引入了 `verify_proof_service` 但 `server.py` 没导出该符号 —— 跟本分支无关,需单独 audit
- `coordinator/main.py` 对 `_default_embedding_provider` 与 `make_priority_fn` 的实例都未做 lifecycle / shutdown 管理,长跑下 OpenAI HTTP 连接池没清理路径(目前 process 退出时 GC 即可)

---

## 7. 回滚级别

| 级别 | 操作 | 影响 |
|---|---|---|
| **配置** | `use_voi_scoring=false` in rethlas.toml | 立即回到 BFS-by-tier 原排序,无 cluster / policy 干预 |
| **环境** | `unset OPENAI_API_KEY` 或 `RETHLAS_EMBEDDING_PROVIDER=hash` | 回到零依赖 hash embedding |
| **代码** | `git revert <commit>` 任一 sprint commit | 各 sprint 可独立 revert,无相互依赖 |
| **完全** | `git revert --no-commit 9c36045..HEAD` | 回到 S1 之前的 dispatcher,只做基础 BFS |

每次回滚后 365/365 旧测试仍应通过(scoring/ 目录被回滚部分会随之消失)。
