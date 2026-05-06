# Rethlas-plus 打分与调度重设计(自然语言证明场景)

> 适用场景:**纯自然语言证明**,verifier 由 LLM 担任(或 LLM panel)。**本项目不使用 Lean kernel**(2026-05-06 用户指令)—— 终极仲裁来自强模型一致或人审。
> 本文档对应代码:`rethlas_scoring/` 模块。

---

## 0. 一句话要义

整套打分要从"奖励 LLM 自信和容易性"切换到 **"奖励信息"**。
"尽快确认证明对" 与 "尽早找出严重错误" 在数学上是同一个目标 —— **最大化对随机变量 `Z := 整个证明成立` 的互信息**。
两者只是同一个 VOI(value of information)框架在不同先验下的退化。

---

## 1. 原打分公式与漏洞清单

原始公式:

```
score(v) = α·crit(v) + β·tract(v) + γ·bridge(v) − δ·cost(v) − ε·stale(v)
```

至少 8 个结构性漏洞(对应每条给出代码模块里的修复):

| # | 漏洞 | 后果 | 修复模块 |
|---|------|------|---------|
| H1 | `tract` 同向加权 | LLM 自信处优先,真错处押后 | `voi.py`(去掉 tract,换 VOI) |
| H2 | `crit` 不含 blast | 错的代价没进打分 | `voi.py`(`sem_blast`)|
| H3 | 无 VOI | 0.5 附近(信息峰值)被忽略 | `voi.py`(MC 估计) |
| H4 | 无相关性传播 | 同一 bug 要被独立暴露 N 次 | `cluster.py` |
| H5 | 自评估偏差 | 提议者打自己的分 | `verifier_ensemble.py` + `refute.py` |
| H6 | 没有 refute task | 只问"能否证",不问"能否反驳" | `refute.py` |
| H7 | `cost` 单调推向廉价节点 | 错误最常藏在长 tactic 块 | `scorer.py`(`VOI/cost` 替代 `−cost`)|
| H8 | 单标量打分 | 系统性漏掉一类节点 | `scorer.py`(Pareto + Thompson) |

自然语言证明额外的三个漏洞:

| # | NL 特异性漏洞 | 修复 |
|---|---|---|
| H9 | verifier 自己噪声大且校准漂移 | `calibration.py`(在线 ROC) |
| H10 | 没有形式系统的 unification,bridge 判定靠猜 | `bridge.py`(语义等价 + 反例对偶审) |
| H11 | LLM-as-judge 假阳性高(看起来对) | `verifier_ensemble.py`(k≥3 + Dawid-Skene) |

---

## 2. 数据模型

```
ProofGraph = (V, E)
  V = {nodes}       -- 每个节点是一个命题/子目标
  E ⊂ V × V         -- 依赖边:u → v 表示 v 的成立用到 u

Node 字段:
  id, claim_text, status ∈ {open, proposed, verified, refuted, speculative}
  prior_p   : LLM 给的"成立概率"先验(经 calibration 校正后)
  posterior_p : 维护中的后验
  embedding : 语义向量(用于 cluster)
  ancestors / descendants
  speculative_load : 当前下游正在投机执行的总成本
  verifier_history : list[VerifierObservation]
```

DAG 上还维护两个集合:
- 前向前沿 `F`:已验证节点的下游可达开放节点
- 后向前沿 `B`:由 `G*` 反向归约出的未关闭子目标

这套数据结构在 `data.py` 里定义。

---

## 3. 核心:Verifier 噪声模型与 calibration

> **重要 caveat(2026-05-06 修订)**:本节描述的 calibration 学习
> (`VerifierROC.update`、`IsotonicCalibrator.add`)**只在拥有真 ground
> truth 的场合**才能起作用。本项目当前只有一种 ground truth 来源:
> **人工 spot-check**。原稿提议的"5% LLM-vs-LLM 抽样审计"已被驳回
> —— 证明对就是对错就是错,不是概率问题(见 `SCORING_INTEGRATION.md
> §5` 设计注)。
>
> 在没有人审反馈进入的稳态下,`VerifierROC` 的 Beta 后验保持在
> Beta(1, 1) 先验(均值 0.5),likelihood ratio 退化成 1。VOI 公式仍
> 成立,但 verifier "信号强度" 等于 0 → VOI 主项归零,系统靠 cluster
> susp / blast radius / refute severity 排序。
>
> 这一节因此应被读作:**"有真值反馈时怎么吸收"** —— 而非"产线常态"。

设 verifier 是一个随机函数 `Verify : Node → {ok, fail}`,真值是 `T(v) ∈ {0, 1}`。

定义两个关键率:

- **TPR**(真阳性率):`p_tpr(v) := P(Verify(v) = ok | T(v) = 1)`
- **FPR**(假阳性率):`p_fpr(v) := P(Verify(v) = ok | T(v) = 0)`

注意 `p_fpr` 是**最危险的量** —— 错的命题被 verifier 接受。LLM verifier 的 `p_fpr` 在结构上不是 0,经验上对"看起来对"的命题尤其大。

### 3.1 在线维护

按节点难度桶维护 `(p_tpr, p_fpr)`:难度通过 `claim_text` 的 token 长度 + LLM 自评 hardness 离散化为 5 桶。
每个桶维护 Beta 分布,**当人工 spot-check 提供 ground truth 时**更新。

```
class VerifierROC:
    bucket(node) -> bucket_id
    posterior_tpr(b) -> Beta(α, β)
    posterior_fpr(b) -> Beta(α, β)
    update(node, predicted, ground_truth)
```

详见 `calibration.py`。

### 3.2 似然比

VOI 计算需要 likelihood ratio:

```
LR_+(v) = p_tpr(v) / p_fpr(v)            -- verifier 说 ok 时,T=1 vs T=0 的证据强度
LR_-(v) = (1−p_tpr(v)) / (1−p_fpr(v))    -- verifier 说 fail 时
```

由 ROC 桶的当前后验给出。

---

## 4. VOI:噪声 verifier 下的形式化

### 4.1 定义

设 `Z := ∧_v T(v)`(整个证明成立)。验证节点 `v` 的 VOI 定义为:

```
VOI(v) := H(Z) − E_{r ~ Verify(v)}[ H(Z | r) ]
```

其中 `H` 是 binary entropy。

### 4.2 蒙特卡洛近似

直接计算 `H(Z)` 在 DAG 上是 #P-难的(等价于 SAT 模型计数)。用 MC:

```
Algorithm voi_node(v, graph, calibrator, N=200):
    sample N truth assignments {T_i}_{i=1..N} from current posteriors p̂(u) for all u
    Z_i := ∧_{u in v's relevance cone} T_i(u)            -- only nodes that affect Z
    p_Z := mean(Z_i)
    H_before := h(p_Z)                                    -- binary entropy

    For each possible verifier outcome r ∈ {ok, fail}:
        For each sample i:
            w_i^r := P(Verify(v)=r | T_i(v))             -- = p_tpr or p_fpr or complements
        p_Z_given_r := Σ_i w_i^r · Z_i / Σ_i w_i^r
        p_r        := mean over i of w_i^r
        H_after_r  := h(p_Z_given_r)

    H_after := p_ok · H_after_ok + p_fail · H_after_fail
    return max(0, H_before − H_after)
```

实现在 `voi.py`。N=200 在 100~500 节点的 DAG 上跑一次约 50ms,可接受。

### 4.3 重要性质(用于测试)

数学上必须满足:

1. `VOI(v) ≥ 0` 始终成立(性质保证测试 1)
2. 当 `p̂(v) → 0` 或 `→ 1`,`VOI(v) → 0`(已经知道答案,验证无信息)(测试 2)
3. 当 verifier 完美(`p_tpr=1, p_fpr=0`)且 `p̂(v)=0.5` 时,`VOI(v) = h(p_Z=0.5) − 0`(测试 3)
4. 当 verifier 完全无用(`p_tpr=p_fpr`),`VOI(v) = 0` 不论先验如何(测试 4 —— 这条防止系统因 verifier 报废还浪费预算)

测试在 `tests/test_voi.py` 中实施。

### 4.4 语义 blast radius

```
sem_blast(v) := Σ_{u downstream of v} sem_relevance(v, u) · |speculative_load(u)|
```

`sem_relevance` 不只是图上"有路径",还要乘以 LLM 抽出的"u 对 v 的语义依赖度"(0/1 离散化或 [0,1] 连续)。这避免了"v 在 graph 上下游有 100 个节点,但实际上只有 3 个真用了 v"导致的过估。

---

## 5. Cluster 传播

### 5.1 邻接

对每个节点的 `claim_text` 取 embedding(任何 sentence transformer 或 LLM embedding API)。
节点 `u, v` 之间的"簇邻接强度":

```
sim(u,v) := max(0, cos(emb(u), emb(v)) − τ)        -- τ 是阈值,典型 0.65
```

### 5.2 失败传播

当 `Verify(u) = fail` 被确认(可信度高):

```
For each v ∈ neighbors(u, sim > τ):
    posterior_p(v) := posterior_p(v) · (1 − γ · sim(u,v))
        with γ ∈ [0, 1] 控制传染力,典型 γ = 0.5
```

这是粗 Bayesian update,假设"shared concept 出错的事件"按相似度耦合。
详细实现在 `cluster.py`。

### 5.3 可监督的部分

每次 cluster 传播后,被打低 `posterior_p` 的节点应被推到调度器优先级高位。
这把"一个 bug 暴露一片可疑"做成自动行为,而不是事后人肉发现。

---

## 6. Bridge audit

自然语言场景下,F 和 B "见面" 的判据是**语义等价**而非 unification。

### 6.1 等价判据

候选桥接 `(u ∈ F, v ∈ B)`:

```
is_bridge(u, v) :=
    cos(emb(u), emb(v)) > 0.85
    AND llm_judge_equivalent(u.claim, v.claim) ∈ {YES_strict, YES_modulo_renaming}
```

`llm_judge_equivalent` 用单独 prompt:见 `refute.py` 的 `EQUIVALENCE_PROMPT`。

### 6.2 二次审

桥接被声明前,**强制走 refute task**:

```
audit_report := refute_task(combined_claim = u → v)
If audit_report.found_counterexample:
    bridge rejected; mark u or v for re-verification at higher budget
Else:
    bridge accepted but flagged for end-of-proof full re-verification
```

这是 H10 的修复。原设计里 bridge 只要满足图结构就关闭 —— 这是 LLM 证明里出 bug 的高频点。

---

## 7. 多 verifier 与 Dawid-Skene

> **caveat(同族 ensemble 隐患)**:Dawid-Skene 的数学前提是**worker
> 错误彼此独立**。当 k=3 verifier 都是同族 LLM(Claude Sonnet × 3 不同
> seed,或 Claude Sonnet + Claude Haiku + Claude Opus 同 prompt),它们
> 的失败模式高度相关 —— 都被同一个 prompt 偏置或 token 模式骗。这种
> "假独立" 让聚合后验过度自信。
>
> 缓解:k=3 至少跨 **两个不同模型族**(例如 Claude × 2 + GPT × 1),且
> prompt 模板做对偶变体(中立陈述 / 鼓励反驳 / 鼓励重述)。
>
> 终极兜底仍是 §8 refute task —— 它问的是不同问题(找反例),不是
> verifier 的副本。

### 7.1 ensemble

对一个节点跑 k 次独立 verifier(不同 seed,可能不同模型):

```
EnsembleVerifier(k=3, models=[...])
    for each call: returns (label, confidence)
```

### 7.2 聚合

用 Dawid-Skene EM 估计每个 worker 的 confusion matrix,加权聚合 label:

```
For each worker w: estimate (TPR_w, FPR_w) via EM
Aggregated posterior p(T=1 | observations) ∝ ∏_w P(obs_w | T=1)
                                            ∝ ∏_w (TPR_w if obs_w=ok else 1−TPR_w)
```

参考:Dawid & Skene (1979), Applied Statistics 28(1).
实现:`verifier_ensemble.py`。

### 7.3 disagreement bonus

当 ensemble 内部分歧大(例如 2:1),即:

```
disagreement(v) := 1 − |2 · majority_fraction − 1|
```

把 `disagreement(v)` 作为额外信号推入打分(它本质上是 VOI 的近似快算 —— 分歧大说明对 `T(v)` 还吃不准)。

---

## 8. Refute task

对偶任务,用一个不同的 prompt 让 LLM 找反例:

```
Given the claim: <claim_text>
Find:
  (a) a concrete counterexample, OR
  (b) edge cases where the claim is fragile, OR
  (c) hidden assumptions used implicitly, OR
  (d) "no apparent issue" + brief justification.

Output JSON:
{
  "verdict": "counterexample" | "fragile" | "hidden_assumption" | "no_issue",
  "details": "...",
  "severity": 0.0..1.0
}
```

severity 直接写入 `Node.refute_severity`,与 verifier 失败信号一起进 cluster 传播。

详见 `refute.py`。

---

## 9. 重写后的打分

### 9.1 多维 Score(不强行聚合成单数)

```
@dataclass
class Score:
    voi:           float    # 主信息项
    risk:          float    # = (1 − p̂(v)) · sem_blast(v) · speculative_load(v)
    cluster_susp:  float    # cluster 传染推上来的可疑度
    bridge_bonus:  float    # 桥接节点的额外加权(乘 BridgeAudit pass 标志)
    disagreement:  float    # ensemble 分歧
    cost:          float    # 预估 wall-clock + token 成本
```

### 9.2 Pareto 前沿

每轮调度从 Pareto 前沿(对 `(voi, risk, disagreement, −cost)` 的非支配集)选 K 个候选。

### 9.3 Thompson 抽样 + 退火

候选内用退火 λ_t 加权聚合:

```
priority(v, t) =
    (1 / max(cost(v), ε)) ·
    [ voi(v)
    + λ_t · risk(v)
    + μ_t · cluster_susp(v)
    + ν_t · bridge_bonus(v)
    + ξ_t · disagreement(v) ]

λ_t = max(0.1, 1.0 − verified_fraction)
μ_t, ν_t, ξ_t: 类似衰减 / 从配置读
```

最终 Thompson:从优先级分布 softmax(temperature decreasing) 抽样,而不是 argmax。
这给打分模型自身的不确定性留出探索空间(H8 的修复)。

### 9.4 抢占

`verifier_callback(v, ok)` 时:
- `v` 关闭 → 取消所有依赖 `v` 的 pending generator tasks(它们的 gain 归零)
- `v` 失败 → 触发 cluster 传播 + reschedule

实现在 `scheduler.py`。

---

## 10. 终止条件:certifying set 的最小覆盖

整个证明被声明"已确认"的条件不是"全部节点都验证一遍",而是:

> 存在一个验证子集 `S ⊆ V`,满足 `P(Z = 1 | ∀v ∈ S, Verify(v) = ok) ≥ 1 − ε`。

这是**集合覆盖问题**,贪心 + VOI 是 (1 − 1/e) 近似:

```
S := ∅
while P(Z=1 | S verified) < 1 − ε:
    pick v* := argmax_v VOI(v | S already verified)
    S := S ∪ {v*}
```

调度器实际上就在做这个(只是 online + 带成本)。
**这给"什么时候停"提供了量化判据**,而不是凭感觉。

---

## 11. 参数默认值

```
embedding_threshold τ        = 0.65
cluster_decay γ              = 0.5
ensemble_size k              = 3
voi_mc_samples N             = 200
isotonic_min_samples         = 30
λ_t schedule                 = max(0.1, 1.0 − verified_fraction)
thompson_temperature τ_t     = 1.0 → 0.2 (linear)
certify_epsilon ε            = 0.01
refute_severity_threshold    = 0.4 (≥ 此触发回归 verifier 高预算)
```

---

## 12. 不变式(测试核对清单)

代码必须保证:

- I1: `VOI(v) ≥ 0` 一切时刻
- I2: `p̂(v)` 永远在 `[0, 1]` 内
- I3: cluster 传播只能让 `p̂` 单调下降(失败信号),不能因为 sim 高就反向推高
- I4: 退火 `λ_t` 关于 `verified_fraction` 单调非增
- I5: certify 终止时,`P(Z=1) ≥ 1 − ε` 实测成立(用同一 MC 采样器)
- I6: scheduler 的 Pareto 前沿不会被同一节点重复处理(去重 + 已分派标记)

---

## 13. 文件索引

```
rethlas_scoring/
├── data.py                  # ProofGraph, Node, VerifierObservation
├── calibration.py           # IsotonicCalibrator, VerifierROC
├── verifier_ensemble.py     # Verifier ABC, EnsembleVerifier, Dawid-Skene
├── voi.py                   # MC VOI estimator, sem_blast
├── cluster.py               # ClusterIndex, embedding 邻接, 传播
├── bridge.py                # BridgeAudit
├── refute.py                # RefuteTask, prompt 模板, EQUIVALENCE_PROMPT
├── scorer.py                # Score, compute_score, Pareto 前沿
├── scheduler.py             # 主循环, 退火 λ, 抢占
├── tests/
│   ├── test_calibration.py
│   ├── test_voi.py
│   ├── test_cluster.py
│   ├── test_scheduler.py
│   └── test_invariants.py
└── docs/
    ├── DESIGN.md            # 本文
    └── INTEGRATION.md       # 集成进 Rethlas-plus 的具体路径
```
