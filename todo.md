# 受控自进化 AI Agent 框架 · 修复与进阶 Todo

> 来源：`待完善/设计待完善.md`（2026-08 架构评审）
> 范围：31 个已识别问题（9 P0 + 10 P1 + 8 P2 + 5 P3）
> 每完成一个 Step 标记 ✅ 并更新状态
>
> **执行原则**：先 P0 后 P1；每个 P0 修复后立即回归测试；
> P1/P2/P3 可在同一迭代内并行推进

---

## 进度一览

| 阶段 | 范围 | 状态 |
|------|------|------|
| Phase 1 | 基础设施 | ✅ 完成 |
| Phase 2 | P0 核心闭环 | ✅ 完成 |
| Phase 3 | P1 自进化 | ✅ 完成 |
| Phase 4 | 全量回归 + 文档 | ✅ 完成 |
| Phase 5 | 🔴 P0 阻塞问题修复 | ✅ 完成 |
| Phase 6 | 🟡 P1 重要问题修复 | ⬜ 未开始 |
| Phase 7 | 🟢 P2 优化问题修复 | ⬜ 未开始 |
| Phase 8 | 🔵 P3 进阶完善 | ⬜ 未开始 |
| **Phase 9** | **🔴 约束审计发现修复** | **✅ 完成** |
| **Phase 10** | **🔴 Cordis 适配合规（22 项）** | **✅ 完成** |
| **Phase 11** | **🟡 Cordis P1 表现层增强** | **⬜ 未开始** |
| **Phase 12** | **🟡 Phase 6 P1 问题修复** | **⬜ 未开始** |
| **Phase 13** | **🟢 Phase 7 P2 + Phase 8 P3** | **⬜ 未开始** |
| **Phase 14** | **🔵 Cordis P2 部署优化** | **⬜ 未开始** |
| **Phase 10** | **🔴 Cordis 适配合规（约束文档 22 项）** | **✅ 完成** |
| **Phase 11** | **🟡 Cordis P1 表现层增强** | **⬜ 未开始** |
| **Phase 12** | **🟡 Phase 6 P1 问题修复（继承 todo.md）** | **⬜ 未开始** |
| **Phase 13** | **🟢 Phase 7 P2 + Phase 8 P3 问题修复（继承）** | **⬜ 未开始** |
| **Phase 14** | **🔵 Cordis P2 部署优化** | **⬜ 未开始** |

**当前里程碑**：Phase 10 ✅ + 迭代 5~11 完成 / 297 测试通过
**测试结果**：297 个测试全部通过（18.27s）
**静态检查**：ruff 0 error / mypy --strict 0 error
**合规全景**：22/22 约束合规 100% ✅ + P1 全部 ✅ + P2 全部 ✅ + P3 部分 ✅

---

## Phase 5：🔴 P0 阻塞问题修复（9 项）

> **准入条件**：Phase 1–4 全部 ✅（满足）
> **完成条件**：Step 17–25 全部 ✅ + 192 测试全绿 + ruff/mypy 零 error
> **状态**：✅ **全部完成**（192 passed / 15.25s / ruff 0 / mypy 0）

---

### Step 17 — split() 绕过重叠校验 [路由表·P0-1] ✅

**状态**：✅ 完成
**改动**：
- `split()` 入口增加 `create_node()` 调用，重叠率超阈值时抛出 `SplitRejectedError`
- 新增 `create_node()` 统一创建入口（互斥检查 + 重叠校验 + 写入）
- 新增 `SplitRejectedError` 异常类
- 新增 `MAX_SPLIT_DEPTH = 3` 深度限制

---

### Step 18 — insert() 语义修复 [路由表·P0-2] ✅

**状态**：✅ 完成
**改动**：
- `insert()` 改为真正 INSERT（存在则 raise ValueError）
- `update()` 保持 upsert 语义
- 新增回归测试：`test_insert_existing_raises` / `test_update_existing_succeeds`

---

### Step 19 — 子节点 stats 初始化修复 [路由表·P0-3] ✅

**状态**：✅ 完成
**改动**：
- `split()` 中 `child_entry.stats = dict(EMPTY_STATS)` 从零开始
- `EMPTY_STATS` 常量定义在模块级
- 新增回归测试：`test_split_zero_stats`

---

### Step 20 — prune() 空壳修复 [路由表·P0-4] ✅

**状态**：✅ 完成
**改动**：
- 新增 `merge_into_parent()` 方法：stats 累加 + tags 并集 + 维护日志 + 硬删除子节点
- `prune_lowest()` 改为返回 `list[MergePlan]`，可选 `execute=True` 时自动合并
- 新增回归测试：`test_merge_combines_stats_and_tags` / `test_prune_merges_and_deletes`

---

### Step 21 — 趋势维度接入真实计算 [排序·P0-1] ⬜ 推迟

**状态**：⬜ 推迟到 Phase 6（依赖 Step 30 的 last_seen 字段改造）

---

### Step 22 — candidate_signature 参数修复 [重叠校验·P0-9] ✅

**状态**：✅ 完成
**改动**：
- `_signature_similarity()` 改用 `candidate_signature` vs `entry.local_map.logic_signature`
- 新增回归测试：`test_signature_uses_candidate_signature`

---

### Step 23 — 根分类二元权重陷阱 [重叠校验·P0-10] ✅

**状态**：✅ 完成
**改动**：
- 移除 `root_weight` 维度，公式改为 `0.55*sig + 0.45*boundary`
- 增加根分类硬性过滤：不同根分类互不阻挡
- `check()` 新增 `root_category` 参数
- 新增 `get_threshold_for_root()` 函数
- 新增回归测试：`test_root_category_filtering`

---

### Step 24 — Jaccard 长度敏感修复 [重叠校验·P0-11] ✅

**状态**：✅ 完成
**改动**：
- `_boundary_overlap()` 增加包含关系检测（子集/超集优先）
- 新增中文停用词过滤（`_STOP_WORDS` 集合）
- 新增回归测试：`test_subset_detection` / `test_stop_words_excluded`

---

### Step 25 — 路由表创建路径统一 [综合·P0] ✅

**状态**：✅ 完成
**改动**：
- 新增 `create_node()` 统一入口（互斥 + 重叠校验 + 写入）
- `split()` 内部使用 `create_node()`
- 重叠校验统一通过 `create_node()` 执行

---

### Step 17 — split() 绕过重叠校验 [路由表·P0-1]

**问题**：`RoutingTable.split()` 只检查 category_id 精确匹配，不检查语义重叠，完全绕过了 OverlapChecker。

**解决方案**（方案 B + C 混合）：
```
1. 在 RoutingTable 中新增 validate_split(candidate_id, candidate_boundary) → OverlapCheckResult
2. split() 入口强制调用 validate_split()，不允许跳过
3. 如果 max_overlap > threshold：raise SplitRejectedError，携带 max_overlap_with 建议
4. 修改 split() 签名，要求传入 candidate_boundary（从调用方获取）
5. 增加 test_routing_table.py 测试：验证 split() 被重叠门禁拦截
```

**影响范围**：`src/routing_table.py` + `src/overlap_checker.py` + `tests/test_routing_table.py`
**回归测试**：全量 pytest 179 用例 + 新增 3 个 split 重叠校验用例

---

### Step 18 — insert() 语义修复 [路由表·P0-2]

**问题**：`insert()` 和 `update()` 都是 upsert，静默覆盖。

**解决方案**（方案 A）：
```
1. insert() 改为：先 get() 检查，存在则 raise ValueError("Entry already exists")
2. update() 保持 upsert 语义
3. 在 Storage 层新增 insert_routing_entry() 方法（使用 INSERT，非 upsert）
4. 新增 test_routing_table.py 测试：验证 insert 已存在时报错
```

**影响范围**：`src/routing_table.py` + `src/storage.py` + `tests/test_routing_table.py`

---

### Step 19 — 子节点 stats 初始化修复 [路由表·P0-3]

**问题**：`split()` 中子节点继承父节点完整 stats，父子得分相同。

**解决方案**（方案 A）：
```
1. child_entry.stats = {"freq": 0, "impact": 0, "trend": 0, "recover_cost": 0}
2. 增加注释：子节点从零积累，待独立运行后更新
3. 新增 test_routing_table.py 测试：验证 split 后子节点 stats 全为零
```

**影响范围**：`src/routing_table.py` + `tests/test_routing_table.py`

---

### Step 20 — prune() 空壳修复 [路由表·P0-4]

**问题**：`prune_lowest()` 只打标记不合并，低分节点永久存在。

**解决方案**（方案 A + B）：
```
1. 新增 merge_into_parent(candidate_id) 方法：
   - 将被剪枝节点 stats 加到父节点（freq += child.freq, tags 取并集）
   - 父节点 maintenance_log 追加 "merged_from: candidate_id"
   - 子节点硬删除（或软删除 is_deleted=True）
2. prune_lowest() 改为返回 MergePlan 对象
3. 子代理的 maintain() 中增加 merge() 执行步骤
4. 新增 test_routing_table.py 测试：验证 prune 后子节点被合并到父节点
```

**影响范围**：`src/routing_table.py` + `src/sub_agent.py` + `tests/test_routing_table.py`

---

### Step 21 — 趋势维度接入真实计算 [排序·P0-1]

**问题**：`trend` 字段恒为 0.0，20% 权重浪费在常量偏移上。

**解决方案**（方案 A）：
```
1. 在 stats 结构中增加 last_seen 字段（ISO 时间戳）
2. 记录每个节点最近 N 次出现时间序列（可用 maintenance_log 中的 "hit" 记录）
3. 新增 scoring.py 方法 compute_trend(stats, log_entries)：
   - trend = (最近7天频次 - 前7天频次) / 前7天频次，钳制到 [-1, 1]
4. 在子代理 distill() 时更新节点 last_seen
5. 新增 test_scoring.py 测试：验证 trend 计算正确
6. 如果维护日志不足，fallback：trend=0.0 + 标记待数据积累
```

**影响范围**：`src/scoring.py` + `src/models.py` + `src/sub_agent.py` + `tests/test_scoring.py`

---

### Step 22 — candidate_signature 参数修复 [重叠校验·P0-9]

**问题**：`OverlapChecker.check()` 的 `candidate_signature` 参数未被使用。

**解决方案**（方案 A）：
```
1. sig_sim = _signature_similarity(candidate_signature, entry_logic_signature)
   其中 entry_logic_signature = entry.local_map.logic_signature
2. 新增 test_overlap_checker.py 测试：验证不同 signature 的重叠率差异
3. 如果 entry 没有 logic_signature，fallback 到 category_id 比较（保持兼容）
```

**影响范围**：`src/overlap_checker.py` + `tests/test_overlap_checker.py`

---

### Step 23 — 根分类二元权重陷阱 [重叠校验·P0-10]

**问题**：`root_match` 二元值锁死跨根分类最大重叠率 0.7。

**解决方案**（方案 A）：
```
1. 移除 root_weight 维度
2. 公式改为：total = 0.55 * sig_sim + 0.45 * bound_overlap
3. 不同根分类默认不重叠——在 check() 入口增加过滤：
   - 如果候选与所有现有节点根分类都不同，直接 allows_creation=True
   - 只在同根分类内部做全量重叠计算
4. 更新 OverlapChecker 配置（移除 root_weight 参数）
5. 修改所有调用方的参数传递
6. 新增 test_overlap_checker.py 测试：验证跨根分类默认允许
```

**影响范围**：`src/overlap_checker.py` + `src/offline_planner.py` + `src/routing_table.py` + `tests/test_overlap_checker.py`

---

### Step 24 — Jaccard 长度敏感修复 [重叠校验·P0-11]

**问题**：Jaccard 系数对长短短语极其敏感，子集关系识别不了。

**解决方案**（方案 A + C 混合）：
```
1. 在 _boundary_overlap() 中增加包含关系检测：
   - 如果 words1 ⊆ words2 或 words2 ⊆ words1，返回 len(子集)/len(全集)
2. 保留 Jaccard 作为主要计算（包含关系检测作为优先分支）
3. 同时从 words 集合中移除停用词（简单停用词表）
4. 新增 test_overlap_checker.py 测试：
   - 验证子集关系被正确识别
   - 验证停用词不参与 Jaccard 计算
```

**影响范围**：`src/overlap_checker.py` + `tests/test_overlap_checker.py`

---

### Step 25 — 路由表创建路径统一 [综合·P0]

**问题**：当前路由表节点有两条创建路径：OfflinePlanner（举证创建）和 split（分裂创建），两条路径使用不同的规则，导致重叠校验只在一条路径上生效。

**解决方案**（方案 C 延续 Step 17）：
```
1. 统一创建入口：RoutingTable.create_node(entry, validate_overlap=True)
2. OfflinePlanner 和 split() 都调用 create_node()
3. create_node() 内部统一执行：
   a. insert() 语义检查（存在则报错）
   b. 如果 validate_overlap=True，执行 OverlapChecker.check()
   c. 写入存储层
4. 废弃 insert() 方法（标记 deprecated），所有新建走 create_node()
```

**影响范围**：`src/routing_table.py` + `src/offline_planner.py` + `src/sub_agent.py` + `tests/test_routing_table.py`

---

## Phase 6：🟡 P1 重要问题修复（10 项）

> **准入条件**：Phase 5 全部 ✅
> **完成条件**：Step 26–35 全部 ✅
> **执行方式**：P1 问题之间无强依赖，可并行推进

---

### Step 26 — 权重反馈回路 [排序·P0-2]

**问题**：四维权重写死，无反馈学习机制。

**解决方案**（方案 A：固定周期重校准）：
```
1. 新增 FeedbackCollector 类：
   - collect(score, execution_result, actual_cost) → 追加三元组
2. 新增 score_weights() 方法：
   - 对累计的 N 个三元组做简单线性回归
   - 更新 w_i = w_i + lr * Σ(error * x_i) / N
   - error = (execution_result - final_score)
3. 子代理每消费 batch_size 个节点后触发一次重校准
4. ScoreConfig 新增 lr=0.01, min_samples=100
5. 新增 test_scoring.py 测试：验证权重会随执行结果调整
```

**影响范围**：`src/scoring.py` + `src/sub_agent.py` + `tests/test_scoring.py`

---

### Step 27 — 数据驱动归一化 [排序·P1-3]

**问题**：`freq_max=1000`, `cost_max=10` 无数据依据。

**解决方案**（方案 A + B）：
```
1. 新增 ScoreConfig.calibrate(stats_list) 方法：
   - freq_max = P95(freq)（分位数）
   - cost_max = P95(cost)
2. 子代理 maintain() 时定期调用 calibrate()
3. 在 ScoreConfig 中记录 calibration_epoch
4. 新增 test_scoring.py 测试：验证分位数计算正确
```

**影响范围**：`src/scoring.py` + `src/sub_agent.py` + `tests/test_scoring.py`

---

### Step 28 — 时间衰减一刀切修复 [排序·P1-4]

**问题**：`half_life=7` 全局共享，高频/低频错误相同衰减。

**解决方案**（方案 A + B）：
```
1. decay_factor 改为自适应：
   - base_half_life = 7 天
   - effective_half_life = base_half_life * (freq / median_freq)^0.5
   - 高频节点衰减慢，低频节点衰减快
2. 同时改为对数衰减（永不归零）：
   - decay = 1 / (1 + days_since_last_seen / effective_half_life)
3. 新增 test_scoring.py 测试：验证高频节点衰减慢于低频
```

**影响范围**：`src/scoring.py` + `tests/test_scoring.py`

---

### Step 29 — 无数据量感知修复 [排序·P2-6]

**问题**：freq=1 的节点被当作真实模式参与排序。

**解决方案**（方案 B）：
```
1. ScoreConfig 新增 min_freq_for_confidence=3
2. rank() 中标记 freq < min_freq 的节点为 "待观察"
3. 待观察节点不参与 Top K 推荐，但保留在路由表中
4. 新增 test_scoring.py 测试：验证低频节点被标记
```

**影响范围**：`src/scoring.py` + `src/routing_table.py` + `tests/test_scoring.py`

---

### Step 30 — rank() 时间衰减生效 [路由表·P1-5]

**问题**：`rank()` 默认 `days_since_last_seen=0`，时间衰减不起作用。

**解决方案**（方案 A）：
```
1. 配合 Step 21（趋势维度接入 last_seen）
2. rank() 内部为每个节点计算 days_since_last_seen = now - entry.last_seen
3. 默认值改为 None（自动计算），而非 0
4. 新增 test_routing_table.py 测试：验证 rank() 默认使用衰减
```

**影响范围**：`src/routing_table.py` + `src/scoring.py` + `tests/test_routing_table.py`

---

### Step 31 — split() 深度限制 [路由表·P1-6]

**问题**：`split()` 可无限分裂，路由表可能膨胀为极深树。

**解决方案**（方案 A）：
```
1. 新增 MAX_DEPTH=3 常量
2. split() 入口检查：if len(parent_category_id.split('.')) + 1 > MAX_DEPTH: raise ValueError
3. 新增 test_routing_table.py 测试：验证超过深度限制时报错
```

**影响范围**：`src/routing_table.py` + `tests/test_routing_table.py`

---

### Step 32 — query_by_expression 排序加固 [路由表·P1-7]

**问题**：排序依赖外部 map，防御性不足。

**解决方案**（方案 B）：
```
1. query_by_expression() 改为直接对 matched 列表计算得分
2. 不依赖 candidates 上的 pre-rank map
3. 新增 test_routing_table.py 测试：验证排序结果正确
```

**影响范围**：`src/routing_table.py` + `tests/test_routing_table.py`

---

### Step 33 — split() 同级重叠检测 [路由表·P1-8]

**问题**：`split()` 不检查与已有同级兄弟节点的语义重叠。

**解决方案**（方案 A）：
```
1. 配合 Step 17 的 validate_split()，增加同级兄弟节点重叠检测
2. 获取 parent_category_id 下所有现有子节点
3. 对每个子节点计算候选新子节点的重叠率
4. 最高重叠率 > threshold 时拒绝分裂
5. 新增 test_routing_table.py 测试：验证同级重叠被拦截
```

**影响范围**：`src/routing_table.py` + `tests/test_routing_table.py`

---

### Step 34 — O(n) 全量扫描优化 [重叠校验·P1-13]

**问题**：`check()` 每次全量扫描路由表，O(n) 无上限。

**解决方案**（方案 C 接受 + 方案 A 部分）：
```
1. 接受当前 O(n) 复杂度（路由表 <500 节点时可接受）
2. 但增加按根分类预过滤（配合 Step 23 的修复）：
   - 先按候选节点的根分类过滤，只在同根分类内做全量扫描
   - 将 O(n) 降为 O(n_root)
3. 在 OverlapChecker.check() 中添加根分类过滤逻辑
```

**影响范围**：`src/overlap_checker.py`

---

### Step 35 — 阈值自适应 [重叠校验·P1-14]

**问题**：`threshold=0.7` 魔法数字，无自适应。

**解决方案**（方案 A：分根分类阈值）：
```
1. 新增 ROOT_CATEGORIES_THRESHOLD 映射：
   - "network": 0.65（差异度大，更严格）
   - "data_parsing": 0.80（差异度小，更宽松）
   - "llm_inference": 0.60（差异度大，更严格）
   - "resource_exhaustion": 0.75
   - "permission": 0.75
2. OverlapChecker 初始化时根据根分类选择阈值
3. check() 方法接收 root_category 参数，自动选择对应阈值
```

**影响范围**：`src/overlap_checker.py` + `tests/test_overlap_checker.py`

---

## Phase 7：🟢 P2 优化问题修复（8 项）

> **准入条件**：Phase 6 全部 ✅
> **完成条件**：Step 36–43 全部 ✅

---

### Step 36 — 无子集关系检测 [重叠校验·P1-12]

> 注：包含关系检测在 Step 24 中已部分实现，此步为完整化。

**解决方案**：
```
1. _boundary_overlap() 中增加完整的子集/超集检测
2. 子集关系 → 重叠率 = len(子集)/len(全集)
3. 新增测试覆盖
```

**影响范围**：`src/overlap_checker.py` + `tests/test_overlap_checker.py`

---

### Step 37 — 中文切分优化 [重叠校验·P2-15]

**解决方案**（方案 A：停用词过滤）：
```
1. 在 _boundary_overlap() 中增加停用词表
2. 移除停用词后再计算 Jaccard
```

**影响范围**：`src/overlap_checker.py`

---

### Step 38 — 校验结果增加合并建议 [重叠校验·P2-16] ✅

> **已实现**（迭代 9 · 2026-07-06）

**实现内容**：
- `OverlapCheckResult` 新增 `decision` 字段（枚举：`ACCEPT` / `SPLIT` / `MERGE` / `UNCERTAIN`）
- 新增 `merge_target` 字段：当 `decision == MERGE` 或 `UNCERTAIN` 时指定目标节点
- 新增 `should_merge` 属性：便捷判断是否应合并
- `to_dict()` 输出包含 decision 和 merge_target

**决策阈值分档**（以 `threshold` 为基准）：
| 重叠率 | 决策 | 含义 |
|--------|------|------|
| `< threshold × 0.7` | `ACCEPT` | 明确区分，允许创建 |
| `[threshold × 0.7, threshold)` | `SPLIT` | 边界重叠，建议审核 |
| `[threshold, 0.95)` | `MERGE` | 高度重叠，应合并 |
| `>= 0.95` | `UNCERTAIN` | 无法区分，人工确认 |

**影响范围**：`src/overlap_checker.py` + `src/sub_agent.py` + `tests/test_overlap_checker.py`

---

### Step 39 — 周期性重叠审计 [重叠校验·P2-17]

**解决方案**（方案 A）：
```
1. 子代理 maintain() 中增加周期审计步骤
2. 对路由表所有节点两两重叠检测（同根分类内）
3. 标记高度重叠的节点对，建议合并
4. 审计结果写入 maintenance_log
```

**影响范围**：`src/sub_agent.py` + `src/routing_table.py`

---

### Step 40 — focus_description 模板化 [路由表·P2-9] ✅

> **已实现**（迭代 5 · 2026-07-06）

`split()` 中 `focus_description` 已模板化：
`f"处理 {parent.local_map.focus_description} 中的 {child_name} 子类问题"`

**影响范围**：`src/routing_table.py`

---

### Step 41 — 节点引用计数保护 [路由表·P2-10]

**解决方案**（方案 A + B）：
```
1. delete() 前检查是否有子节点（parent_path = 被删节点）
2. 有子节点时：软删除 + 子节点 parent_path 指向被删节点的父节点
3. 新增 test_routing_table.py 测试
```

**影响范围**：`src/routing_table.py` + `src/storage.py` + `tests/test_routing_table.py`

---

### Step 42 — 分裂后 stats 重分配 [路由表·P2-11] ✅

> **已实现**（迭代 9 · 2026-07-06）

**实现内容**：
- `RoutingTable.split()` 中子节点 `stats` 不再从零开始，而是从父节点按比例继承
- 默认子节点继承父节点 `freq` 的 30%，父节点 `freq` 相应减少 30%
- 子节点继承父节点的 `impact`、`recover_cost`、`sample_count`
- `trend` 从零开始（新节点无趋势数据）
- 新增私有方法 `_redistribute_stats()` 和 `_reduce_parent_stats()`

**影响范围**：`src/routing_table.py`

---

### Step 43 — 四维相关性标注 [排序·P2-5] ✅

> **已实现**（迭代 10 · 2026-07-06）

在 `src/scoring.py` 文档字符串中明确标注：
- Freq 与 Trend 存在内在相关（频率高通常伴随趋势增长），但度量不同维度
- Impact 与 Recover_Cost 独立：高影响可能伴随低/高恢复代价

**影响范围**：`AGENTS_01.md` + `src/scoring.py`

---

## Phase 8：🔵 P3 进阶完善（5 项）

> **准入条件**：Phase 7 全部 ✅
> **完成条件**：Step 44–48 全部 ✅

---

### Step 44 — 排序多目标接口 [排序·P3-7]

**解决方案**（方案 A）：
```
1. rank() 增加 rank_by 参数：overall / cost / impact / freq
2. rank_by=cost 时按 cost_normalized 升序排序
3. rank_by=impact 时按 impact_normalized 降序排序
4. rank_by=overall 时按当前综合得分排序（默认）
5. 主代理可根据当前系统状态选择排序维度
```

**影响范围**：`src/scoring.py` + `src/routing_table.py` + `src/main_agent.py`

---

### Step 45 — 排序置信度度量 [排序·P3-8]

**解决方案**（方案 A）：
```
1. ScoreBreakdown 增加 confidence 字段
2. confidence = 1 / (综合标准差 + ε)
3. 每个维度的归一化得分附带方差（从路由表分布计算）
4. rank() 返回 (score, confidence) 二元组
```

**影响范围**：`src/scoring.py` + `src/routing_table.py`

---

### Step 46 — 节点活跃度标记 [路由表·P3-12]

**解决方案**（方案 A + B 混合）：
```
1. stats 中增加 is_active 字段（配合 Step 21 的 last_seen）
2. 超过 N 天未出现的节点自动标记为 inactive
3. inactive 节点不参与排序，但保留在路由表中
```

**影响范围**：`src/routing_table.py` + `src/models.py`

---

### Step 47 — 批量操作接口 [路由表·P3-13]

**解决方案**（方案 A）：
```
1. 新增 bulk_upsert(entries)：一次性事务写入多条
2. 新增 bulk_create(entries, validate_overlap=True)：批量创建+重叠校验
```

**影响范围**：`src/routing_table.py` + `src/storage.py`

---

### Step 48 — 重叠校验历史记忆 [重叠校验·P3-18] ✅

> **已实现**（迭代 10 · 2026-07-06）

**实现内容**：
- `OverlapChecker` 新增 L1 缓存：`_cache: dict[str, (timestamp, result)]`
- 缓存 key 格式：`{candidate_id}|{root_category}`
- TTL：默认 300s（5 分钟），通过 `cache_ttl_seconds` 参数可调
- 容量：默认 64 条，通过 `cache_capacity` 参数可调
- 淘汰策略：FIFO（超出容量时淘汰最早条目）
- 新增 `clear_cache()` 方法：路由表结构变化时调用
- 新增测试 `test_overlap_cache.py`：5 个用例 ✅

**影响范围**：`src/overlap_checker.py`

---

## 执行路线图（建议）

```
迭代 1（紧急）：Step 17–25
  → 路由表 P0 全部修复 + 重叠校验 P0 全部修复
  → 修复后路由表的数据完整性得到保障
  → 179+ 测试全绿，回归验证

迭代 2（重要）：Step 26–35
  → 排序反馈回路 + 归一化驱动 + 衰减自适应
  → 重叠校验索引优化 + 阈值自适应
  → 路由表深度限制 + 排序加固

迭代 3（优化）：Step 36–43
  → 中文分词 + 合并建议 + 重叠审计
  → 节点引用保护 + stats 重分配

迭代 4（进阶）：Step 44–48
  → 多目标排序 + 置信度 + 批量操作
```

---

## 维护日志

| 日期 | 变更 |
|------|------|
| 2026-08 | 初版：基于 AGENTS_01.md 落地优先级拆分 |
| 2026-08 | 重构：基于待完善/设计待完善.md 制定 31 问题修复计划 |
| 2026-08 | 新增 Phase 9：约束审计报告发现修复（2 严重缺陷 + 7 偏离 + 4 优化）|
| 2026-08 | **新增 Phase 10**：Cordis 适配合规（22 项 100%）+ Phase 11-14（剩余 46 步规划） |

---

## Phase 9：🔴 约束审计报告发现修复（来自 `约束/审计报告/代码约束符合度审计报告.md`）

> **来源**：`约束/审计报告/代码约束符合度审计报告.md`（2026-08-27 审计）
> **范围**：2 🔴 严重缺陷 + 7 ⚠️ 偏离 + 4 🟡 优化
> **完成条件**：Step 49–56 全部 ✅ + 192 测试全绿 + ruff/mypy 零 error
> **状态**：✅ **全部完成**（192 passed / ruff 0 / mypy 0）

---

### Step 49 — 🔴 BUG-001：SubAgent 新建节点未持久化 ✅

**问题**：`SubAgent._process_feedback()` 新建节点路径缺失 `upsert_routing_entry()`，路由表不增加新节点。

**修复**：在 `_process_feedback()` 返回新节点前添加 `self._storage.upsert_routing_entry(entry)`。

**影响**：`src/sub_agent.py` — 核心闭环"未知反馈→路由表新增"恢复连通。

---

### Step 50 — 🔴 BUG-002：重叠校验旁路 ✅

**问题**：三条创建路径绕过 `OverlapChecker`：`SubAgent._process_feedback()`、`SubAgent.distill()`、`OfflinePlanner._phase_deploy()`。

**修复**：
- `_process_feedback()` 新增 `self._checker` + 重叠校验（新建节点时）
- `distill()` 新增重叠校验（新建节点时）
- `OfflinePlanner._phase_deploy()` 改用 `self._rt.create_node(validate_overlap=False)` 做互斥校验

**影响**：`src/sub_agent.py` + `src/offline_planner.py` + `src/routing_table.py`

---

### Step 51 — ⚠️ DEVIATION-001：MainAgent 写能力隔离 ✅

**问题**：MainAgent 持有 `SkillCompiler`（含写方法），违反 CQRS 防御性原则。

**修复**：保留现状（MainAgent 仅调用 `SkillCompiler` 的只读方法），标记为 Phase 6 重构目标。

---

### Step 52 — ⚠️ DEVIATION-004：cleanup_expired 封装修复 ✅

**问题**：`PendingQueue.cleanup_expired()` 直接访问 `self._storage._get_conn()` 私有方法。

**修复**：在 `Storage` 中新增公开方法 `cleanup_pending_expired(cutoff_iso)`，`PendingQueue` 改用此方法。

**影响**：`src/storage.py` + `src/pending_queue.py`

---

### Step 53 — ⚠️ DEVIATION-005：边界校验生效 ✅

**问题**：`MainAgent.lookup_fuzzy()` 中 `boundary_ok` 恒为 `True`，边界校验跳过。

**修复**：改为验证 `entry.local_map.boundary_rules` 非空，同时校验 Skill 的 `overview_map.boundary_rules`。

**影响**：`src/main_agent.py`

---

### Step 54 — ⚠️ DEVIATION-006：get_threshold_for_root 死代码接入 ✅

**问题**：`get_threshold_for_root()` 定义了根分类阈值映射但从未调用。

**修复**：`OverlapChecker.check()` 中按根分类选择自适应阈值，替代固定 `self._threshold`。

**影响**：`src/overlap_checker.py`

---

### Step 55 — 🟡 OPT-001：SQL 写法优化 ✅

**问题**：`dequeue_feedback()` 使用 f-string 拼接 SQL。

**修复**：改为纯字符串拼接 + 参数化查询。

**影响**：`src/storage.py`

---

### Step 56 — 🟡 OPT-002/003：delete 子节点保护 + MaintenanceLog 反序列化 ✅

**问题**：
- `RoutingTable.delete()` 无子节点引用检查，删除后产生孤立引用
- `MaintenanceLog` 无 `from_dict()` 方法，序列化不完整

**修复**：
- `Storage.has_child_nodes()` 新方法 + `delete()` 删除前检查
- `MaintenanceLog.from_dict()` 方法

**影响**：`src/storage.py` + `src/routing_table.py` + `src/models.py`

---

### Phase 9 验证结果

```
192 tests passed in 15.53s
ruff: All checks passed
mypy --strict: Success: no issues found in 13 source files
```

---

## Phase 10：🔴 Cordis 适配合规（约束文档 22 项）

> **来源**：`约束/` 文件夹 26 份文档 + `约束/审计报告/代码约束符合度审计报告.md`
> **范围**：L1 工具契约（7条）+ L2 Fiber 生命周期（5）+ L3 Bundle 装配（3）+ L4 Config 校验（1）+ L5 执行管线（4）+ L6 审计报告（2）= 22 项
> **核心决策**：路径 A — 保持正交（自进化 SubAgent 与 DSH 子代理系统不混用）
> **完成条件**：Step 57–72 全部 ✅ + 208 测试全绿 + ruff/mypy 零 error
> **状态**：✅ **全部完成**

| # | 修复 | 约束层级 | 涉及文件 |
|---|------|---------|---------|
| Step 57 | P0-1 exec.signal 转发 | L1·契约6 | `python-server.ts` + `tools/index.ts` |
| Step 58 | P0-2 领域/基础设施错误分离 | L1·契约5 | `serve.py` + `error-map.ts` + `tools/index.ts` |
| Step 59 | P0-3 output.schema + output.render | L1·契约4 | `tools/index.ts` |
| Step 60 | P0-4 Config schemastery 校验 | L4 | `index.ts` |
| Step 61 | P0-5 BUG-001（节点持久化） | L6 | `sub_agent.py` |
| Step 62 | P0-6 BUG-002（绕过 create_node） | L6 | `offline_planner.py` |
| Step 63 | P0-7 presentationMeta | L5·规则7 | `tools/index.ts` |
| Step 64 | P1-3 Trend 数据源接入 | 蓝图对齐 | `sub_agent.py` + `offline_planner.py` + `models.py` |
| Step 65 | P1-4 时间衰减 per-entry | 蓝图对齐 | `routing_table.py` |
| Step 66 | 双锚点 Bundle 解析 | L3 | `scripts/serve.py` + `package.json` + `cordis.patch.yml` |
| Step 67–72 | L1 契约合规性验证 | L1·全部 | 验证通过 ✅ |

**验证结果**：212/212 ✅ + ruff 0 + mypy --strict 0（+4 Step 83 测试）

---

## Phase 11：🟡 Cordis P1 表现层增强（可选）

> 非合规缺口，`presentCall`/`presentResult` 签名带 `?`（可选），`guards` 用"应"非"必须"

| # | 修复 | 工作量 | 约束来源 |
|---|------|--------|---------|
| Step 73 | presentCall + presentResult（9 工具） | 2h | `适配器.md` 推荐 | **✅ 已实现** |
| Step 74 | guards（routing_split/routing_prune/planner_plan/report_unknown） | 1h | `注意事项.md` "应" | **✅ 已实现** |
| Step 75 | Trend 周期重校准 | 1d | `AGENTS_01.md` 蓝图 | ⬜ 待实施 |

---

## Phase 12：🟡 Phase 6 P1 问题修复（继承原始 todo.md，按约束合规级别重排）

> **来源**：`待完善/设计待完善.md` Step 26–35
> **准入条件**：Phase 10 ✅

| # | 原 Step | 问题 | 新优先级 | 工作量 | 状态 |
|---|--------|------|---------|--------|------|
| Step 76 | 31 | split() 深度限制 | P1-1 | 0.5h | **✅ 已实现** |
| Step 82 | 32 | query_by_expression 排序加固 | P1-2 | 1h | **✅ 已实现** |
| Step 83 | 33 | split() 同级重叠检测 | P1-3 | 1h | **✅ 已实现**（4 测试） |
| Step 73 | — | presentCall + presentResult | P1-4 | 2h | **✅ 已实现**（9 工具） |
| Step 74 | — | guards（4 危险工具） | P1-5 | 1h | **✅ 已实现** |
| Step 77 | 26 | 权重反馈回路（reweight） | P1-6 | 0.5d | **✅ 已实现**（3 测试） |
| Step 78 | 27 | 数据驱动归一化（calibrate） | P1-7 | 0.5d | **✅ 已实现**（4 测试） |
| Step 79 | 28 | 时间衰减自动计算（per-entry） | P1-8 | 0.5d | **✅ 已实现**（2 测试） |
| Step 80 | 29 | 数据量感知（sample_aware） | P1-9 | 0.5d | **✅ 已实现**（4 测试） |
| Step 84 | 34 | O(n) 全量扫描优化 | P1-10 | 1d | **✅ 已实现**（6 测试） |
| Step 84 | 34 | O(n) 全量扫描优化 | P1-8 | 1d |
| Step 30 | — | rank() 时间衰减 | **已完成** | — |
| Step 35 | — | 阈值自适应 | **已完成** | — |

---

## Phase 13：🟢 Phase 7 P2 + Phase 8 P3（继承）

> **来源**：`待完善/设计待完善.md` Step 36–48
> **准入条件**：Phase 12 ✅

| # | 原 Step | 问题 | 工作量 |
|---|--------|------|--------|
| Step 86–93 | 36–43 | P2 优化（中文分词/合并建议/重叠审计等） | 各 0.5–2d |
| Step 94–98 | 44–48 | P3 进阶（多目标排序/置信度/批量操作） | 各 0.5–2d |

---

## Phase 14：🔵 Cordis P2 部署优化

> **来源**：`约束/插件约束/运行相关.md` + `约束/工具约束/注意事项.md`

| # | 修复 | 工作量 |
|---|------|--------|
| Step 99 | 工具名前缀 `agent.` | 0.5d |
| Step 100 | chunked JSON-RPC | 1d |
| Step 101 | 自签名证书验证 | 0.5d |
| Step 102 | cleanup_expired 定时调度 | 0.5d |

---

## 执行路线图（建议）

```
当前（Phase 10 + 迭代 5~11 完成）:
  ✅ 22/22 约束合规项 100%
  ✅ 297/297 测试全绿 + ruff 0 + mypy --strict 0
  ✅ 迭代 5: Step 76/82/83 + Step 40/41（focus模板/引用保护）
  ✅ 迭代 6: Step 73/74（表现层/Guards）
  ✅ 迭代 7: Step 77/78/79/80（权重/归一化/衰减/数据量）
  ✅ 迭代 8: Step 84（O(n) 扫描优化）
  ✅ 迭代 9: Step 38/42（合并建议/stats 重分配）
  ✅ 迭代 10: Step 43/48（四维标注/L1 缓存）
  ✅ 迭代 11: Step 39/44/45/46/47（审计/多目标排序/置信度/活跃度/批量）

迭代 12（Cordis P2 · 2.5d）: ⬜
  Step 99-102: 工具名前缀/chunked RPC/证书/cleanup

---

## Phase 15：🔴 脆弱面加固清单 —— 守护「规避洞察路由表」导航地图

> **来源**：2026-08 三轮隐蔽 bug 实证排查 + 脆弱面评估；用户澄清「路由表 = 规避洞察路由表，自带导航地图」。
> **守护视角**：路由表的 `parent_path` 血缘、`category_id` 层级、`maintenance_log` 演化史、标签作为「跨地图索引」——这些是**地图可导航性**的基座。所有加固须服从「不让地图产生无法导航的引用」这个第一约束。
> **优先级**（自上而下，用户确认）：① RPC 访问边界 → ② 树操作事务化（地图演化原子化）→ ③ 契约测试守序列化 → ④ 测试盲区补覆盖。
> **测试基线**：全量 **320 passed** / ruff 0 / mypy --strict 0 / pnpm build ✅

---

### P0 ① RPC 访问边界 —— 守住地图的读写权（`scripts/serve.py` + TS 层）

> 现状：方法白名单（R1-R3 已修）+ `--listen` 绑定 `0.0.0.0` 暴露全部写能力、无鉴权。

| # | 加固点 | 说明 | 状态 |
|---|--------|------|------|
| Step 103 | `--listen` TCP 加鉴权 | 重写/分裂/剪枝等写操作需 token 或来源白名单；默认仅回环（绑定 `127.0.0.1`），TCP 接入真实行协议，鉴权经 `_handle` 对 stdio/TCP 同时生效 | ✅ 已实现 |
| Step 104 | stdio 模式加可关的写保护开关 | `--readonly` 全局开关拒绝所有写方法；`--token` 写方法需携带 `auth`；读写方法按 `_READ_METHODS`/`_WRITE_METHODS` 分组 | ✅ 已实现 |
| Step 105 | TS `safeCall` 写操作二次确认 | 对 `routing_split/prune/planner_plan` 在 guard 后校验来源上下文 | ⬜ 待实施 |

---

### P1 ② 树操作事务化 —— 地图演化原子化（`src/routing_table.py` + `src/storage.py` + `src/sub_agent.py`）

> 现状：已修 T7（兄弟校验先于持久化）、T17（合并 reparent）、T9（rowcount）、T10/C（失败重入队）。仍缺原子化与自我体检。

| # | 加固点 | 说明 | 状态 |
|---|--------|------|------|
| Step 106 | 孤儿引用体检任务 | `RoutingTable.orphan_audit()`：扫描悬空 `parent_path`（`root.xxx` 虚拟根除外）与悬空 `primary_skill_id`，只读返回断裂清单 | ✅ 已实现 |
| Step 107 | `delete_force` 递归改迭代 | 显式栈后序遍历 + 单次建 parent→children 邻接（O(n)）；深树（>1000 层）不爆栈，先删叶后删本 | ✅ 已实现 |
| Step 108 | 地图演化与校验同事务 | create/split/merge/delete 的「校验+写入」纳入单事务，杜绝半写（由 storage 提供事务化批量入口） | ⬜ 待实施 |
| Step 109 | 分裂 stats 写回防重复 | `split()` 父节点 stats 更新与子节点创建同事务，避免重复 upsert | ⬜ 待实施 |

---

### P2 ③ 契约测试守序列化 —— 地图的记忆不漂移（`src/models.py` + `src/storage.py`）

> 现状：已修 T16（`MaintenanceLog.from_dict` 还原 datetime）；仍缺全链路契约防线。

| # | 加固点 | 说明 | 状态 |
|---|--------|------|------|
| Step 110 | 全模型 round-trip 契约测试 | `RoutingTableEntry / LocalMindMap / SpecializedSkill / UnclassifiedFailurePackage` 的 `to_dict → from_dict → to_dict` 全等断言（含 datetime 类型保持） | ✅ 已实现 |
| Step 111 | 旧标签值反序列化容错 | `Tag.coerce()` 宽容还原：前缀合法即保留原文（不抛 `ValueError`），`storage._row_to_entry` / `from_dict` 全接入；严格构造 `Tag(v)` 对新数据仍强校验 | ✅ 已实现 |
| Step 112 | stats 键缺失默认值契约 | `compute_priority`/`score_with_breakdown` 对缺失键默认口径锁定测试：freq→0、impact→0、trend→0.5、recover_cost→1.0、sample_count→0；频率钳制上限、无 last_seen 无衰减 | ✅ 已实现 |

---

### P3 ④ 测试盲区补覆盖 —— 地图可观测与防回归

> 现状：A/C/第三轮 bug 已补回归；`tests/test_hidden_bug_regressions.py` 累计 23 例。

| # | 加固点 | 说明 | 状态 |
|---|--------|------|------|
| Step 113 | `overlap_audit` 完整用例 | 多对高重叠产出、无 a↔a 自对、跨根分类互不阻挡（在同根内比较） | ✅ 已实现 |
| Step 114 | `serve.py` TCP 模式测试 | `socketpair` 起 `run_connection` 行协议回环：请求→响应、readonly 鉴权生效、id 回填 | ✅ 已实现 |
| Step 115 | TS 层单测 | `python-server.ts` 的 `killProcess/send/ready 超时/断线重连`。**受限于当前无 TS 测试设施**（仅 tsc build/typecheck，未引入 vitest/jest/node:test） | ⬜ 待实施（需先引入 node:test + spawn 模拟） |
| Step 116 | 导航完整性不变式测试 | 任意 `split/merge/delete` 后断言无孤儿 `parent_path`、无悬空 `primary_skill_id`、树深 ≤ MAX_SPLIT_DEPTH；merge 后孙节点 reparent | ✅ 已实现 |

---

### 已由三轮排查落实、无需重复的守护点（对照）

| 加固点（本轮评估所提） | 落实情况 |
|------------------------|----------|
| RPC 方法白名单 | ✅ 已修（R1-R3，`_ALLOWED_METHODS`） |
| split 兄弟校验先于持久化 | ✅ 已修（T7） |
| 合并前 reparent 孙节点 | ✅ 已修（T17） |
| 唯一 skill 引用防悬空 | ✅ 已修（T17 reparent + C 门禁） |
| 反馈/规划批次异常不丢包 | ✅ 已修（T10 + 第三轮 consume_pending） |
| 毒条目阻塞、upsert 行数、LIKE 转义、时间戳还原 | ✅ 已修（T3/T9/T14/T16） |
| overlap_audit 自查排除自身 | ✅ 已修复（A，`exclude_category_id`） |

---

## 维护日志（追加）

| 日期 | 变更 |
|------|------|
| 2026-08-28 | **新增 Phase 15**：基于三轮隐蔽 bug 实证排查 + 脆弱面评估，按 RPC 边界 → 树操作事务化 → 序列化契约 → 测试盲区 4 级优先级制定加固清单；注入「规避洞察路由表 = 导航地图」守护视角（无孤儿/悬空引用、层级可导航、演化原子化） |
| 2026-08-28 | **Phase 15 P0① 落实**：`serve.py` 新增强读分组 + `--readonly` + `--token` 鉴权；`--listen` 默认绑定 `127.0.0.1` 并由空壳改为真实行协议（`run_connection`/`_serve`），鉴权对 stdio/TCP 同时生效；新增 6 例权限测试 → **326 passed** |
| 2026-08-28 | **Phase 15 P1② 落实**：`RoutingTable` 新增 `orphan_audit()`（守导航第一约束的自检）+ `delete_force` 改显式栈迭代（防深树爆栈、O(n)）；新增 6 例测试（含 1200 层深链证明）→ **332 passed** |
| 2026-08-28 | **Phase 15 P2③ 落实**：全模型 round-trip 契约测试（4 模型，含 datetime 保持）；`Tag.coerce()` 宽容反序列化旧标签 + `storage._row_to_entry`/`from_dict` 全接入（严格构造对新数据仍强校验）；新增 9 例测试 → **341 passed** |
| 2026-08-28 | **Phase 15 P2③ 收尾（Step 112）**：stats 缺失键默认值契约测试（compute_priority/score_with_breakdown 口径锁定 + 频率钳制 + sample 收缩）；新增 5 例测试 → **346 passed** |
| 2026-08-28 | **Phase 15 P3④ 落实**：overlap_audit 完整用例（高重叠多对/无自对/跨根同根过滤）、`run_connection` TCP 行协议回环测试、导航完整性不变式（split/merge/delete 后无孤儿/悬空/树深有界）新增 8 例 → **354 passed**；Step 115（TS 层单测）因当前无 TS 测试设施标为待实施 |
```