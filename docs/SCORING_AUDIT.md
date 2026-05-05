# SCORING_AUDIT — H1–H11 现状定位报告

> 配套 `SCORING_HANDOFF.md` 第 "已诊断的漏洞" 节,把每条漏洞钉到当前代码位置或注明"尚未存在,属预防性"。
> 时间锚:基于 commit `c677636`(branch `claude/youthful-goldberg-7369a8`)的 worktree。

---

## 0. 执行摘要

**HANDOFF 假设的"原打分公式" `score(v) = α·crit + β·tract + γ·bridge − δ·cost − ε·stale` 在当前代码里不存在。**

当前调度是 **two-pool, lexicographic sort**,完全无打分:

- Generator pool — 按 `label` 字典序(`coordinator/dispatcher.py:42-58`)
- Verifier pool — 按 `(pass_count asc, label asc)`(`coordinator/dispatcher.py:61-86`)

因此:

- **H1 / H2 / H3 / H7 / H8** —— **目前无代码 locus**,属"如果朝这个方向加打分会踩到的坑"。在 `rethlas_scoring/` 落地时**绕过**它们。
- **H4 / H5 / H6 / H10** —— 部分功能本就**还没实现**(cluster 传播 / refute task / bridge 合并),也是新模块要补的部分。
- **H9 / H11** —— **当前代码已经踩中**。LLM verifier 是单调用、零校准。

下文按这三类组织。

---

## 1. 现状调度的代码地图(给后面引用)

| 角色 | 文件 | 关键行 | 作用 |
|------|------|--------|------|
| 候选池构造(generator) | [coordinator/main.py](../coordinator/main.py) | 820–832 | 选 `pass_count == -1` 且依赖就绪的节点 |
| 候选池构造(verifier) | [coordinator/main.py](../coordinator/main.py) | 833–851 | 选 `0 ≤ pass_count < desired` 且 `verifier_deps_strictly_ahead` |
| 容量计算 | [coordinator/main.py](../coordinator/main.py) | 853–862 | 按配置 `generator_workers` / `verifier_workers` 减去在飞 |
| 排序 + 截断(generator) | [coordinator/dispatcher.py](../coordinator/dispatcher.py) | 42–58 | `sorted({c.label})`,FIFO by label |
| 排序 + 截断(verifier) | [coordinator/dispatcher.py](../coordinator/dispatcher.py) | 61–86 | `sorted(by_label.items(), key=(pass_count, label))` |
| dispatcher 调用 | [coordinator/main.py](../coordinator/main.py) | 863–868 | 唯一入口,集成点就在这里 |
| Node 数据模型 | [common/kb/types.py](../common/kb/types.py) | 71–102 | `frozen=True, slots=True`;没有 `posterior_p/embedding/refute_severity` |
| Verifier 输出解码 | [verifier/decoder.py](../verifier/decoder.py) | 170–215 | 只产出 `accepted/gap/critical`,无 confidence,无 ensemble |
| Verifier role | [verifier/role.py](../verifier/role.py) | 全文 | 单 Codex 调用,无 k>1 |
| Generator role | [generator/role.py](../generator/role.py) | 全文 | 单 Codex 调用 |

---

## 2. 通用漏洞 H1–H8(预防性,目前无代码 locus)

### H1 — `tract` 同向加权

- **状态**:不存在。`VerifierCandidate` ([coordinator/dispatcher.py:34-36](../coordinator/dispatcher.py)) 只有 `label, pass_count`。
- **若朝 HANDOFF 公式方向加**:会在 `select_verifier_targets:77` 给排序键加 `+β·tract`,把 LLM 高自信的节点优先 → 真错被押后。
- **新模块对策**:`voi.py` 提供 `voi_node(v)` 替代 `tract`。`scorer.py::compute_score` 完全不暴露 `tract` 字段。

### H2 — `crit` 不含 blast radius

- **状态**:`crit` 唯一出现在 [verifier/decoder.py:194](../verifier/decoder.py) 作为 verdict 字段名(critical errors 列表),与打分无关。
- **预防**:`voi.py::sem_blast(v)` 提供下游成本求和,scorer 把它编码进 `Score.risk`。

### H3 — 没有 VOI

- **状态**:整个仓库无 `entropy / mutual_info / voi` 字串。
- **新模块对策**:`voi.py::voi_node` MC 估计,200 样本/节点,纯 stdlib。

### H4 — 没有相关性传播

- **状态**:projector ([librarian/projector.py](../librarian/projector.py)) 在收到 `verifier_critical_*` 时只更新单节点,不影响其它节点先验。
- **集成点**:`librarian/projector.py` 应用 critical verdict 后,可以可选地调用 `cluster.py::propagate_failure`。
- **风险**:projector 当前是 idempotent 投影,加副作用要谨慎 —— 见 `SCORING_INTEGRATION.md` "Cluster 集成" 节。

### H5 — 自评估偏差

- **状态**:[generator/role.py](../generator/role.py) 与 [verifier/role.py](../verifier/role.py) 用同一 Codex 后端、同一模型。即便 prompt 不同,**模型族同源**意味着 H5 已经部分成立。
- **新模块对策**:`verifier_ensemble.py` 支持配置不同 `model_id` 列表的 k>=3 调用。

### H6 — 没有 refute task

- **状态**:无 "counter-example" / "refute" 角色。`cli/`、`generator/`、`verifier/` 全无相关 prompt。
- **新模块对策**:`refute.py` 作为对偶 task 提供。集成时新增一个 worker 类型(详见 `SCORING_INTEGRATION.md`)。

### H7 — `−δ·cost` 推向廉价节点

- **状态**:cost 不进现有调度排序键。但 `Event.cost` ([common/kb/types.py:116](../common/kb/types.py)) 已经预留为 `dict | None`,说明早期设计想加 —— 若直接接到 sort key 上就踩坑。
- **新模块对策**:`scorer.py` 用 `priority = info_value / max(cost, ε)` 而非 `info_value − δ·cost`,避免单调推向小 cost。

### H8 — 单标量打分

- **状态**:不存在打分。
- **新模块对策**:`scorer.py::Score` 是 6 维 dataclass,Pareto 前沿 + Thompson 抽样。

---

## 3. NL 特异性漏洞 H9–H11

### H9 — verifier 噪声 + 校准漂移(**已踩中**)

- **现场**:
  - [verifier/role.py](../verifier/role.py) 单调用,verdict 直接进 projector,无 confidence、无 ROC、无 ground-truth 反馈环
  - [verifier/decoder.py:170-215](../verifier/decoder.py) 解码后丢掉所有 LLM 自报的不确定性
  - projector ([librarian/projector.py](../librarian/projector.py)) 把 verdict 当作 ground truth 入 KB
- **风险**:`p_fpr` 实际可能远大于 0,系统当 0 处理 → 错的命题悄悄被吸收。
- **新模块对策**:`calibration.py::VerifierROC` 维护按难度桶的 `(p_tpr, p_fpr)` Beta 后验。集成需:
  1. verifier 输出补上 `confidence: float`
  2. 5% 抽样人工 / 强 verifier 复审作为 ground truth 反馈,经 `VerifierROC.update` 进 ROC

### H10 — 没有 unification 的 bridge 判定

- **状态**:**bridge 合并目前不在代码里**。Phase II.5 的 M12/M13(动态证明树 / DAG view)是这块的容器,但合并判据未实装。
- **新模块对策**:`bridge.py::BridgeAudit` 提供 `(emb-cosine + LLM-equivalence + refute 二审)` 三段式,集成到将来的 bridge 合并流。

### H11 — LLM-as-judge FP 高(**已踩中**)

- **现场**:同 H9,k=1 调用 + 无对偶任务。
- **新模块对策**:`verifier_ensemble.py::EnsembleVerifier` 默认 k=3 + Dawid-Skene 加权。集成需扩 verifier role 的 dispatch 容量(每节点 ×k)。

---

## 4. 改造路线图

### 4.1 不动现有代码就能落地的部分(Phase A)

新建 `rethlas_scoring/` 包,**不接** coordinator,但导入/类型上对齐 `common/kb/types.Node`。这部分包含:

- `data.py` — ScoredNode 包装 `common.kb.types.Node` + 加扩展字段(posterior_p, embedding, refute_severity, speculative_load)
- `calibration.py`、`voi.py`、`cluster.py`、`verifier_ensemble.py`、`bridge.py`、`refute.py`、`scorer.py`、`scheduler.py`(全是离线纯函数 / 内存数据结构)
- `tests/scoring/` 全套单测 + 不变式测试

这部分**完全独立**,跑通就交付。

### 4.2 接现有 coordinator 的最小改动(Phase B)

唯一改动文件:`coordinator/dispatcher.py`。

- 给 `select_verifier_targets` 增加可选参数 `priority_fn: Callable[[Sequence[VerifierCandidate]], list[str]] | None = None`,默认 `None` → 现有 `(pass_count, label)` 排序。
- `coordinator/main.py:863-868` 调用处加配置开关 `state.config.scheduling.use_voi_scoring`,True 时构造 `priority_fn = rethlas_scoring.scheduler.make_priority_fn(snapshot)`。
- `state.config.scheduling.use_voi_scoring` 默认 `False`(回滚开关)。

风险:为 VOI 计算 `snapshot` 必须包含 `claim_text` 与 `embedding`。当前 `CandidateInput`([coordinator/main.py](../coordinator/main.py))只携带 label / pass_count / deps;需要扩展 KB snapshot 字段。**这条扩展放在 Phase C,Phase B 先用 stub embedding(全 0 向量)+ 简化 prior(0.5 常数)走通管道。**

### 4.3 真正打开 VOI 信号(Phase C,需要 verifier 改造)

- verifier role 输出 confidence
- ground-truth 反馈环(5% 人工 / 强 verifier)
- ensemble k=3 在 verifier role 内并发
- `Node.embedding` 字段,librarian 写入时计算

这一段不属于本次任务,留 INTEGRATION.md 写注意事项。

---

## 5. 不变式自检要求(测试必须覆盖)

来自 SCORING_DESIGN §13:

- **I1**: `voi_node(v) ≥ 0` —— `tests/scoring/test_voi.py::test_voi_nonneg`
- **I2**: `0 ≤ p̂(v) ≤ 1` 任何变化后 —— `tests/scoring/test_invariants.py::test_posterior_in_unit_interval`
- **I3**: cluster 失败传播只下降不上升 —— `tests/scoring/test_cluster.py::test_propagation_monotone_decreasing`
- **I4**: `λ_t` 关于 verified_fraction 单调非增 —— `tests/scoring/test_scheduler.py::test_anneal_lambda_monotone`
- **I5**: certify 终止时 `P(Z=1) ≥ 1−ε` 实测 —— `tests/scoring/test_scheduler.py::test_certify_termination`
- **I6**: scheduler Pareto 前沿不重复处理 —— `tests/scoring/test_scheduler.py::test_pareto_no_duplicate_dispatch`

加 4 条 VOI 边界:

- VOI=0 当 `p̂ → 0`
- VOI=0 当 `p̂ → 1`
- VOI=0 当 verifier `p_tpr == p_fpr`(verifier 报废)
- VOI 完美 verifier + p̂=0.5 时等于 H(0.5)
