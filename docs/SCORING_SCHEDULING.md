# SCORING_SCHEDULING — 调度策略研究

> 用户 2026-05-06 指令:**"verification 的要求是所有节点最终过 3 遍,
> 但是一开始要尽量先让所有节点过一遍。"**
>
> 配套 `SCORING_DESIGN.md`(数学)、`SCORING_INTEGRATION.md`(集成)。
> 本文是研究/设计稿,**还未落地**。

---

## 1. 问题陈述

每个节点要被验证 `desired_pass_count` 次(默认 3)才算 `verified`。
两个相反的极端:

- **深度优先(DFS)**:一次把节点 A 验完 3 遍再去 B。坏处:A 出错时,
  B…N 还没收到信号,白白等它三轮。一个错误暴露最迟。
- **广度优先(BFS)**:每个节点先过 1 遍,然后所有节点过第 2 遍,再过
  第 3 遍。好处:第 1 轮就能广覆盖,任何 critical 立刻触发 cluster
  传播 + 抢占。代价:确认时间略长(必须三轮才能宣告 verified)。

用户要求 **BFS by tier**:tier = `pass_count`,严格先把所有 tier-0 都
推到 tier-1,再开始 tier-2,以此类推。

### 1.1 为什么数学上 BFS 优于 DFS

记 `Z := ∧_v T(v)`。第 1 轮 verifier 报告对每个节点都是高信息量
(`p̂(v)` 大致在先验附近,VOI 接近峰值)。第 2、3 轮在前两轮一致 ok 时
信息量已经很低 —— 大部分熵已经被前两轮吃掉了。

形式化:

```
VOI(pass r on node v | history H) ≈ H(P(Z=1 | H)) − E[H | H + new pass]
                                  ≤ H(P(Z=1 | H))
```

History 越长,先验越尖,VOI 越低。所以 **每个节点的第 1 次 verify 比同
节点的第 2 次更有价值,而第 1 次也比邻居节点的第 2 次更有价值**(假设
节点对 Z 的边际贡献相近)。

这正好等价于 BFS-by-tier:tier-0 → tier-1 的转移恒比 tier-1 → tier-2
转移更有信息量。

唯一例外:某节点的 tier-0 verifier 报 `critical`,这时启动该节点的第 2、3
轮 *与其它节点的第 1 轮* 之间出现真正的取舍 —— 见 §3。

---

## 2. 当前实现的裂缝

### 2.1 Legacy dispatcher — **已经满足 BFS by tier**

[coordinator/dispatcher.py:77](../coordinator/dispatcher.py:77) 用
`sorted(by_label.items(), key=(pass_count, label))`。任何 tier-k 的候选
**总是**排在 tier-(k+1) 候选之前。Capacity 在 tier-k 用满之前不会触及
tier-(k+1)。**这条策略在 commit 之前的代码里就是对的。**

### 2.2 Phase B wiring — **打破了它**

提交 `303b686` 在 `use_voi_scoring=true` 时调用
`rethlas_scoring.scheduler.make_priority_fn`。该函数 **完全忽略
`pass_count`**:

```python
# rethlas_scoring/scheduler.py::make_priority_fn (current)
def _priority_fn(candidates, capacity):
    valid = [c for c in candidates if c in graph.nodes]
    scores = [compute_score(nid, graph, roc, ...) for nid in valid]
    picked = thompson_sample(scores, ...)
    return [s.node_id for s in picked]
```

`ScoredNode` 也没有 `pass_count` 字段(`grep "pass_count"
rethlas_scoring/` 0 条命中)。结果:VOI Thompson 从 tier-0 ∪ tier-1 ∪
tier-2 全集采样。一个 tier-2 高 VOI 节点可能被选中,而 tier-0 节点饿
着。

**这是 Phase B 当前的缺陷。** Phase A 的 `select_verifier_targets`
fallback 路径(`priority_fn=None`)未受影响,所以 toggle 设 `false` 时
仍然安全。

---

## 3. 设计选项

### 3.1 选项 A — Dispatcher 按 tier 分组,每 tier 各请 priority_fn 一次

```python
# coordinator/dispatcher.py::select_verifier_targets (proposed)
def select_verifier_targets(candidates, *, capacity, in_flight_targets,
                            priority_fn=None):
    busy = set(in_flight_targets)
    by_label = dedup_min_pass_count(candidates)
    if not by_label or capacity <= 0:
        return []

    # Group by tier; walk low → high.
    by_tier: dict[int, list[str]] = {}
    for lbl, pc in by_label.items():
        by_tier.setdefault(pc, []).append(lbl)

    out: list[str] = []
    for tier in sorted(by_tier):
        tier_labels = sorted(by_tier[tier])  # legacy default
        if priority_fn is not None:
            try:
                voi_order = priority_fn(tier_labels, capacity - len(out))
                # leftover safety as today
                ...
                tier_labels = voi_order + leftover
            except Exception:
                pass  # fall back to alphabetical within tier
        for lbl in tier_labels:
            if lbl in busy:
                continue
            out.append(lbl)
            busy.add(lbl)
            if len(out) >= capacity:
                return out
    return out
```

**优点**:
- BFS by tier 是结构性约束,不是 priority_fn 自觉
- VOI 只需要决定 *同一 tier 内* 的次序 —— 这是 VOI 真正擅长的事
- priority_fn 接口不变(仍是 `(labels, capacity) -> labels`),scoring
  层不需要懂 pass_count
- 现有 7 个 dispatcher 单元测试仍通过(legacy 路径 unchanged)

**缺点**:
- 多了一层 for tier。capacity 大时 priority_fn 可能被调用多次,每次跑
  VOI MC —— 见 §3.4 性能。

### 3.2 选项 B — ScoredNode 加 pass_count,priority_fn 内部分组

把 `pass_count` 推入 scoring 层:`ScoredNode.pass_count: int`,
`make_priority_fn` 自己按 tier 分组。

**优点**:dispatcher 改动 0。
**缺点**:scoring 层要懂调度协议(违反职责分离);scoring 测试要造
pass_count;ScoredNode 字段越来越胖。

### 3.3 选项 C — 完全在 priority 公式里编码

在 `priority(score, lambda_t, ...)` 里加 `−κ·pass_count`,κ 巨大。让
高 tier 节点的 priority 永远比低 tier 节点低。

**优点**:实现最简单。
**缺点**:不是硬约束,只是软优先。Thompson 抽样有可能(概率小但非
零)穿透。也不利于"刚有 1 个高 VOI tier-2 节点一直输给 tier-0"的可
读性。

### 3.4 性能

VOI MC 在 N=200 / 100~500 节点的图上 ~50ms。选项 A 把它从"每 tick 1
次"放大到"每 tick × tier 数"。tier 数 = `desired_pass_count + 1` =
4,即 ~200ms/tick。Coordinator 的 tick 间隔是 1s(default),还有
余量 5×。可以接受。

如果未来要降:
- 缓存:tier 内候选不变时复用上次的 score
- 早停:capacity 满了立刻退出,不计算更高 tier
- 降 N:N=100 即可,损失精度 ~5%

---

## 4. 推荐方案

**选项 A**。理由:

1. BFS by tier 是 *调度协议* 的一部分,不是评分逻辑 —— 应该归
   dispatcher。
2. scoring 层保持纯粹"评比同质候选",方便后续替换或独立测试。
3. dispatcher 已经持有 `pass_count`(从 `VerifierCandidate.pass_count`
   解码),不需要新数据通路。
4. 性能可接受。

实施清单(下一档 commit):

- [ ] `coordinator/dispatcher.py::select_verifier_targets` —— 按 tier
  分组,逐 tier 调 priority_fn。
- [ ] `tests/scoring/test_dispatcher_integration.py` —— 加 5 条:
  - `tier_constraint_lower_pass_count_first` —— 高 VOI 的 tier-1 必输
    给低 VOI 的 tier-0
  - `voi_ordering_within_tier` —— 同 tier 内仍按 VOI 排
  - `tier_constraint_with_in_flight` —— in_flight 跨 tier 隔离正确
  - `legacy_dispatcher_path_still_alphabetical_within_tier`
  - `priority_fn_failure_falls_back_per_tier_not_globally`
- [ ] `tests/unit/test_m8_dispatcher.py` —— 现有 7 条不应破。
- [ ] 更新 `SCORING_INTEGRATION.md §2 / §3` 说明 tier 协议。
- [ ] 更新 `SCORING_DESIGN.md §9` 加上 BFS-by-tier 的数学论证。

---

## 5. 仍待用户决定

1. **`desired_pass_count = 3` 是否可调?** 配置已经有这个字段
   (`SchedulingConfig.desired_pass_count`),默认 3。BFS by tier 用的就
   是它。如果用户想"前两轮 BFS,第 3 轮按 VOI 跨 tier 抓最可疑的",这
   会是 §3.3 的软优先变体。当前我假设硬约束。
2. **"过 3 遍"的"遍"是否指独立 verifier worker?** 如果是 ensemble
   (DESIGN §7 的 k=3 + Dawid-Skene),"3 遍"可能意味着"3 个不同 worker
   各跑 1 遍",而不是"同一 worker 跑 3 次"。前者强、后者弱。当前代码
   的 `pass_count` 把 worker 身份扁平化掉了 —— 一个节点连续被同一
   verifier 跑 3 次也算 `pass_count=3`。这个语义需要拍板。
3. **同 tier 内 VOI 真的对吗?** §1.1 的论证假设节点对 Z 的边际贡献相
   近。如果不近(例如 thm:main 比 lem:trivial 重要 100×),即便都在
   tier-0,`thm:main` 应该先 —— 这正是 VOI 给的答案。但要 VOI 在
   *tier-0 全空仓库* 上是否 informative,实测才知道(目前 placeholder
   prior 0.5,所有节点 VOI 区分度可能很小,退化成字典序)。
4. **Adaptive verifier policy(`SCORING_INTEGRATION.md §5.1`)是否仍要
   做?** 那一段是上轮被采纳的"刺探不一致 → refute → 升级模型"escalation
   ladder。BFS-by-tier 落地后,该 policy 可作为同节点跨 tier 的具体行为
   策略(tier-1 → tier-2 之间走哪个 ladder)。两者不冲突,但顺序值得
   讨论。

---

## 6. 系统分层视图

往后做的所有调度组件应该归到下面 5 层,各层职责正交:

| 层 | 职责 | 状态 | 关键代码 |
|----|------|------|---------|
| **L1 拓扑** | 谁是节点 / 谁依赖谁 / 在哪个池(generator vs verifier) | ✅ 现成 | [common/kb/types.Node](../common/kb/types.py:71), [coordinator/main.py:820-851](../coordinator/main.py:820) |
| **L2 资格** | "现在能不能验" —— `verifier_deps_strictly_ahead` 等硬约束 | ✅ 现成 | [coordinator/precheck.py](../coordinator/precheck.py), [coordinator/main.py:836-851](../coordinator/main.py:836) |
| **L3 Tier(BFS)** | 强保证 "所有节点过 r 遍 → 才有节点开始 r+1" | ⚠️ Legacy 对,Phase B 错 | [coordinator/dispatcher.py:77](../coordinator/dispatcher.py:77) — 见 §3 |
| **L4 同 tier 内排序** | 软偏好 —— VOI / cluster susp / blast radius | ✅ 已写,❌ 未生效(被 L3 越过) | [rethlas_scoring/scorer.py](../rethlas_scoring/scorer.py), [scheduler.py](../rethlas_scoring/scheduler.py) |
| **L5 节点动作策略** | 给一个节点选下一步是 LLM 重验 / refute / 强模型 / Lean / user_blocked | 🔲 设计中,见 §7 | 尚未实现 |
| **L6 终止判据** | 何时宣告 verified / refuted / done | ✅ 简单版(`pass_count ≥ desired`),🔲 高级版(certifying set) | [common/kb/types.Node.initial_count](../common/kb/types.py:93), [scheduler.py::certifying_set_done](../rethlas_scoring/scheduler.py) |

### 6.1 调度一次 tick 的流程(目标态)

```
1. 从 KB snapshot 读所有候选 (L1)
2. 过 L2 资格筛 → eligible[]
3. 按 pass_count 分 tier;从最低 tier 开始 (L3)
4. 在该 tier 内:
     a. 调 priority_fn(tier_labels, capacity_left) → VOI 排序 (L4)
     b. 跳过 in_flight,逐个填入,capacity 满即返回
5. 对每个被选中的节点,调 policy.decide_next_action(node, evidence) → action (L5)
     - action ∈ {default_verify, refute, strong_verify, lean, user_blocked}
6. 派 worker 执行该 action
7. Worker 回报 → 入 KB → 触发 L4 cluster 传播 + L5 状态更新
8. 检查 L6 终止 → 若 done,关 supervise
```

**当前代码状态对照**:
- 步骤 1–2:✅ done
- 步骤 3:⚠️ legacy 对 / Phase B 错(本文档主线)
- 步骤 4:⚠️ wiring 在但 L3 把它拖下水
- 步骤 5:🔲 全无 —— 永远派 default_verify
- 步骤 6:✅ done(单一动作所以平凡)
- 步骤 7a 触发 cluster:🔲 librarian/projector 没接 cluster 传播
- 步骤 8 简单版:✅(`pass_count ≥ desired`);高级版:🔲

---

## 7. L5 节点状态机 —— 详细设计

不再用 `policy.py` 第一稿那种"看到一条 critical 立刻 user_blocked"的机制。
重新基于 *evidence accumulation* 的纯函数。

### 7.1 节点的 evidence 视图

KB 每条 verifier 输出存为一行 `Evidence`:

```python
@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind          # default | refute | strong | lean
    worker_id: str              # 来自哪个 verifier 实例
    verdict: VerdictKind        # ok | gap | critical | abstain
    ts_iso: str
    counterexample: str | None  # 仅 refute kind 用
```

`Node.pass_count` 重新定义为:**default-kind 且 verdict=ok 的 evidence 计数**(去重 worker_id)。
Critical / refute / strong 走旁路,不计入 `pass_count`,但都进 evidence
列表。

### 7.2 状态判定(纯函数)

```python
def classify(evidence: list[Evidence], desired_pass: int = 3) -> NodeState:
    if any(e.kind == LEAN and e.verdict == OK for e in evidence):
        return VERIFIED                      # Lean 一锤定音
    if any(e.kind == LEAN and e.verdict == CRITICAL for e in evidence):
        return REFUTED
    if any(e.kind == REFUTE and e.counterexample for e in evidence):
        return REFUTED                       # 反例 = 真错
    n_strong_ok    = sum(1 for e in evidence if e.kind == STRONG  and e.verdict == OK)
    n_strong_crit  = sum(1 for e in evidence if e.kind == STRONG  and e.verdict == CRITICAL)
    if n_strong_ok >= 1 and n_strong_crit == 0:
        return VERIFIED                      # 强模型 1 票 = 默认模型 3 票
    if n_strong_crit >= 1:
        return USER_BLOCKED                  # 强模型说错,且没反例 = 必须人审
    n_default_ok   = sum(1 for e in evidence if e.kind == DEFAULT and e.verdict == OK)
    n_default_crit = sum(1 for e in evidence if e.kind == DEFAULT and e.verdict == CRITICAL)
    if n_default_ok >= desired_pass and n_default_crit == 0:
        return VERIFIED
    if n_default_ok >= 1 and n_default_crit >= 1:
        return DISAGREEMENT                  # 触发 refute
    return PENDING
```

**注意**:这是纯 deterministic 状态机,**没有概率**。状态由 evidence
集合的代数决定,不是后验阈值。

### 7.3 下一步动作(纯函数)

```python
def next_action(state: NodeState, evidence: list[Evidence],
                budget: PolicyBudget) -> Action:
    if state in (VERIFIED, REFUTED, USER_BLOCKED):
        return Action.NONE
    n_refute = sum(1 for e in evidence if e.kind == REFUTE)
    n_strong = sum(1 for e in evidence if e.kind == STRONG)
    if state == DISAGREEMENT:
        if n_refute < budget.max_refute:    return Action.REFUTE
        if n_strong < budget.max_strong:    return Action.STRONG_VERIFY
        if budget.lean_available:           return Action.LEAN
        return Action.USER_BLOCKED          # 升迁阶梯走完仍然分歧
    # state == PENDING
    return Action.DEFAULT_VERIFY            # 老老实实下一遍
```

### 7.4 为什么这样设计满足"binary truth"原则

- 状态机里**没有任何概率比较**(比如 "p_tpr > 0.8 才算 verified")
- "verified" 只来自三种证据:Lean ✓ / 强模型 ✓ / 默认模型 ≥3 致同意
- "refuted" 只来自硬证据:反例 / Lean ✗
- 模糊地带(分歧)走升迁阶梯,而不是估算 verifier 噪声率
- 升迁阶梯走完仍然分歧 → 退给人,**不让系统自主"概率上判定"**

---

## 8. 资源分配 —— Capacity 怎么切

`verifier_workers`(默认 4)是同时跑的 verifier 数。L5 引入多种 action
后,同一池里跑的可能是 default / refute / strong 不同 role。两种切法:

### 8.1 选项 X — 共池,policy 决定每个 slot 干什么

每个 worker slot 在该 tick 接到 `(node, action)` 一对。Worker 进程根据
`action` 启动对应 role 的 binary。

**优点**:capacity 单参数好懂;空闲时所有 slot 都能干活。
**缺点**:worker 进程要能"按需变身",binary 多形态;refute 高需求时
default 验证会被挤死。

### 8.2 选项 Y — 分池(default / refute / strong 各自 capacity)

`SchedulingConfig` 新增三个字段:

```toml
[scheduling]
verifier_workers       = 4   # 既有,只装 default
refute_workers         = 1   # 新增
strong_verifier_workers= 1   # 新增
```

**优点**:每个 role 独立配额,refute 高峰不挤兑 default;运维上看
worker 类型一目了然。
**缺点**:多了三个 worker 池,coordinator 状态变复杂;空闲时强模型 slot
没法干默认任务。

**推荐**:Y。理由:default 是稳态主流,refute / strong 是事件驱动
低频。固定配额避免抖动;capacity tuning 跟成本预算解耦。

---

## 9. Sprint 拆分(顺序 + 独立性)

每个 sprint 独立可发可回滚。每个都需要一次 commit + push。

| Sprint | 内容 | 依赖 | 估时 |
|--------|------|------|------|
| **S1** | L3 修复 —— `select_verifier_targets` 按 tier 分组,Phase B wiring 重新尊重 BFS;加 5 条测试 | 无 | 半天 |
| **S2** | `Evidence` 数据模型 + `classify()` 纯函数 + 单测;**不**改 KB schema(evidence 可从现有 verdict 列表推导) | S1 | 1 天 |
| **S3** | `next_action()` 纯函数 + 单测;coordinator 仍只派 default_verify(action 实际只用一个 case),但代码结构就位 | S2 | 半天 |
| **S4** | Refute role + worker 二进制;coordinator 接 `Action.REFUTE` 实派;独立 `refute_workers` 配置 | S3 + Y 切法 | 2 天 |
| **S5** | Strong verifier role(只换模型,prompt 复用);coordinator 接 `Action.STRONG_VERIFY` | S4 | 半天 |
| **S6** | Embedding pipeline:librarian 写 KB,scoring 真值 cluster 传播;`projector` 接 `propagate_failure` | 任意时刻可做(独立) | 2 天 |
| **S7** | Ensemble k=3:同 node 同 pass 派 k 个独立 default worker,Dawid-Skene 聚合 | S2 | 1.5 天 |
| **S8** | Lean 接口:`Action.LEAN` 派 Lean kernel(M14+ 同步) | S5 | 长远 |
| **S9** | Certifying-set 终止(DESIGN §10):VOI 跑动态终止判据替代固定 `pass_count` | S6 + S7 | 1 天 |

**最小可发集**:S1 单独发 → 立刻把 Phase B 的 BFS bug 修掉。其他不阻塞。

**自然下一档**:S1。我可以现在就做。

---

## 10. 还有一个隐藏假设值得说清楚

L4 的 VOI 有意义的前提是 **"posterior_p 不全部相等"**。当前 Phase B
wiring 给所有节点 `posterior_p = 0.5`(placeholder),所有节点 sem_blast
也几乎相等(speculative_load 全 0,只剩 descendants 数差异)。结果
**VOI 几乎退化成"按下游节点数排"** —— 这恰巧是 "verify 根定理 / 重要
中间结果优先" 的合理近似,但不是 VOI 设计的本意。

要让 VOI 真正发挥作用,必须先把以下任一件做了:

1. verifier 输出真 confidence,projector 写真 posterior_p(对应 S2 的
   一部分 + verifier 改造)
2. embedding 落地,cluster 传播实际改 posterior_p(S6)
3. refute task 实际跑,severity 进 cluster 信号(S4)

在那之前,L4 的 VOI 大致等价于"按下游影响力排"。S1 的修复让我们至少
**不会因为 VOI 的存在而打破 L3 BFS** —— 这是底线。

---

## 11. 我现在的建议

**做 S1**(半天,纯调度协议修复,有测试,可独立 commit/push)。其它一律等用户拍板。

S1 的范围:
- 改 `coordinator/dispatcher.py::select_verifier_targets` 走 §3.1 选项 A
- 加 5 条 dispatcher 集成测试
- 现有 7 条 dispatcher 单测 + 49 条 scoring 测 + 10 条 wiring 测全绿
- 更新 `SCORING_INTEGRATION.md §3` 反映 tier 协议
- commit + push

是否开做?
