# src API 参考文档（自动生成）

> 本文档由 `scripts/gen_api_docs.py` 自动生成，以代码为唯一真相源。
> 若需更新 API 描述，请修改代码 docstring 后重新生成。

---

## `models`

*六大核心数据模型 — 受控自进化 AI Agent 框架的数据基石。

设计原则：
- 每个模型携带 `LocalMindMap`（局部思维导图），记录边界与逻辑签名
- 标签必须带前缀（状态_ / 代价_ / 场景_），禁止裸标签
- 所有时间字段使用 datetime (ISO 8601)
- 所有 ID 使用字符串，格式由调用方约定*

### `LocalMindMap`

*局部思维导图：每个路由表节点和 Skill 步骤的元数据。

这是框架的"执念核心"——强制每个节点记述：
- 自己聚焦解决什么（focus_description）
- 绝对不管什么（boundary_rules）——防越界的关键
- 逻辑签名（logic_signature）——自然语言描述行为
- 完整变更史（maintenance_log，滚动保留最近 MAX_MAINTENANCE_LOG 条）*

- 签名: `LocalMindMap(self, node_id: 'str', parent_path: 'str', focus_description: 'str', boundary_rules: 'str', logic_signature: 'str', maintenance_log: 'list[MaintenanceLog]' = <factory>) -> None`

公开成员：

  - 方法: `append_log(action: 'str', reason: 'str', actor: 'str') -> 'None'` — 追加一条维护日志。
  - 方法: `from_dict(data: 'dict[str, Any]') -> 'LocalMindMap'`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `MaintenanceLog`

*单条维护日志条目。*

- 签名: `MaintenanceLog(self, timestamp: 'datetime', action: 'str', reason: 'str', actor: 'str') -> None`

公开成员：

  - 方法: `from_dict(data: 'dict[str, Any]') -> 'MaintenanceLog'` — 从字典恢复维护日志条目。
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `NodeQualityScore`

*路由表节点质量评分（Skill-Judge D1 知识增量维度）。

基于 Skill-Judge 白皮书的核心公式：
    知识增量 = E / (E + A + R)
    E = Expert 知识（具体策略、决策树、反模式、边界案例）
    A = Activation 知识（通用提醒、已知概念标注）
    R = Redundant 知识（"处理X"、"修复X"、"检查X"等空话）

质量等级判定：
    delta >= 0.5 → "expert"（保留）
    delta >= 0.3 → "adequate"（可接受）
    delta >= 0.1 → "poor"（标记待改进）
    delta < 0.1  → "redundant"（加入剪枝候选）*

- 签名: `NodeQualityScore(self, category_id: 'str', expert_score: 'float', activation_score: 'float', redundant_score: 'float', knowledge_delta: 'float', quality_level: 'str', signals: 'list[str]' = <factory>) -> None`

公开成员：

  - 方法: `from_dict(data: 'dict[str, Any]') -> 'NodeQualityScore'`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `RoutingTableEntry`

*路由表条目 — 规避洞察路由表的核心数据单元。

category_id 使用点号分隔的层级命名，如 'network.rate_limit.429'。
第一级必须属于 ROOT_CATEGORIES（人类锁定层）。*

- 签名: `RoutingTableEntry(self, category_id: 'str', stats: 'dict[str, float | str]', local_map: 'LocalMindMap', tags: 'set[Tag]' = <factory>, primary_skill_id: 'str | None' = None) -> None`

公开成员：

  - 方法: `clear_subtype(name: 'str') -> 'None'` — 移除指定子类型的观测计数。
  - 方法: `dominant_subtype() -> 'tuple[str, float] | None'` — 返回观测占比最高的 (子类型名, 占比)。
  - 方法: `from_dict(data: 'dict[str, Any]') -> 'RoutingTableEntry'`
  - 方法: `normalize_subtype(raw: 'str') -> 'str'` — 将原始子类型描述规范化为可作 category_id 片段的安全名称。
  - 方法: `record_subtype(raw_subtype: 'str') -> 'str'` — 记录一次子类型观测。
  - 方法: `subtype_distribution() -> 'dict[str, float]'` — 返回 {子类型名: 观测次数}；未观测到任何子类型时返回空字典。
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `SkillStep`

*Skill 中的单一步骤，携带步骤局部地图。*

- 签名: `SkillStep(self, step_id: 'str', action: 'str', local_map: 'LocalMindMap', precondition: 'str | None' = None, postcondition: 'str | None' = None, retry_policy: 'dict[str, Any] | None' = None) -> None`

公开成员：

  - 方法: `from_dict(data: 'dict[str, Any]') -> 'SkillStep'`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `SpecializedSkill`

*专类 Skill 工作流（DAG）。overview_map 继承自路由表节点。

pattern: Skill 结构模式（来自 Skill-Builder 模板模式适配）。
可选值："tool" / "domain" / "workflow" / "memory" / "generic"。
不同模式对应不同的步骤结构和行为特征。

tools: Skill 运行时工具集。从路由表节点的边界规则中推断，
描述该 Skill 执行时需要哪些工具（如 "http_client" / "retry" / "memory"）。
这是 Agent-Builder "Skill 运行时化" 的一部分。

context_keys: Skill 执行时需要从主代理上下文读取的键名列表，
用于上下文压缩和按需注入。*

- 签名: `SpecializedSkill(self, skill_id: 'str', name: 'str', pattern: 'str' = 'generic', overview_map: 'LocalMindMap' = <factory>, steps: 'list[SkillStep]' = <factory>, tools: 'list[str]' = <factory>, context_keys: 'list[str]' = <factory>, tags: 'set[Tag]' = <factory>) -> None`

公开成员：

  - 方法: `add_step(step: 'SkillStep') -> 'None'` — 向 Skill 追加一个执行步骤。
  - 方法: `from_dict(data: 'dict[str, Any]') -> 'SpecializedSkill'`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `Tag`

*带前缀的标签。value 必须形如 '状态_稳定'、'代价_低消耗' 等。

使用 frozen dataclass 保证不可变性。*

- 签名: `Tag(self, value: 'str') -> None`

公开成员：

  - 属性: `body` → `str` — 返回标签本体（去掉前缀）。
  - 方法: `coerce(value: 'str') -> 'Tag | None'` — 宽容反序列化：前缀合法即保留，本体不在白名单也不抛错。
  - 属性: `prefix` → `TagPrefix` — 返回标签前缀枚举。

### `TagPrefix`

*三类强制前缀。所有 Tag.value 必须以这三种之一开头。*

- 签名: `TagPrefix(self, args, kwds)`

### `UnclassifiedFailurePackage`

*主代理遇到未知错误时生成的举证包，异步写入反馈暂存队列。*

- 签名: `UnclassifiedFailurePackage(self, error_stack: 'str', context_snapshot: 'dict[str, Any]', attempted_strategies: 'list[str]' = <factory>, location_guess: 'str' = '', confidence: 'float' = 0.0, timestamp: 'datetime' = <factory>) -> None`

公开成员：

  - 方法: `from_dict(data: 'dict[str, Any]') -> 'UnclassifiedFailurePackage'`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `sanitize_signature`

*把任意错误签名字符串规约为合法的 category_id 组成段。*

- 签名: `sanitize_signature(raw: 'str') -> 'str'`

---

## `storage`

*SQLite 存储层 — 路由表、暂存队列、Skill 库的统一持久化。

设计要点：
- 使用 JSON 列存储复杂对象（LocalMindMap / stats / Skill DAG）
- 标签存储为逗号分隔字符串（SQLite 无原生数组）
- 所有写入操作返回写入行数，便于调用方验证
- 时间字段统一使用 ISO 8601 字符串*

### `Storage`

*SQLite 存储引擎。管理连接、建表、CRUD 操作。

使用示例：
    db = Storage("path/to/data.db")
    db.init()
    db.upsert_routing_entry(entry)
    entries = db.query_routing_entries()
    db.close()*

- 签名: `Storage(self, db_path: 'str | Path') -> 'None'`

公开成员：

  - 方法: `cleanup_pending_expired(cutoff_iso: 'str') -> 'int'` — 清理超期举证包。返回删除的条目数。
  - 方法: `close() -> 'None'`
  - 方法: `count_routing_entries() -> 'int'`
  - 方法: `delete_routing_entry(category_id: 'str') -> 'bool'` — 删除路由表条目，返回是否删除成功。
  - 方法: `dequeue_feedback(limit: 'int' = 10) -> 'list[UnclassifiedFailurePackage]'` — 取出未处理的举证包（按创建时间排序），标记为已处理。
  - 方法: `enqueue_feedback(pkg: 'UnclassifiedFailurePackage') -> 'int'` — 向暂存队列写入举证包，返回新行的 id。
  - 方法: `get_routing_entry(category_id: 'str') -> 'RoutingTableEntry | None'` — 按 category_id 精确查询路由表条目。
  - 方法: `get_skill(skill_id: 'str') -> 'SpecializedSkill | None'`
  - 方法: `get_skills(skill_ids: 'Iterable[str]') -> 'dict[str, SpecializedSkill]'` — 批量获取 Skill，返回 {skill_id: SpecializedSkill}。
  - 方法: `has_child_nodes(category_id: 'str') -> 'bool'` — 检查是否存在以 category_id 为 parent_path 的子节点。
  - 方法: `init() -> 'None'` — 初始化所有表结构。幂等：多次调用安全。
  - 方法: `pending_count() -> 'int'`
  - 方法: `query_routing_entries(root_category: 'str | None' = None, tags: 'set[Tag] | None' = None, parent_path: 'str | None' = None) -> 'list[RoutingTableEntry]'` — 查询路由表条目，支持根分类过滤、标签过滤和父路径过滤。
  - 方法: `upsert_routing_entry(entry: 'RoutingTableEntry') -> 'int'` — 插入或更新路由表条目，返回影响行数。
  - 方法: `upsert_skill(skill: 'SpecializedSkill') -> 'int'`

---

## `tag_system`

*标签系统 — 遗传、变异、查询。

核心职责：
- 遗传：子节点继承父节点所有标签
- 变异：子节点可覆盖或移除遗传标签
- 查询：构建多标签 AND 查询条件

标签系统本身不持有状态，所有操作均为纯函数。*

### `TagQuery`

*构建多标签 AND 查询条件。

使用示例：
    query = TagQuery() \
        .require(Tag("状态_稳定")) \
        .require(Tag("场景_第三方依赖"))

    # 转为标签集合，供 storage.query_routing_entries(tags=query.build()) 使用
    tags = query.build()*

- 签名: `TagQuery(self) -> 'None'`

公开成员：

  - 方法: `build() -> 'set[Tag]'` — 返回查询所需的标签集合。
  - 方法: `require(tag: 'Tag') -> 'TagQuery'` — 添加一个必须匹配的标签（AND 语义）。
  - 方法: `require_prefix(prefix: 'TagPrefix') -> 'TagQuery'` — 添加一个按前缀的查询：任意一个该前缀的标签即可匹配。
  - 属性: `tags` → `list[Tag]` — 返回已添加的标签列表（保留添加顺序）。

### `filter_tags_by_prefix`

*从标签集合中筛选出指定前缀的标签。*

- 签名: `filter_tags_by_prefix(tags: 'Iterable[Tag]', prefix: 'TagPrefix') -> 'set[Tag]'`

### `inherit_tags`

*子节点标签继承。

Args:
    parent_tags: 父节点的全部标签
    overrides: 子节点要覆盖的标签（替代父节点同前缀的标签）
    removals: 子节点要移除的标签

示例：
    parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}

    # 继承 + 覆盖状态为"稳定"
    child = inherit_tags(parent, overrides={Tag("状态_稳定")})
    # -> {Tag("状态_稳定"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}

    # 继承 + 移除高延迟
    child = inherit_tags(parent, removals={Tag("代价_高延迟")})
    # -> {Tag("状态_实验性"), Tag("场景_第三方依赖")}*

- 签名: `inherit_tags(parent_tags: 'Iterable[Tag]', overrides: 'Iterable[Tag] | None' = None, removals: 'Iterable[Tag] | None' = None) -> 'set[Tag]'`

### `merge_tags`

*合并多个标签集合，自动去重同前缀标签（取第一个出现的）。

用于多来源标签合并时的优先级处理。*

- 签名: `merge_tags(tag_sets: 'Iterable[Tag]') -> 'set[Tag]'`

### `tags_to_strings`

*将标签集合转为排序后的字符串列表。*

- 签名: `tags_to_strings(tags: 'Iterable[Tag]') -> 'list[str]'`

---

## `tag_query`

*标签复合查询构建器 — 支持 AND / OR / NOT 组合逻辑。

扩展了简单的 TagQuery（仅 AND），提供完整的布尔表达式查询能力。

查询语法（链式调用）：
    query = TagQueryBuilder() \
        .group() \
        .must(Tag("状态_稳定")) \
        .must_not(Tag("场景_本地计算")) \
        .end_group() \
        .or_() \
        .group() \
        .must(Tag("状态_实验性")) \
        .must(Tag("场景_第三方依赖")) \
        .end_group()

查询语义：
    (状态_稳定 AND NOT 场景_本地计算) OR (状态_实验性 AND 场景_第三方依赖)

内部表示：
    query.to_dict() → {
        "should": [
            {"must": [{"tag": "状态_稳定"}, {"must_not": [{"tag": "场景_本地计算"}]}]},
            {"must": [{"tag": "状态_实验性"}, {"tag": "场景_第三方依赖"}]}
        ]
    }

使用示例：
    builder = TagQueryBuilder()
    query = builder.must(Tag("状态_稳定")).must_not(Tag("场景_本地计算")).build()
    results = storage.query_routing_entries(match_query=query)*

### `TagQueryBuilder`

*标签复合查询构建器。

支持语义：
- must(tag): AND — 必须包含此标签
- must_not(tag): NOT — 必须不包含此标签
- should(tag): OR — 至少包含以下之一
- group() / end_group(): 分组，支持 (A AND B) OR (C AND D)
- or_(): OR 分组分隔符*

- 签名: `TagQueryBuilder(self) -> 'None'`

公开成员：

  - 属性: `all_tags` → `list[str]` — 返回查询中出现的所有标签字符串（去重，保留顺序）。
  - 方法: `build() -> 'dict[str, Any]'` — 构建查询表达式。
  - 方法: `end_group() -> 'TagQueryBuilder'` — 结束当前组；后续条件进入一个新的、被追踪的组。
  - 方法: `group() -> 'TagQueryBuilder'` — 开始一个新的 AND 组。
  - 方法: `must(tag: 'Tag') -> 'TagQueryBuilder'` — 必须包含此标签（AND）。
  - 方法: `must_not(tag: 'Tag') -> 'TagQueryBuilder'` — 必须不包含此标签（NOT）。
  - 方法: `or_() -> 'TagQueryBuilder'` — OR 分组分隔符：结束当前组并开启新的 OR 组。
  - 方法: `should(tag: 'Tag') -> 'TagQueryBuilder'` — 至少包含以下之一（OR，同一组内）。
  - 方法: `to_dict() -> 'dict[str, Any]'` — 同 build()，返回可序列化的查询表达式。

### `evaluate_query`

*评估一个路由表条目的标签是否匹配查询表达式。

这是一个纯函数，不依赖存储。

Args:
    entry_tags: 路由表条目的标签集合
    query: TagQueryBuilder.build() 返回的查询表达式

Returns:
    True 表示匹配。*

- 签名: `evaluate_query(entry_tags: 'set[Tag]', query: 'dict[str, Any]') -> 'bool'`

---

## `scoring`

*四维排序计算器 — 路由表节点优先级评估。

公式（来自 AGENTS_01.md）：

    综合优先级 = Freq × 0.25 + Impact × 0.35 + Trend × 0.20 + Recover_Cost × 0.20
    衰减因子 = 2^(-days_since_last_seen / 7)
    最终得分 = 综合优先级 × 衰减因子

权重含义：
- Freq (25%)：过去 N 天的命中频率，越高越重要
- Impact (35%)：修复后的恢复成功率，影响最大
- Trend (20%)：近期增长趋势，防止漏掉"即将爆发"的问题
- Recover_Cost (20%)：恢复代价，代价越低越优先（反向）

Step 43：四维相关性说明
- Freq 与 Trend 存在内在相关（频率高通常伴随趋势增长），
  但两者度量不同维度：Freq 是历史总量，Trend 是变化率。
  保留双维度可区分"高频稳定"与"中频增长"两类问题。
- Impact 与 Recover_Cost 独立：高影响可能伴随低恢复代价（简单问题），
  也可能伴随高代价（复杂问题），两者不可相互替代。

使用示例：
    calc = ScoreCalculator()
    score = calc.compute_final_score(
        stats={"freq": 50, "impact": 0.85, "trend": 0.3, "recover_cost": 2},
        days_since_last_seen=3,
    )

进阶功能：
- 数据驱动归一化（Step 78）：calibrate() 从实际数据学习 freq_max/cost_max
- 权重反馈回路（Step 77）：reweight() 根据数据方差自适应调整权重
- 数据量感知（Step 80）：当 sample_count < threshold 时自动降低影响*

### `ScoreBreakdown`

*单节点得分明细，用于调试和日志。*

- 签名: `ScoreBreakdown(self, category_id: 'str', freq_normalized: 'float', impact_normalized: 'float', trend_normalized: 'float', cost_normalized: 'float', priority: 'float', decay_factor: 'float', final_score: 'float', days_since_last_seen: 'float', sample_count: 'int' = 0, impact_confidence: 'float' = 1.0, sample_penalty: 'float' = 0.0, confidence: 'float' = 1.0) -> None`

公开成员：

  - 方法: `to_dict() -> 'dict[str, Any]'`

### `ScoreCalculator`

*四维排序计算器。

所有 normalize_* 方法均为纯函数，便于测试和调试。*

- 签名: `ScoreCalculator(self, config: 'ScoreConfig | None' = None) -> 'None'`

公开成员：

  - 方法: `calibrate(stats_list: 'list[dict[str, Any]]') -> 'dict[str, float]'` — 从实际数据统计归一化参考值（freq_max / cost_max）。
  - 方法: `compute_final_score(stats: 'dict[str, float]', days_since_last_seen: 'float' = 0.0) -> 'float'` — 计算最终得分（含时间衰减）。
  - 方法: `compute_priority(stats: 'dict[str, float]') -> 'float'` — 计算四维综合优先级（不含时间衰减）。
  - 方法: `decay_factor(days_since_last_seen: 'float') -> 'float'` — 时间衰减因子。
  - 方法: `normalize_cost(cost: 'float') -> 'float'` — 恢复代价归一化：代价越低得分越高（反向 sigmoid）。
  - 方法: `normalize_freq(freq: 'float') -> 'float'` — 频率归一化：线性映射到 [0, 1]。
  - 方法: `normalize_impact(impact: 'float') -> 'float'` — Impact 已在 [0, 1] 范围内，钳制即可。
  - 方法: `normalize_trend(trend: 'float') -> 'float'` — 趋势从 [-1, 1] 映射到 [0, 1]。
  - 方法: `reweight(stats_list: 'list[dict[str, Any]]') -> 'dict[str, float]'` — 根据数据方差自适应调整四维权重。
  - 方法: `sample_aware_impact(impact: 'float', sample_count: 'int') -> 'tuple[float, float, float]'` — 数据量感知的影响得分调整。
  - 方法: `score_with_breakdown(entry: 'RoutingTableEntry', days_since_last_seen: 'float | None' = None) -> 'ScoreBreakdown'` — 计算单节点得分并返回完整明细。

### `ScoreConfig`

*排序计算器的可调参数。*

- 签名: `ScoreConfig(self, freq_weight: 'float' = 0.25, impact_weight: 'float' = 0.35, trend_weight: 'float' = 0.2, cost_weight: 'float' = 0.2, half_life_days: 'float' = 7.0, freq_window_days: 'int' = 30, freq_max: 'float' = 1000.0, cost_max: 'float' = 10.0, sample_count_threshold: 'int' = 5, sample_confidence_floor: 'float' = 0.3, reweight_alpha: 'float' = 0.1) -> None`

---

## `pending_queue`

*反馈暂存队列 — 主代理与子代理之间的异步通信管道。

设计要点：
- 容量上限：超过限制时拒绝入队，防止内存/磁盘无限膨胀
- 过期策略：默认保留 7 天，超期举证包自动清理
- 基于 Storage 的持久化实现，支持重启后恢复

使用示例：
    queue = PendingQueue(storage, capacity=1000, max_age_hours=168)
    ok = queue.enqueue(pkg)   # False 表示队列已满
    items = queue.dequeue(limit=10)
    queue.cleanup_expired()   # 定时调用清理超期条目*

### `PendingQueue`

*反馈暂存队列。

Args:
    storage: 底层持久化存储
    capacity: 最大容量（未处理条目数），默认 1000
    max_age_hours: 举证包最大存活时间（小时），默认 168（7 天）
    on_full: 队列满时的回调，默认抛出 QueueFullError*

- 签名: `PendingQueue(self, storage: 'Storage', capacity: 'int' = 1000, max_age_hours: 'float' = 168.0, on_full: 'Callable[[], None] | None' = None) -> 'None'`

公开成员：

  - 属性: `capacity` → `int`
  - 方法: `cleanup_expired() -> 'int'` — 清理超期举证包。返回删除的条目数。
  - 方法: `dequeue(limit: 'int' = 10) -> 'list[UnclassifiedFailurePackage]'` — 出队未处理的举证包，按创建时间升序返回。
  - 方法: `enqueue(pkg: 'UnclassifiedFailurePackage') -> 'bool'` — 入队举证包。
  - 方法: `is_full() -> 'bool'` — 队列是否已满。
  - 属性: `pending_count` → `int` — 当前未处理条目数。
  - 属性: `remaining` → `int` — 剩余可用容量。

### `QueueFullError`

*队列已满时抛出。*

- 签名: `QueueFullError(self, args, kwargs)`

---

## `overlap_checker`

*重叠率校验器 — 子代理新建分类前的"门禁"。

目的：
当子代理准备从反馈举证中创建新路由表节点时，必须校验新节点
与现有路由表节点的重叠率。若重叠率 >= 阈值（默认 70%），则拒绝创建，
要求子代理合并或复用已有节点，防止路由表膨胀和分类漂移。

重叠率计算维度（v2 修复后）：
1. 错误签名相似度（Levenshtein 距离归一化）——权重 0.55
2. 边界规则重叠度（子集检测 + Jaccard）——权重 0.45

注意：
- 根分类维度已从公式中移除，改为硬性过滤：不同根分类默认不重叠
- 包含关系检测优先于 Jaccard（子集/超集直接计算）
- 中文停用词不参与 Jaccard 计算

Step 38：决策枚举 + 合并建议
- ACCEPT：允许创建（重叠率低于阈值 70%）
- SPLIT：边界重叠，允许创建但建议人工审核
- MERGE：拒绝创建，建议合并到指定已有节点
- UNCERTAIN：高度重叠（>=0.95），无法区分，建议人工确认

使用示例：
    checker = OverlapChecker(storage)
    result = checker.check("network.http_500", "修复 HTTP 500 错误", "仅处理 HTTP 500")
    if result.decision == "ACCEPT":
        # 允许创建
    elif result.decision == "MERGE":
        # 合并到 result.merge_target*

### `OverlapCheckResult`

*重叠率检查结果。*

- 签名: `OverlapCheckResult(self, candidate_id: 'str', candidate_signature: 'str', candidate_boundary: 'str', threshold: 'float' = 0.7, max_overlap: 'float' = 0.0, max_overlap_with: 'str | None' = None, all_scores: 'list[dict[str, Any]] | None' = None, decision: 'str' = 'ACCEPT', merge_target: 'str | None' = None) -> 'None'`

公开成员：

  - 属性: `allows_creation` → `bool` — 重叠率是否低于阈值，允许创建新节点。
  - 属性: `should_merge` → `bool` — 是否应合并到已有节点。
  - 属性: `threshold` → `float`
  - 方法: `to_dict() -> 'dict[str, Any]'`

### `OverlapChecker`

*重叠率校验器。

Args:
    storage: 底层持久化存储
    threshold: 重叠率阈值，默认 0.7（70%）
    signature_weight: 签名相似度权重，默认 0.55
    boundary_weight: 边界重叠度权重，默认 0.45

注意：root_weight 已移除，根分类改为硬性过滤维度。*

- 签名: `OverlapChecker(self, storage: 'Storage', threshold: 'float' = 0.7, signature_weight: 'float' = 0.55, boundary_weight: 'float' = 0.45) -> 'None'`

公开成员：

  - 方法: `check(candidate_category_id: 'str', candidate_signature: 'str', candidate_boundary: 'str', root_category: 'str | None' = None, exclude_category_id: 'str | None' = None, exclude_ids: 'set[str] | None' = None) -> 'OverlapCheckResult'` — 检查候选新节点与现有路由表节点的重叠率。
  - 方法: `check_pair(entry_a: 'RoutingTableEntry', entry_b: 'RoutingTableEntry', words_a: 'set[str] | None' = None, words_b: 'set[str] | None' = None) -> 'float'` — 计算**成对**重叠率（O(1)），不做全表扫描。
  - 属性: `threshold` → `float`

### `get_threshold_for_root`

*获取指定根分类的重叠率阈值。

不同根分类可能需要不同的严格程度：
- network / llm_inference: 差异度大，更严格
- data_parsing: 差异度小，更宽松

Args:
    root_category: 根分类名称
    default: 未配置时的默认阈值

Returns:
    该根分类对应的阈值*

- 签名: `get_threshold_for_root(root_category: 'str', default: 'float' = 0.7) -> 'float'`

---

## `offline_planner`

*离线规划器 — 子代理消费暂存队列的完整闭环。

流程：
1. 从暂存队列 dequeue 举证包
2. 对每个举证包执行"三阶段规划"：
   Phase 1: 分析 —— 解析错误签名、推断根分类、提取边界
   Phase 2: 校验 —— 重叠率检查（< 70% 才允许创建）
   Phase 3: 落地 —— 创建路由表节点 + 编译 Skill
3. 记录规划决策日志（谁/何时/为何/是否通过）

与 SubAgent.consume_pending 的区别：
- consume_pending 是快速路径，适合在线处理
- OfflinePlanner 是完整路径，含重叠率门禁和详细规划日志

使用示例：
    planner = OfflinePlanner(storage, pending_queue)
    report = planner.plan(batch_size=10)
    # report 包含：通过数、拒绝数（含原因）、创建节点、编译 Skill*

### `OfflinePlanner`

*离线规划器 — 子代理消费暂存队列的完整闭环。

Args:
    storage: 底层持久化存储
    pending_queue: 反馈暂存队列*

- 签名: `OfflinePlanner(self, storage: 'Storage', pending_queue: 'PendingQueue') -> 'None'`

公开成员：

  - 方法: `plan(batch_size: 'int' = 10) -> 'PlanningReport'` — 执行整批离线规划。

### `PlanningDecision`

*单次规划决策记录。*

- 签名: `PlanningDecision(self, package: 'UnclassifiedFailurePackage', candidate_category_id: 'str' = '', candidate_signature: 'str' = '', candidate_boundary: 'str' = '', phases: 'list[PlanningPhase]' = <factory>, created_entry: 'RoutingTableEntry | None' = None, compiled_skill: 'SpecializedSkill | None' = None, overlap_result: 'dict[str, Any] | None' = None, rejected: 'bool' = False, rejection_reason: 'str' = '', timestamp: 'datetime' = <factory>) -> None`

公开成员：

  - 方法: `to_dict() -> 'dict[str, Any]'`

### `PlanningPhase`

*单阶段规划结果。*

- 签名: `PlanningPhase(self, phase: 'str', status: 'str', reason: 'str' = '', data: 'dict[str, Any]' = <factory>) -> None`

### `PlanningReport`

*整批规划结果汇总。*

- 签名: `PlanningReport(self, total_processed: 'int' = 0, accepted: 'int' = 0, rejected: 'int' = 0, decisions: 'list[PlanningDecision]' = <factory>, errors: 'list[str]' = <factory>) -> None`

公开成员：

  - 属性: `acceptance_rate` → `float`
  - 方法: `to_dict() -> 'dict[str, Any]'`

---

## `routing_table`

*路由表模块 — 规避洞察路由表的核心操作层。

职责：
- CRUD：基于 Storage 的路由表条目增删改查
- 排序：基于 ScoreCalculator 的四维排序 + 时间衰减
- 分裂（Split）：高频父节点自动下钻，生成子节点
- 剪枝（Prune）：长期垫底节点自动合并

所有写操作都会在 local_map.maintenance_log 中追加记录。*

### `MergePlan`

*剪枝计划。

默认语义（action="merge"）：待剪枝节点关联到其父节点，
合并时将子节点的 stats 加到父节点，tags 取并集，然后删除子节点。

第七批 F-2（第四轮 BUG-31 恢复）：无父节点（parent_path 为空）的
孤立节点无法执行合并，此前被 `prune_lowest` 直接 `continue` 跳过，
导致自动建节点永不参与剪枝、路由表单调膨胀。现按 `action` 字段
区分处理：

- "merge"：合并到 parent_id 指向的父节点
- "delete"：无父可合并，直接淘汰。`detail` 保留被删节点的摘要
  （得分/日志条数/最近动作），弥补节点行删除后 maintenance_log
  随之丢失的审计缺口。*

- 签名: `MergePlan(self, target_id: 'str', parent_id: 'str | None', action: 'str' = 'merge', detail: 'dict[str, Any] | None' = None) -> 'None'`

### `RoutingTable`

*路由表操作层。

Args:
    storage: 底层持久化存储
    score_config: 排序计算器配置，默认使用标准权重
    overlap_checker: 重叠校验器，用于分裂时的语义门禁*

- 签名: `RoutingTable(self, storage: 'Storage', score_config: 'ScoreConfig | None' = None, overlap_checker: 'OverlapChecker | None' = None) -> 'None'`

公开成员：

  - 方法: `check_overlap(candidate_category_id: 'str', candidate_signature: 'str', candidate_boundary: 'str', root_category: 'str | None' = None, exclude_category_id: 'str | None' = None, exclude_ids: 'set[str] | None' = None) -> 'OverlapCheckResult'` — 代理 OverlapChecker.check()，确保上层与 RoutingTable 共用同一 checker。
  - 方法: `check_pair(entry_a: 'RoutingTableEntry', entry_b: 'RoutingTableEntry', words_a: 'set[str] | None' = None, words_b: 'set[str] | None' = None) -> 'float'` — 代理 OverlapChecker.check_pair()，O(1) 成对重叠率计算。
  - 方法: `count() -> 'int'` — 路由表条目总数。
  - 方法: `create_node(entry: 'RoutingTableEntry', validate_overlap: 'bool' = True, candidate_signature: 'str' = '', candidate_boundary: 'str' = '', exclude_ids: 'set[str] | None' = None) -> 'RoutingTableEntry'` — 统一创建路由表节点入口。
  - 方法: `decide(overlap: 'float', threshold: 'float') -> 'str'` — 代理 OverlapChecker._decide()，按重叠率返回决策字符串。
  - 方法: `delete(category_id: 'str') -> 'bool'` — 删除路由表条目。
  - 方法: `delete_force(category_id: 'str') -> 'bool'` — 强制删除：先删除全部子孙节点再删除自身。
  - 方法: `get(category_id: 'str') -> 'RoutingTableEntry | None'` — 按 category_id 精确查询。
  - 方法: `insert(entry: 'RoutingTableEntry') -> 'RoutingTableEntry'` — 插入路由表条目。若条目已存在则抛出 ValueError。
  - 方法: `merge_into_parent(child_category_id: 'str', reason: 'str' = '剪枝合并到父节点', actor: 'str' = 'sub_agent') -> 'MergePlan'` — 将子节点合并到其父节点。
  - 方法: `orphan_audit() -> 'list[dict[str, Any]]'` — 导航地图完整性体检：扫描引用断裂（孤儿/悬空）。
  - 方法: `prune_lowest(threshold: 'float' = 0.1, bottom_pct: 'float' = 0.1, reason: 'str' = '长期垫底自动合并', actor: 'str' = 'sub_agent', execute: 'bool' = True, root_category: 'str | None' = None, orphan_strategy: 'str' = 'skip') -> 'list[MergePlan]'` — 自动剪枝：将得分排名末尾 bottom_pct 的节点标记、合并或淘汰。
  - 方法: `query(root_category: 'str | None' = None, tags: 'set[Tag] | None' = None, parent_path: 'str | None' = None) -> 'list[RoutingTableEntry]'` — 查询路由表条目，支持根分类/标签/父路径过滤（AND 语义）。
  - 方法: `query_all() -> 'list[RoutingTableEntry]'` — 获取全部路由表条目。
  - 方法: `query_by_expression(query_expr: 'dict[str, Any]', root_category: 'str | None' = None, parent_path: 'str | None' = None) -> 'list[RoutingTableEntry]'` — 使用复合标签表达式查询（AND/OR/NOT/分组）。
  - 方法: `rank(days_since_last_seen: 'float' = 0.0, root_category: 'str | None' = None, rank_by: 'str' = 'overall', inactive_days: 'float' = 0.0) -> 'list[ScoreBreakdown]'` — 对路由表条目排序。
  - 方法: `score_entry(entry: 'RoutingTableEntry', days_since_last_seen: 'float' = 0.0) -> 'ScoreBreakdown'` — 计算单个条目的得分明细（使用 per-entry 衰减）。
  - 方法: `split(parent_category_id: 'str', child_name: 'str', reason: 'str', actor: 'str' = 'sub_agent', child_boundary_rules: 'str | None' = None, child_logic_signature: 'str | None' = None, child_overrides: 'set[Tag] | None' = None, child_removals: 'set[Tag] | None' = None) -> 'RoutingTableEntry'` — 从父节点分裂出一个子节点。
  - 属性: `threshold` → `float` — 代理 OverlapChecker.threshold，暴露当前默认重叠率阈值。
  - 方法: `top_k(k: 'int', days_since_last_seen: 'float' = 0.0, root_category: 'str | None' = None) -> 'list[ScoreBreakdown]'` — 返回得分最高的 K 个条目（使用 rank() 的 per-entry 衰减）。
  - 方法: `update(entry: 'RoutingTableEntry') -> 'RoutingTableEntry'` — 更新路由表条目（幂等：不存在则创建，已存在则覆盖）。

### `SplitRejectedError`

*子节点分裂被重叠校验拒绝。*

- 签名: `SplitRejectedError(self, message: 'str', max_overlap: 'float', max_overlap_with: 'str | None') -> 'None'`

---

## `skill_compiler`

*Skill 编译器 — 从路由表节点自动生成专类 Skill 工作流。

设计要点：
- 从 RoutingTableEntry 的 local_map 和 logic_signature 推断 Skill 结构
- 默认生成三步 DAG：前置校验 → 核心动作 → 后置校验
- 每个步骤携带独立的 LocalMindMap（继承 + 细化边界）
- 支持自定义步骤模板（StepTemplate）
- 编译结果存入 storage 的 skills 表

使用示例：
    compiler = SkillCompiler(storage)
    skill = compiler.compile_from_entry(entry, name="HTTP429RetrySkill")
    # skill.steps 包含三步工作流*

### `SkillCompiler`

*Skill 编译器：从路由表节点生成专类 Skill。

支持按根分类自动选择 Skill-Builder 模板模式：
    tool / domain / workflow / memory / generic

如果调用方显式传入 templates，则优先使用传入模板。

Args:
    storage: 底层持久化存储
    default_templates: 默认步骤模板，可覆盖*

- 签名: `SkillCompiler(self, storage: 'Storage', default_templates: 'list[StepTemplate] | None' = None) -> 'None'`

公开成员：

  - 方法: `compile_by_id(category_id: 'str', name: 'str | None' = None, templates: 'list[StepTemplate] | None' = None, extra_tags: 'set[Tag] | None' = None) -> 'SpecializedSkill | None'` — 通过 category_id 查找并编译 Skill。
  - 方法: `compile_custom(skill_id: 'str', name: 'str', overview_map: 'LocalMindMap', steps: 'list[SkillStep]', tags: 'set[Tag] | None' = None) -> 'SpecializedSkill'` — 完全自定义地编译一个 Skill。
  - 方法: `compile_from_entry(entry: 'RoutingTableEntry', name: 'str | None' = None, templates: 'list[StepTemplate] | None' = None, extra_tags: 'set[Tag] | None' = None) -> 'SpecializedSkill'` — 从路由表节点编译生成 Skill。
  - 方法: `get_skill(skill_id: 'str') -> 'SpecializedSkill | None'` — 获取已编译的 Skill。
  - 方法: `get_skill_for_entry(entry: 'RoutingTableEntry') -> 'SpecializedSkill | None'` — 获取路由表条目关联的 Skill。
  - 方法: `get_skills_for_entries(entries: 'Iterable[RoutingTableEntry]') -> 'dict[str, SpecializedSkill]'` — 批量获取多个路由表条目关联的 Skill。

### `StepTemplate`

*单步模板定义。

每个模板描述了一个步骤的行为特征，编译器据此生成 SkillStep。*

- 签名: `StepTemplate(self, step_id: 'str', action: 'str', boundary_rules_suffix: 'str', precondition: 'str | None' = None, postcondition: 'str | None' = None, retry_policy: 'dict[str, Any] | None' = None) -> None`

公开成员：

  - 方法: `build_step(parent_map: 'LocalMindMap', step_counter: 'int') -> 'SkillStep'` — 根据父节点的 LocalMindMap 构建一个 SkillStep。

---

## `main_agent`

*主代理（前台 · 只读）— 错误查询、Skill 执行、未知举证。

职责边界（来自 AGENTS_01.md §3.1）：
- ✅ 读取路由表（精确分类匹配）
- ✅ 读取路由表（模糊标签匹配）
- ✅ 调用 Skill 工作流（只执行，不修改）
- ✅ 将未知错误举证包写入反馈暂存队列

- ❌ 创建/修改/删除路由表节点
- ❌ 创建/修改/删除 Skill
- ❌ 修改标签系统
- ❌ 执行 INSERT/UPDATE/DELETE 到路由表或 Skill 库

使用示例：
    agent = MainAgent(storage, pending_queue)

    # 精确查询
    result = agent.lookup_exact(error_signature="HTTP_429")

    # 模糊查询
    result = agent.lookup_fuzzy(tags={Tag("场景_第三方依赖")})

    # 执行 Skill
    outcome = agent.execute_skill(skill, context={"target": "api.example.com"})

    # 未知错误举证
    agent.report_unknown("GraphQL: Field not found", context, attempts=["retry"])*

### `LookupResult`

*查询结果。*

- 签名: `LookupResult(self, category_id: 'str', entry: 'RoutingTableEntry | None', skill: 'SpecializedSkill | None', match_type: 'str', note: 'str' = '') -> None`

### `MainAgent`

*主代理 — 前台只读组件。

Args:
    storage: 底层持久化存储
    pending_queue: 反馈暂存队列*

- 签名: `MainAgent(self, storage: 'Storage', pending_queue: 'PendingQueue') -> 'None'`

公开成员：

  - 方法: `execute_skill(skill: 'SpecializedSkill', context: 'dict[str, Any] | None' = None, executor: 'Any | None' = None) -> 'SkillExecutionResult'` — 执行 Skill 工作流。
  - 方法: `lookup_exact(category_id: 'str') -> 'LookupResult'` — 按 category_id 精确查询路由表节点和关联 Skill。
  - 方法: `lookup_fuzzy(required_tags: 'set[Tag]', root_category: 'str | None' = None, limit: 'int' = 5) -> 'list[LookupResult]'` — 通过标签组合进行模糊查询。
  - 方法: `lookup_min_cost(scenario_tags: 'set[Tag] | None' = None, exclude_tags: 'set[Tag] | None' = None, root_category: 'str | None' = None, limit: 'int' = 5) -> 'list[LookupResult]'` — 寻找最小代价方案（Gherkin F2 场景2）。
  - 方法: `report_unknown(error_stack: 'str', context: 'dict[str, Any] | None' = None, attempted_strategies: 'list[str] | None' = None, location_guess: 'str' = '', confidence: 'float' = 0.0) -> 'bool'` — 生成未知错误举证包并写入反馈暂存队列。

### `SkillExecutionResult`

*Skill 工作流执行结果。*

- 签名: `SkillExecutionResult(self, skill_id: 'str', skill_name: 'str', steps: 'list[SkillExecutionStepResult]' = <factory>, overall_success: 'bool' = False, total_steps: 'int' = 0, successful_steps: 'int' = 0) -> None`

公开成员：

  - 方法: `add_step_result(result: 'SkillExecutionStepResult') -> 'None'`
  - 属性: `all_succeeded` → `bool`

### `SkillExecutionStepResult`

*单步执行结果。*

- 签名: `SkillExecutionStepResult(self, step_id: 'str', action: 'str', success: 'bool', output: 'Any' = None, error: 'str | None' = None) -> None`

---

## `sub_agent`

*子代理（后台 · 只写）— 日志蒸馏 / 暂存消费 / 路由维护 / Skill 孵化。

职责边界（来自 AGENTS_01.md §3.2）：
- ✅ 扫描 DSH Session 日志，执行蒸馏
- ✅ 创建/更新/分裂/合并路由表节点
- ✅ 编译/更新/废弃 Skill
- ✅ 更新标签系统（遗传与变异）
- ✅ 消费反馈暂存队列

- ❌ 直接响应用户请求（用户交互由主代理负责）
- ❌ 修改人类锁定的根分类骨架
- ❌ 跳过 maintenance_log 直接修改节点

使用示例：
    agent = SubAgent(storage, pending_queue, log_source=log_reader)

    # 蒸馏
    new_entries = agent.distill()

    # 消费暂存队列
    processed = agent.consume_pending()

    # 路由表维护
    agent.maintain()

    # Skill 孵化
    agent.compile_skills(top_k=5)*

### `DistillationResult`

*蒸馏结果汇总。*

- 签名: `DistillationResult(self, new_entries: 'list[RoutingTableEntry]' = <factory>, updated_entries: 'list[RoutingTableEntry]' = <factory>, total_distilled: 'int' = 0, errors: 'list[str]' = <factory>) -> None`

### `DistilledFix`

*蒸馏出的已验证修复方案。*

- 签名: `DistilledFix(self, error_signature: 'str', fix_action: 'str', impact_scope: 'str', session_id: 'str', timestamp: 'datetime', confidence: 'float' = 1.0, subtype: 'str' = '') -> None`

### `FeedbackProcessingResult`

*暂存队列消费结果。*

- 签名: `FeedbackProcessingResult(self, processed_count: 'int' = 0, new_entries: 'list[RoutingTableEntry]' = <factory>, compiled_skills: 'list[SpecializedSkill]' = <factory>, errors: 'list[str]' = <factory>) -> None`

### `SubAgent`

*子代理 — 后台写操作组件。

Args:
    storage: 底层持久化存储
    pending_queue: 反馈暂存队列
    log_reader: 日志读取函数，签名为 Callable[[], Iterable[dict]]
                返回字典列表，每个字典包含 session_id / event_type / content 等字段*

- 签名: `SubAgent(self, storage: 'Storage', pending_queue: 'PendingQueue', log_reader: 'Callable[[], Iterable[dict[str, Any]]] | None' = None) -> 'None'`

公开成员：

  - 方法: `compile_skills(top_k: 'int' = 5, quality_delta_min: 'float' = 0.1) -> 'list[SpecializedSkill]'` — 为得分最高的 Top K 路由表节点编译/更新 Skill。
  - 方法: `consume_pending(batch_size: 'int' = 10) -> 'FeedbackProcessingResult'` — 消费反馈暂存队列中的举证包。
  - 方法: `distill() -> 'DistillationResult'` — 扫描 Session 日志，提取已验证的错误修复方案。
  - 方法: `maintain(split_threshold_top: 'int' = 3, split_consecutive: 'int' = 3, prune_threshold: 'float' = 0.1, prune_bottom_pct: 'float' = 0.1, quality_delta_min: 'float' = 0.1, split_min_samples: 'int' = 5, split_dominant_share: 'float' = 0.7) -> 'dict[str, Any]'` — 路由表维护：基于四维排序 + D1 知识增量质量评分触发分裂和剪枝。
  - 方法: `overlap_audit() -> 'list[dict[str, Any]]'` — Step 39：对路由表所有同根分类节点执行两两重叠检测。

---

## `sub_agent_pool`

*子代理池 — Agent-Builder 专用子代理工厂。

当某个根分类（root category）的路由表节点数超过阈值时，
自动创建该分类的专用子代理，实现按领域专业化。

设计原则（来自 agent-builder/AGENTS.md）：
    - 每个子代理拥有独立的 SOUL.md / IDENTITY.md / AGENTS.md
    - 子代理只处理自己负责的根分类
    - 子代理与主代理通过 Storage 共享数据层

使用示例：
    pool = SubAgentPool(storage, pending_queue, log_reader=my_reader)
    pool.auto_balance(threshold=50)  # 自动为超过 50 节点的根分类创建专用子代理
    pool.maintain()                  # 依次调用所有子代理的维护
    pool.compile_skills()            # 依次调用所有子代理的 Skill 编译*

### `SpecializedSubAgent`

*某个根分类的专用子代理。

职责：
    - 只处理指定根分类的路由表节点
    - 蒸馏时只提取该分类的错误方案
    - 维护时只做该分类的质量门禁与剪枝（BUG-39 修复：文档如实声明，
      分裂统一由通用 SubAgent.maintain() 执行，避免双代理重复分裂）
    - Skill 孵化时只编译该分类的 Skill

Args:
    root_category: 负责的根分类，如 "network" / "data_parsing"
    storage: 共享的持久化存储*

- 签名: `SpecializedSubAgent(self, root_category: 'str', storage: 'Storage') -> 'None'`

公开成员：

  - 属性: `category_prefix` → `str` — 该子代理负责的 category_id 前缀。
  - 方法: `compile_skills(top_k: 'int' = 5, quality_delta_min: 'float' = 0.1) -> 'list[SpecializedSkill]'` — 为该根分类下得分最高的节点编译 Skill。
  - 方法: `entry_count() -> 'int'` — 当前负责的分类下有多少个路由表节点。
  - 方法: `maintain(prune_threshold: 'float' = 0.1, prune_bottom_pct: 'float' = 0.1, quality_delta_min: 'float' = 0.1) -> 'dict[str, Any]'` — 维护该根分类下的路由表节点。

### `SubAgentPool`

*子代理池：管理通用子代理 + 专用子代理。

Agent-Builder 模式：
    - 一个通用 SubAgent 处理所有日志和反馈
    - 多个专用 SubAgent 按根分类专业化
    - auto_balance() 根据节点数量自动创建专用子代理

使用示例：
    pool = SubAgentPool(storage, pending_queue)
    pool.auto_balance(threshold=50)
    pool.maintain()
    pool.compile_skills()*

- 签名: `SubAgentPool(self, storage: 'Storage', pending_queue: 'PendingQueue', log_reader: 'Callable[[], Iterable[dict[str, Any]]] | None' = None) -> 'None'`

公开成员：

  - 方法: `auto_balance(threshold: 'int' = 50) -> 'list[str]'` — 自动平衡：为超过阈值的根分类创建专用子代理。
  - 方法: `compile_skills(top_k: 'int' = 5, quality_delta_min: 'float' = 0.1) -> 'dict[str, list[SpecializedSkill]]'` — 依次调用所有子代理编译 Skill。
  - 方法: `consume_pending() -> 'object'` — 委托通用子代理消费暂存队列。
  - 方法: `create_specialized(root_category: 'str') -> 'SpecializedSubAgent'` — 创建一个根分类的专用子代理。
  - 方法: `distill() -> 'object'` — 委托通用子代理执行蒸馏。
  - 方法: `get_specialized(root_category: 'str') -> 'SpecializedSubAgent | None'` — 获取指定根分类的专用子代理。
  - 方法: `maintain(prune_threshold: 'float' = 0.1, prune_bottom_pct: 'float' = 0.1, quality_delta_min: 'float' = 0.1) -> 'dict[str, Any]'` — 依次调用所有子代理执行维护。
  - 方法: `pool_summary() -> 'dict[str, Any]'` — 生成子代理池的概要统计。
  - 方法: `remove_specialized(root_category: 'str') -> 'None'` — 移除一个专用子代理（节点数减少后可能不再需要）。
  - 属性: `specialized_categories` → `list[str]` — 所有专用子代理负责的根分类列表。
  - 属性: `specialized_count` → `int` — 专用子代理数量。

---
