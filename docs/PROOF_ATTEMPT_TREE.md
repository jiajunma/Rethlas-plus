# PROOF_ATTEMPT_TREE — 详细设计

> 2026-05-07 — 配套 `PROOF_SEARCH_DESIGN.md`(决策理由)。
> 本文是**设计稿**,代码尚未实现。读完拍板再写。

---

## 0. 目标

为 Rethlas 的 NL 证明搜索加显式**回溯机制**,解决 5 个失败模式(`PROOF_SEARCH_DESIGN.md §0`):

- 反复 critical 的节点能换 approach
- 子 lemma 不可证时父节点能换路
- 整个 sub-tree 死路时能向上回溯
- 循环依赖能退到引入处
- 成本超时能切换或升级

**不**是 MCTS。无 UCT,无 rollout,无 value network。只用**树结构 + 显式死亡传播 + LLM 驱动 expansion**。

---

## 1. 两层模型

理解整个设计的关键:**两个不同的"node"**。

### Layer 1 — KB ProofGraph(已有,不改)

DAG of propositions:
```
Node = (label, statement, proof, depends_on, status, ...)
status ∈ {pending, in_progress, verified, refuted}
```

每个 KB Node 表示**一个待证或已证的命题**。这是数学结果的最终账本。

### Layer 2 — AttemptForest(新增)

每个 KB Node 配一棵**尝试树**:
```
AttemptNode = (
  attempt_id,
  target_label,                      -- 指向 KB Node
  parent_attempt_id NULLABLE,        -- 树结构
  approach_signature,                -- generator 自报的策略 tag
  introduced_lemmas: tuple[str, ...],-- 这次新引入的子 KB Node
  status,
  death_reason,
  proof_text NULLABLE,               -- generator 写的 proof
  verifier_history: tuple[Verdict, ...],
  created_ts,
  closed_ts NULLABLE,
)
status ∈ {in_progress, succeeded, dead}
```

每次 generator 跑一遍 = **创建一个 AttemptNode**。AttemptNode 死了(下文定义),同 target 下 spawn 一个**兄弟节点**(新 approach)。

**树是 per-target 的**。不同 target 的尝试树**彼此独立**(只通过 KB DAG 间接耦合 —— 一个 attempt 引入的子 lemma 是另一个 target 的 KB Node,有自己的尝试树)。

### 完整状态 = ProofGraph + AttemptForest

```
ProofGraph:                 KB DAG of propositions
  ├ thm:main
  ├ lem:foo (intro by some attempt)
  └ lem:bar (intro by some attempt)

AttemptForest:               per-target tree of attempts
  thm:main:
    ├ attempt_1 (dead: subgoal lem:foo refuted)
    │
    └ attempt_2 (in_progress)  -- spawned because attempt_1 died
        intro lem:baz
  lem:foo:
    ├ attempt_3 (dead: verifier critical x5)
    └ attempt_4 (dead: refuted by counterexample)
                              -- target lem:foo now "exhausted"
                              -- propagates up to attempt_1's death above
  lem:bar:
    └ attempt_5 (succeeded)
  lem:baz:
    └ attempt_6 (in_progress)  -- generator hasn't returned yet
```

---

## 2. 死亡判定(纯函数,确定性)

```python
def death_reason(node: AttemptNode, kb) -> str | None:
    """Return non-None death reason if this attempt should be marked dead.
    Pure function of (node, kb state) — no side effects."""
    
    # (a) Verifier 反复 critical
    n_critical = sum(1 for v in node.verifier_history if v.verdict == "critical")
    if n_critical >= REPAIR_LIMIT:                      # default 5
        return "verifier_critical_repeated"
    
    # (b) 引入的子 lemma 整棵尝试树死透
    for lem in node.introduced_lemmas:
        if kb.target_exhausted(lem):                    # all attempts dead, no more siblings possible
            return f"introduced_lemma_exhausted:{lem}"
    
    # (c) 引入的子 lemma 被 refuted (有反例)
    for lem in node.introduced_lemmas:
        if kb.status(lem) == "refuted":
            return f"introduced_lemma_refuted:{lem}"
    
    # (d) 循环依赖 —— linter 拒过的同结构 attempt
    if kb.cyclic_with_existing(node):
        return "cyclic_dependency"
    
    # (e) 成本超
    if node.cost_so_far() >= COST_BUDGET_PER_ATTEMPT:   # default $5 / 30 min wall
        return "budget_exhausted"
    
    # 还活着
    return None
```

`REPAIR_LIMIT` / `COST_BUDGET_PER_ATTEMPT` 配置可调。

`target_exhausted(target)` 递归判:
```python
def target_exhausted(target: str, kb) -> bool:
    attempts = kb.attempts_for(target)
    if any(a.status == "succeeded" for a in attempts):
        return False
    if not all(a.status == "dead" for a in attempts):
        return False
    # 所有现存 attempt 都死了 — 还能不能开新兄弟?
    return not kb.can_spawn_alternative(target)
```

`can_spawn_alternative(target)`:
- 该 target 的死 attempt 数 < `MAX_ATTEMPTS_PER_TARGET`(default 8)?
- 总成本 < `TOTAL_BUDGET_PER_TARGET`?
- 上一个失败的 LLM "我已经没新 approach 可提" 信号没出现?

满足全部才允许 spawn。

---

## 3. 调度(纯函数 + 一个 LLM call)

```python
def select_next_action(forest: AttemptForest, kb) -> Action:
    """Decide what to do next. Pure function over current state.
    Returns one of:
      ContinueWith(attempt_id)         -- attempt 还活着,等 verifier 回报
      VerifyAttempt(attempt_id)        -- attempt 写完 proof,该派 verifier
      SpawnSibling(target, parent_attempt_id, avoid: list[approach_signature])
                                       -- 父 attempt 死,需要新 approach
      EscalateToUser(target)           -- 整树死透
    """
    
    # 1. 优先处理 in_progress(generator 写 proof / verifier 验)
    for attempt in forest.in_progress_attempts():
        if attempt.proof_text is None:
            return ContinueWith(attempt.id)         # generator 在跑
        if not attempt.verifier_history:
            return VerifyAttempt(attempt.id)         # 写完了,该 verify
        last = attempt.verifier_history[-1]
        if last.verdict == "accepted" and all_intro_lemmas_succeeded(attempt, kb):
            attempt.status = "succeeded"             # 状态自动收
            propagate_success_upward(attempt, kb)
            continue
        # last verdict 是 critical 或 gap 或 introduced lemma 还没全 ok
        # → 调用 generator 修(同一 attempt,内部 repair)
        return ContinueWith(attempt.id)
    
    # 2. 找需要 spawn 的死 attempt(它们的 parent 还能 spawn 新兄弟)
    for dead in forest.dead_attempts_by_depth_asc():    # 浅的优先
        target = dead.target_label
        if kb.can_spawn_alternative(target):
            siblings = forest.dead_siblings_of(dead)
            return SpawnSibling(
                target=target,
                parent_attempt_id=dead.parent_attempt_id,
                avoid=[s.approach_signature for s in siblings],
            )
    
    # 3. 整棵尝试树死透 → 升级
    for target in kb.exhausted_targets():
        return EscalateToUser(target=target)
    
    # 4. 一个 in_progress 都没,啥事干不了 → idle
    return None
```

**这个函数完全确定性**。LLM 在 `SpawnSibling` 被执行时才被调用(让 generator 写新 approach)。`select_next_action` 本身**不调 LLM**。

---

## 4. Generator 接口扩展

现有 generator 调用:
```
inputs:  target.statement, target.dependencies
outputs: proof_text, sub_lemmas[]
```

新增字段:
```
inputs:
  + dead_siblings: list[(approach_signature, death_reason, proof_excerpt)]
                   -- 已死的兄弟 attempt,prompt 里要求"产出与之显著不同"
  + kb_relevant_lemmas: list[(label, statement)]
                   -- 跟 target embedding 相似的已 verified lemma,允许直接引用
outputs:
  + approach_signature: str  -- generator 自报的策略标签
                              ("induction_on_n" / "contradiction" / 
                               "case_split_on_parity" / "direct_via_X" / ...)
  + introduced_lemmas: list[(label, statement, difficulty, importance)]
                       -- 跟既有 generator 输出一致,仅 difficulty/importance
                       -- 来自 SCORING 设计稿
  + give_up_signal: bool  -- generator 自评 "我已经穷尽思路,这个 target 我搞不定"
                          -- 触发 can_spawn_alternative 返回 False
```

`approach_signature` 的格式:`<technique>[_<key_param>]`,例如:
- `induction_on_n`
- `induction_strong_on_n`
- `contradiction`
- `case_split_parity`
- `direct_via_lem:foo`
- `direct_via_intermediate_value_thm`

generator 自报。LLM 给的,不是固定枚举(避免 NL action 离散化的问题)。

**重复检测靠字符串匹配**:连续 2 次给同一 `approach_signature` → 视为"没新 approach 可提" → 触发 `give_up_signal=true`(系统侧补打)。

---

## 5. KB schema 增量

只加 2 张表(其它字段或事件复用)。

### `AttemptNode` 表

```sql
CREATE NODE TABLE AttemptNode (
    attempt_id        STRING PRIMARY KEY,    -- ULID 风格
    target_label      STRING,                -- 指向 ProofGraph.Node.label
    parent_attempt_id STRING NULLABLE,       -- 树结构,根 attempt 此字段为 NULL
    approach_signature STRING,               -- generator 自报
    introduced_lemmas LIST<STRING>,          -- 子 lemma label 列表
    status            STRING,                -- in_progress / succeeded / dead
    death_reason      STRING DEFAULT '',     -- 见 §2 枚举
    proof_text        STRING DEFAULT '',     -- generator 输出
    verifier_history  STRING DEFAULT '[]',   -- JSON array of verdicts
    cost_estimate     DOUBLE DEFAULT 0.0,    -- 累计 wall+token 估值
    created_ts        STRING,                -- ISO 8601 Z
    closed_ts         STRING NULLABLE
);

CREATE REL TABLE AttemptParent FROM AttemptNode TO AttemptNode;  -- parent → child
CREATE REL TABLE AttemptTargets FROM AttemptNode TO Node;        -- attempt → KB Node
CREATE REL TABLE AttemptIntroduces FROM AttemptNode TO Node;     -- attempt → introduced lemma
```

### `DeadStrategy` 表(prompt-time 索引,可重建)

```sql
CREATE NODE TABLE DeadStrategy (
    target_label       STRING,
    approach_signature STRING,
    failure_reason     STRING,
    failed_attempt_ids LIST<STRING>,
    last_failed_ts     STRING,
    PRIMARY KEY (target_label, approach_signature)
);
```

`DeadStrategy` 是查询优化用的(prompt 组装时一查就拿到全部失败 approach),内容可从 `AttemptNode` 重建,丢了无所谓。

### 现有 `Node` 字段不变

`Node` 不动(已经够用)。AttemptForest 的所有状态在新表里。

---

## 6. 状态机与不变式

### AttemptNode 状态转换图

```
                ┌────── verifier accepted + all sub-lemmas succeeded
                │
                v
      ┌─→ succeeded ──→ (终态)
      │
in_progress
      │
      └─→ dead ───────→ (终态;parent 被通知,可 spawn 兄弟)
                        触发条件:见 §2 death_reason()
```

### 不变式(测试必须覆盖)

- **I-PAT-1**: 一个 AttemptNode 一旦进 `succeeded` 或 `dead`,**永远不变回**
- **I-PAT-2**: 如果 attempt A 的 `introduced_lemmas` 含 X,那么 X 这个 KB Node 必然存在
- **I-PAT-3**: 兄弟 attempt 的 `approach_signature` 互异(同 parent 下不允许重复 approach)
- **I-PAT-4**: 死亡传播是单向的 —— 子 attempt 死不直接死 parent,只是触发 parent spawn 新兄弟;parent 因为"全部 children dead 且 can_spawn_alternative=False"才死
- **I-PAT-5**: `target_exhausted(t)` 单调 —— 一旦 True,后续仍 True(因为 attempt 不会复活)
- **I-PAT-6**: 同 target 下 succeeded attempt 至多 1 个(一旦有 succeeded,target 进 verified 状态,后续 attempt 不再 spawn)

---

## 7. Coordinator 集成

`coordinator/main.py` 新增一段(伪代码):

```python
def _supervise_one_tick(state):
    # ... 既有 verifier dispatch ...
    
    # 新增:每 tick 调一次 ProofAttemptTree 的 select_next_action
    forest = load_attempt_forest(state.ws)              # 从 KB 读
    action = pat.select_next_action(forest, kb=state.kb_view)
    
    match action:
        case ContinueWith(attempt_id):
            # generator 已经在跑,啥也别动
            pass
        case VerifyAttempt(attempt_id):
            # 把 attempt 的 proof 派给 verifier
            dispatch_verifier_for_attempt(state, attempt_id)
        case SpawnSibling(target, parent_attempt_id, avoid):
            # 派一次 generator,带上"avoid these signatures"
            dispatch_generator_for_attempt(
                state, target, parent_attempt_id, avoid
            )
        case EscalateToUser(target):
            mark_target_user_blocked(state, target)
        case None:
            pass  # idle
```

**关键:这层跟 BFS-by-tier dispatcher 完全正交**。
- BFS-by-tier 决定"现成的 KB Node 谁先 verify"(已有功能,不变)
- ProofAttemptTree 决定"卡住的 target 该开新 attempt 吗 / 该升级吗"(新功能)

两者**串行执行**,不抢资源。capacity 各自分:
- `verifier_workers`(既有)给 BFS-by-tier
- `generator_workers`(既有)给 ProofAttemptTree spawn

---

## 8. 跟现有 Generator/Verifier 契约的契合点

### Generator role(`generator/role.py`)改造

Phase 1(本次):
- 接受新 inputs(`dead_siblings`, `kb_relevant_lemmas`)
- 在 prompt 模板里 surface 这些 context
- 输出加 `approach_signature` + `give_up_signal` 字段
- decoder 解析这些字段,缺省时打 placeholder

Phase 2(后续):
- 接受 `parent_attempt_id`,拿到 parent 的中间状态(已经 introduced 哪些 lemma)避免重复
- 实现 `SpawnSibling.avoid` 的"显著不同"硬约束(prompt 里强调)

### Verifier role(`verifier/role.py`)改造

**几乎不改**。只需:
- verdict 输出加 `confidence` 字段(SCORING 设计稿已定)
- 把 verdict 写进 attempt 的 `verifier_history` 而不只是 KB Node 字段

KB 写入流程:
```
verifier completes attempt A on target T
  ↓
projector applies verdict to AttemptNode A:
  A.verifier_history.append(verdict)
  if verdict.accepted and all sub-lemmas succeeded:
    A.status = succeeded
    T.status = verified  (KB Node 状态)
  else if verdict.critical and A.repair_count > REPAIR_LIMIT:
    A.status = dead, A.death_reason = "verifier_critical_repeated"
  else:
    A stays in_progress; generator gets called again with repair_hint
```

---

## 9. 边界情况

### 9.1 cyclic dependency

generator 在 attempt A(target T)中引入子 lemma X,但 X 在 KB 里已经依赖 T。

**当前**:linter 拒绝,attempt A 的 publish 失败。
**新**:`death_reason()` 检测到 `kb.cyclic_with_existing(A)` → A 立刻死 → spawn 兄弟,prompt 里告诉 LLM "X 跟 T 循环,别用 X"。

### 9.2 partial succeed(子 lemma 部分通过)

attempt A introduces (X, Y, Z)。X 已 verified,Y 已 verified,Z 还在 attempt 中。

**当前**:A 卡在 `deps_blocked`。
**新**:A 仍 `in_progress`,wait Z's attempt forest;Z 死透时 A 自动死。Z 成功时,A 进入 verifier 阶段(verify A.proof)。

### 9.3 parent 已经死了之后子 attempt 还在跑

attempt A introduced 子 lemma Y,Y 在跑 attempt B,但 A 因为别的原因(如别的子 lemma 死透)已经死。

**策略**:**保留** B 继续跑(它产出的 verified lemma 进 KB,以后别的 target 可能用)。**不抢占**,因为 generator 已花成本。

例外:如果 cost budget 紧张(运行环境配 `aggressive_cancel: true`),才取消 B。

### 9.4 LLM proposes the same approach signature again

generator 第二次 spawn 时,LLM 给的 `approach_signature` 跟 `avoid` 列表里某个相同。

**策略**:dispatch 拒绝写入 `AttemptNode`(违反 I-PAT-3),自动重派一次 generator,prompt 里加更强的"必须不同"指令。重派 3 次仍重复 → 标记 `can_spawn_alternative=False` → target_exhausted。

### 9.5 succeeded attempt 后又有人重新 verify 出 critical

不太可能(verified 后 KB Node statement_hash 不再变),但理论上 verifier 模型升级 + 重 audit 可能翻案。

**策略**:**out of scope** for v1。treat verified as final;翻案需要人审 + 显式 KB rewrite。

---

## 10. 算力 / 预算预估

### 单 target 最坏情况

- `MAX_ATTEMPTS_PER_TARGET` = 8
- 每 attempt 平均 2 次 generator(初次 + 1 次 repair)+ 3 次 verifier
- 每次 generator/verifier ≈ 30s + $0.20
- 单 target 上限 ≈ 8 × 5 × 30s = 20 分钟,$8

可接受。

### 整证明

100 个 target,假设 80% 一次过,15% 两次过,5% 卡需要 attempt forest:
- 一次过:100 × 0.8 × 5 × 30s = 200 分钟
- 两次过:100 × 0.15 × 10 × 30s = 75 分钟
- 卡的:5 × 20 分钟 = 100 分钟
- 总:~6 小时,$50

跟当前 generator 死循环跑一晚上 vs 这个能定界,**改善明显**。

---

## 11. 实施步骤

每个 commit 独立可 revert。

### S0 — 拆包袱(已拍未做)

- 删 `rethlas_scoring/{policy,refute,bridge}.py` + `refute/` + 对应 tests
- 改 `coordinator/main.py` 移除 `_evidence_from_candidate / _action_for_candidate` + dispatch site policy check
- 删 `common/config/loader.py` 的 `policy_max_refute_per_node / policy_max_strong_per_node / strong_verifier_model_id`
- 把 `rethlas_mcts/` 改名 `proof_attempt_tree/`(空 `__init__.py`)

### S1 — `proof_attempt_tree/` 核心(纯算法)

- `__init__.py`
- `data.py` — `AttemptNode` dataclass + `Action` 枚举
- `death.py` — `death_reason(node, kb)` 纯函数 + helpers
- `select.py` — `select_next_action(forest, kb)` 纯函数
- `forest.py` — `AttemptForest` in-memory 操作 + transposition
- `tests/proof_attempt_tree/test_*.py` — 不变式 I-PAT-1..6 + 死亡传播 + 兄弟 spawn 测试

**KB-agnostic** —— 通过 Protocol(`KBLookup`)注入 KB 查询能力。

### S2 — KB schema + librarian

- `common/kb/types.py` 加 `AttemptNode` dataclass
- `common/kb/kuzu_backend.py` 加 2 张表的 schema + DDL
- `librarian/projector.py` apply `attempt_*` 事件类型
- 测试:KB 写入 + 读出 + transposition

### S3 — Generator schema 扩展

- `generator/decoder.py` parse `approach_signature` + `give_up_signal` + `difficulty` + `importance`(都 default safe)
- `generator/prompt.py` 接 `dead_siblings` + `kb_relevant_lemmas` 注入
- `generator/role.py` 把这些 context 取出来传 prompt
- 测试:decoder 容错 + prompt 渲染

### S4 — Verifier schema 扩展(并行)

- `verifier/decoder.py` parse `confidence`(default 0.8)
- 测试

### S5 — Coordinator 集成

- `coordinator/main.py` 加每 tick 调 `select_next_action` 一次
- 派 generator / verifier 时附带 attempt context
- `verifier_callback` 写 attempt 的 `verifier_history`
- 测试:端到端 happy path + 死亡传播

### S6 — 生产开关

- `[scheduling] use_proof_attempt_tree = false`(默认 off,回滚保险)
- 文档 `PROOF_ATTEMPT_TREE_INTEGRATION.md` 记录 wiring + rollback

---

## 12. 不做的事(明确划界)

- **MCTS 的 UCT** —— 不做
- **MCTS 的 rollout** —— 不做
- **Value network / policy network** —— 不做
- **Strategy embedding 自动学** —— 不做(approach_signature 由 LLM 自报,字符串匹配就够)
- **跨 target 的 attempt 共享**(transposition table) —— 不做(每 target 独立树,`approach_signature` 同字符串复用是隐式 transposition,够了)
- **Refute / strong verifier** —— 不做(已废)
- **抢占已经在跑的子 attempt** —— v1 默认不抢占(见 §9.3)

---

## 13. 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM 总是给同一 `approach_signature`(没创意) | spawn 重派 3 次后强制 `give_up`,target 升级 |
| `MAX_ATTEMPTS_PER_TARGET` 调太低 → 过早升级 | 配置可调,生产观察后再 tune |
| `MAX_ATTEMPTS_PER_TARGET` 调太高 → 浪费成本 | 同上 + 总预算上限二次保险 |
| KB schema 改动影响现有 phase II 测试 | S2 单独 commit,跑全测试再下一步;`AttemptNode` 表新建,Node 表不动 |
| 死亡传播算法 bug → 整棵树假死 / 假活 | I-PAT-1..6 不变式测试 + property-based 测试覆盖 |
| Forest 长太大(深度 > 100)栈溢 | depth-bound 限制(`MAX_TREE_DEPTH=20`),超过强制升级 |

---

## 14. 待你拍

1. **整体方向**:ProofAttemptTree(无 UCT 无 rollout 的树搜索) 你认吗?
2. **MAX_ATTEMPTS_PER_TARGET 默认 8** —— 偏多 / 偏少?
3. **REPAIR_LIMIT 默认 5** —— 单 attempt 内 generator + verifier 来回 5 次仍 critical 才标死。OK?
4. **`approach_signature` 由 LLM 自报字符串** —— 你接受这个"非离散 action"的折中吗?
5. **抢占策略 §9.3**(parent 死后子 attempt 仍跑) —— OK 还是要 aggressive cancel?
6. **实施次序 §11(S0..S6)** —— 顺序对吗?要重排?

确认后开 S0 → S1。
