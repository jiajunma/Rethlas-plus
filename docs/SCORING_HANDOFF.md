# Rethlas-plus 打分/调度重设计 —— Handoff

> 上下文文档,与 `docs/SCORING_DESIGN.md`(完整数学设计)、`docs/SCORING_AUDIT.md`
> (现状漏洞定位)、`docs/SCORING_INTEGRATION.md`(集成点) 配套。
> 真代码在 `rethlas_scoring/` 包内。

---

## 用户身份与上下文

我是一个数学家。当前在做 Rethlas-plus(LLM 辅助证明系统)的 phase 2/3 重审。
当前主体是**自然语言证明**,verifier 由 LLM 担任(LLM-as-judge)。长期目标接 Lean 4。

## 任务的根本目标

让生成/验证任务的自动调度满足两个目标的**同时**最大化:

1. 尽快确认整个证明是正确的
2. 尽早发现严重错误

数学上这两个是**同一目标在不同先验下的退化** —— 都等价于最大化对 `Z := 整个证明成立` 的互信息(VOI)。

## 已诊断的漏洞(11 个)

原打分公式(诊断对象,**当前代码尚未实现这条公式** —— 见 `SCORING_AUDIT.md`):

```
score(v) = α·crit(v) + β·tract(v) + γ·bridge(v) − δ·cost(v) − ε·stale(v)
```

通用漏洞:

- **H1**: `tract` 同向加权 → LLM 自信处优先,真错处押后
- **H2**: `crit` 不含 blast radius → 错的代价没进打分
- **H3**: 没有 VOI → `p̂ ≈ 0.5`(信息峰值)被忽略
- **H4**: 没有相关性传播 → 一个 bug 要被独立暴露 N 次
- **H5**: 自评估偏差 → 提议者给自己打分
- **H6**: 没有 refute task → 只问"能否证",不问"能否反驳"
- **H7**: `−δ·cost` 推向廉价节点 → 错误最常藏在长 tactic 块
- **H8**: 单标量打分 → 总有一类节点系统性被忽视

NL 特异性漏洞:

- **H9**: verifier 自己噪声大且校准漂移
- **H10**: 没有 unification,bridge 判定靠猜
- **H11**: LLM-as-judge 假阳性高(看起来对)

## 重设计要点

1. **VOI 替代 tract**。`VOI(v) := H(Z) − E_r[H(Z|Verify(v)=r)]`,蒙特卡洛估计(N=200 样本)。
2. **风险显式化**:`risk(v) = (1−p̂(v)) · sem_blast(v) · speculative_load(v)`。
3. **Verifier 噪声模型**:每节点按难度分桶,在线维护 `(p_tpr, p_fpr)` 的 Beta 后验,VOI 计算用 likelihood ratio。
4. **Verifier ensemble**:k=3 独立调用 + Dawid-Skene 加权。
5. **Cluster 传播**:embedding 余弦邻接,失败按 `p̂ ← p̂·(1 − γ·sim)` 衰减传给邻居。γ=0.5。
6. **Bridge audit**:语义等价检查 + 强制 refute task 二审,通过后仍标记终局重检。
7. **Refute task**:对偶 prompt,找反例/边界/隐含假设。severity 进 cluster 信号。
8. **多维 Score + Pareto + Thompson**:不强行聚合成单数,从 Pareto 前沿 Thompson 抽样。
9. **退火**:`λ_t = max(0.1, 1 − verified_fraction)`,早期偏找错,晚期偏确认。
10. **Certifying set 终止条件**:贪心 VOI 选最小集 S 使 `P(Z=1 | S verified) ≥ 1 − ε`,ε=0.01。
11. **抢占**:节点关闭时取消所有依赖它的 pending generator tasks。

数学不变式(测试必须覆盖):

- VOI ≥ 0 始终
- `p̂ ∈ [0,1]` 始终
- cluster 传播只能让 `p̂` 单调下降
- 退火 `λ_t` 关于 verified_fraction 单调非增
- verifier 完全无用(`p_tpr = p_fpr`)时 VOI 恒为 0
- `p̂ → 0` 或 `p̂ → 1` 时 VOI → 0

## 与 Lean 4 的迁移

verifier 切到 Lean 时,`(p_tpr, p_fpr) = (1, 0)`,calibration 跳过,bridge 用 `is_def_eq` 替代 LLM judge,其余架构不变。NL 阶段的设计是 Lean 阶段的真子集。

## 默认参数

```
τ_embedding              = 0.65
γ_cluster_decay          = 0.5
k_ensemble               = 3
N_voi_mc                 = 200
isotonic_min_samples     = 30
λ_schedule               = max(0.1, 1 − verified_fraction)
thompson_temp            = 1.0 → 0.2 (linear in verified_fraction)
ε_certify                = 0.01
refute_severity_thresh   = 0.4
```

## 模块清单(实装)

```
rethlas_scoring/
├── __init__.py
├── data.py                  # ProofGraph, Node, VerifierObservation
├── calibration.py           # IsotonicCalibrator, VerifierROC
├── verifier_ensemble.py     # Verifier ABC, EnsembleVerifier, Dawid-Skene
├── voi.py                   # MC VOI estimator, sem_blast
├── cluster.py               # ClusterIndex, 传播
├── bridge.py                # BridgeAudit
├── refute.py                # RefuteTask, prompts
├── scorer.py                # Score, compute_score, Pareto
└── scheduler.py             # 主循环, 退火 λ, 抢占
tests/scoring/               # pytest, 覆盖所有不变式
docs/
├── SCORING_DESIGN.md        # 完整数学设计
├── SCORING_HANDOFF.md       # 本文
├── SCORING_AUDIT.md         # 现状漏洞定位
└── SCORING_INTEGRATION.md   # 集成进 Rethlas-plus 真代码的具体替换点
```
