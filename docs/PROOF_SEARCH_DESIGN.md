# PROOF_SEARCH_DESIGN — proof exploration architecture decision

> 2026-05-07 — design analysis comparing **full MCTS**,
> **pure stuck-detection**, and **ProofAttemptTree**(中间路径)。
> 详细设计在 `PROOF_ATTEMPT_TREE.md`。本文件记录决策理由。

---

## 0. 问题陈述

主体是**自然语言数学证明**,verifier 是 LLM 判 `accepted | gap | critical`,generator 是 codex agent 写 blueprint。当前 Rethlas:

- generator 写完整 blueprint(可能含子 lemma)
- verifier 验,若 critical → generator 拿 `repair_hint` 改同一节点
- `repair_count++`,无限循环直到 ok 或人审

**已知失效场景**:

| # | 失败模式 | 当前行为 |
|---|---|---|
| (a) | 单节点 verifier 反复 critical | `repair_count` 无界增长,死循环 |
| (b) | 子 lemma 实际是错的(refuted) | 子 lemma 进 refuted,但**父节点不知道要换路** |
| (c) | 整个 sub-tree 死路(approach A 的 L1/L2/L3 全黄) | 父节点僵在 `deps_blocked`,等永远 |
| (d) | 循环依赖被发现 | linter 拒,但**没机制回到上层换不引入它** |
| (e) | 成本超时间超 | 没机制 |

需要的是**显式的退出机制 + 备选策略发现**。

---

## 1. 三个候选架构

### Option α — 完整 MCTS

经典 Monte Carlo Tree Search:
- Root = 待证目标
- 每个节点 = 一个 proof state
- Action = 策略选择(归纳 / 反证 / 引子 lemma X / ...)
- UCT 选 action,rollout 估值,backprop 统计
- 通常配 policy network + value network(AlphaZero 风格)

**适用场景**:
- 离散动作空间(围棋、Lean tactic)
- 廉价 rollout(µs 级随机 play)
- 有学过的 policy/value 先验

### Option β — 纯 stuck-detection + KB-aware prompting

- 检测 `repair_count > 阈值` 的节点
- 给 generator prompt 加"过去失败记录" + "KB 里相关已验 lemma"
- LLM 自己决定换花样
- **无显式树结构**

**适用场景**:
- LLM 自己有强 meta-cognition
- 失败模式简单(主要是 (a))

### Option γ — ProofAttemptTree(中间路径)

- 每个 KB Node 配一棵**尝试树**(attempt forest)
- 每次 generator 调用 = 一个 AttemptNode
- AttemptNode 死了 → 同 target 下 spawn 兄弟节点(不同 approach)
- 死亡显式向上传播
- **无 UCT,无 rollout**:LLM 决定 expand,树只做 bookkeeping + backtracking

**适用场景**:
- LLM 擅长 expansion(写策略)
- 系统需要显式"已死"记录(LLM 自己看不到)
- 需要回溯到祖先换路

---

## 2. 评估三者

### MCTS 在 NL 证明域为什么不划算

| 维度 | MCTS 强项 | NL 证明现实 |
|---|---|---|
| Action 离散性 | 围棋 move、Lean tactic 可哈希枚举 | "用归纳" vs "用强归纳" vs "对次要变量归纳"是连续语义,**枚举不出干净 action 集** |
| Rollout 廉价 | µs 级随机 play | 每个 rollout = generator + verifier ≈ 30s + $$,**统计平均效应消失** |
| Policy/value prior | AlphaZero 用 NN | 我们没 NN;LLM 是黑箱;唯一的"prior"是 KB 检索 |
| 内部 vs 外部搜索 | 简单 board state,需要外部搜索剪枝 | codex agent **prompt 内部已经在搜索策略**,套 MCTS 是双层搜索 |
| 状态空间 | 围棋 10^170 | 单 proof 的策略空间通常 ~10 种,根本不需要树搜索做 pruning |

**关键观察**:DeepMind AlphaProof 用 MCTS 是因为它跑在 **Lean**(真离散 tactic + 学过的 policy/value net)上。我们既无 Lean 也无 NN,**MCTS 的两个核心引擎都不在**。

**结论**:Option α **不要**。

### 纯 stuck-detection 为什么不够

只解决了 (a) —— 单节点反复失败。

剩下 (b)/(c)/(d)/(e) 都需要**树状回溯**:
- (b) 子 lemma 是错的 → 父节点要知道这条死路 → 退到上层换 approach
- (c) 整个 sub-tree 死路 → 同上,递归
- (d) 循环依赖 → 退到引入这个依赖的 attempt,标死,spawn 兄弟
- (e) 成本超 → 标死当前 attempt,spawn 不同 approach 或升级

LLM 看不到自己跨多次调用的失败历史(每次 prompt context 重置),没机制说"approach A 试 5 次都黄,我换 B"。**必须**有外部数据结构记账。

**结论**:Option β **不够**。

### ProofAttemptTree 的设计取舍

把 MCTS 拆成两件:

- **(i) UCT + rollout + value network** —— **跳过**(NL 域不适用)
- **(ii) 树结构 + 显式回溯** —— **要**

留 (ii) 即可:
- 树负责"已经试过 / 已经死掉"的 bookkeeping
- LLM 决定怎么 expand(它擅长的)
- 简单选择规则代替 UCT(BFS / DFS / 深度阈值)
- 评估直接用 verifier verdict,不做 random rollout

**结论**:Option γ **采用**。

---

## 3. ProofAttemptTree 跟完整 MCTS 对比

| | ProofAttemptTree | 完整 MCTS |
|---|---|---|
| 树结构 | ✓ | ✓ |
| 显式回溯 / 死亡传播 | ✓(显式) | ✓(隐式,通过 visit count) |
| UCT exploration bonus | ✗ | ✓ |
| Rollout 估值 | ✗ —— 直接用 verifier verdict | ✓ |
| Value network | ✗ | ✓ |
| LLM 角色 | 决定 expand 的内容 | 仅做 simulation 输出 |
| 代码量 | ~200 行 + tests | ~500 行 + tests + 调参 |
| KB 表加几张 | 2(`AttemptNode` + `DeadStrategy`) | 5(+ `DeadEnd / Counterexample / ActionStats / MCTSNode`) |
| 跟现有 generator/verifier 契约 | 自然衔接(攻击点 = generator 调用一次 = 一个 AttemptNode) | 别扭(强行离散化 + 引入 MCTSCoordinator 新层) |

---

## 4. 决策

**采用 Option γ — ProofAttemptTree**。

详细设计:`PROOF_ATTEMPT_TREE.md`。

---

## 5. 已落地代码 vs 决策

会话过程中,在拍板 Option γ 之前,我已经创建了:

- `rethlas_mcts/__init__.py`(本节会话内)

**待决**:
- 删除该文件(走纯 ProofAttemptTree 路线)
- 留着改名 `proof_attempt_tree/`(包名改,内容重写)
- 留着当 future option(默认不集成)

倾向**改名 + 重写**(空 `__init__.py` 没多少残留代码,改名零成本)。

---

## 6. 历史包袱清算

会话过程中曾经讨论过但已**作废**的设计元素:

| 元素 | 作废理由 |
|---|---|
| L5 policy state machine(`Evidence / classify / next_action`) | 用户拍板"回到原架构,no L5 policy" |
| Refute role(`refute/`)+ refute prompt | 用户拍板"refute 跟 verifier 同源,信号不增量" |
| Bridge audit(`rethlas_scoring/bridge.py`) | 依赖 refute,顺带作废 |
| Strong verifier role | 同 refute |
| Ensemble k=3 dispatch(S7-full) | 用户拍板"不要,浪费 worker 跟 quota" |
| Audit-sampling 5% | 用户拍板"证明对就是对错就是错,不是概率问题" |
| MCTS over verification scheduling | 见本文 §1-2 分析 |

**保留**:
- BFS-by-tier dispatcher(S1)
- 打分调度公式(`importance × uncertainty × (1+blast)`,带 difficulty 进 prior + 进预算)
- 嵌入 + cluster propagation(S6 系列)
- VOI math 模块(留作可选维度,目前不接 dispatcher)

**新增**:
- ProofAttemptTree(本决策)

---

## 7. 实施次序

1. 写 `PROOF_ATTEMPT_TREE.md` —— 详细设计稿
2. **拍板** —— 你看完决定改不改
3. 拆 L5 policy stack(用户已拍但未执行)+ 改 `rethlas_mcts/` → `proof_attempt_tree/`
4. 实现 `proof_attempt_tree/` 核心算法 + 测试
5. KB schema 加 2 张表
6. coordinator + generator 集成

每步独立 commit + push。
