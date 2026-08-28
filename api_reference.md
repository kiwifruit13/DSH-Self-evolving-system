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
- 血缘关系（node_id + parent_path）
- 完整变更史（maintenance_log）*

- 签名: `LocalMindMap(self, node_id: 'str', parent_path: 'str', focus_description: 'str', boundary_rules: 'str', logic_signature: 'str', maintenance_log: 'list[MaintenanceLog]' = <factory>) -> None`

### `MaintenanceLog`

*单条维护日志条目。*

- 签名: `MaintenanceLog(self, timestamp: 'datetime', action: 'str', reason: 'str', actor: 'str') -> None`

### `RoutingTableEntry`

*路由表条目 — 规避洞察路由表的核心数据单元。

category_id 使用点号分隔的层级命名，如 'network.rate_limit.429'。
第一级必须属于 ROOT_CATEGORIES（人类锁定层）。*

- 签名: `RoutingTableEntry(self, category_id: 'str', stats: 'dict[str, float]', local_map: 'LocalMindMap', tags: 'set[Tag]' = <factory>, primary_skill_id: 'str | None' = None) -> None`

### `SkillStep`

*Skill 中的单一步骤，携带步骤局部地图。*

- 签名: `SkillStep(self, step_id: 'str', action: 'str', local_map: 'LocalMindMap', precondition: 'str | None' = None, postcondition: 'str | None' = None, retry_policy: 'dict[str, Any] | None' = None) -> None`

### `SpecializedSkill`

*专类 Skill 工作流（DAG）。overview_map 继承自路由表节点。*

- 签名: `SpecializedSkill(self, skill_id: 'str', name: 'str', overview_map: 'LocalMindMap', steps: 'list[SkillStep]' = <factory>, tags: 'set[Tag]' = <factory>) -> None`

### `Tag`

*带前缀的标签。value 必须形如 '状态_稳定'、'代价_低消耗' 等。

使用 frozen dataclass 保证不可变性。*

- 签名: `Tag(self, value: 'str') -> None`

### `TagPrefix`

*三类强制前缀。所有 Tag.value 必须以这三种之一开头。*

- 签名: `TagPrefix(self, args, kwargs)`

### `UnclassifiedFailurePackage`

*主代理遇到未知错误时生成的举证包，异步写入反馈暂存队列。*

- 签名: `UnclassifiedFailurePackage(self, error_stack: 'str', context_snapshot: 'dict[str, Any]', attempted_strategies: 'list[str]' = <factory>, location_guess: 'str' = '', confidence: 'float' = 0.0, timestamp: 'datetime' = <factory>) -> None`

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

使用示例：
    calc = ScoreCalculator()
    score = calc.compute_final_score(
        stats={"freq": 50, "impact": 0.85, "trend": 0.3, "recover_cost": 2},
        days_since_last_seen=3,
    )*

### `ScoreBreakdown`

*单节点得分明细，用于调试和日志。*

- 签名: `ScoreBreakdown(self, category_id: 'str', freq_normalized: 'float', impact_normalized: 'float', trend_normalized: 'float', cost_normalized: 'float', priority: 'float', decay_factor: 'float', final_score: 'float', days_since_last_seen: 'float') -> None`

### `ScoreCalculator`

*四维排序计算器。

所有 normalize_* 方法均为纯函数，便于测试和调试。*

- 签名: `ScoreCalculator(self, config: 'ScoreConfig | None' = None) -> 'None'`

### `ScoreConfig`

*排序计算器的可调参数。*

- 签名: `ScoreConfig(self, freq_weight: 'float' = 0.25, impact_weight: 'float' = 0.35, trend_weight: 'float' = 0.2, cost_weight: 'float' = 0.2, half_life_days: 'float' = 7.0, freq_window_days: 'int' = 30, freq_max: 'float' = 1000.0, cost_max: 'float' = 10.0) -> None`

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

重叠率计算维度：
1. 错误签名相似度（Levenshtein 距离归一化）
2. 根分类匹配（同根分类加分）
3. 边界规则重叠度（TF-IDF 简化版）

综合重叠率 = 0.4 * 签名相似度 + 0.3 * 根分类匹配 + 0.3 * 边界重叠度

使用示例：
    checker = OverlapChecker(storage)
    overlap = checker.check("network.http_500", "network.http_503")
    if overlap < 0.7:
        # 允许创建
    else:
        # 拒绝，建议合并到已有节点*

### `OverlapCheckResult`

*重叠率检查结果。*

- 签名: `OverlapCheckResult(self, candidate_id: 'str', candidate_signature: 'str', candidate_boundary: 'str', threshold: 'float' = 0.7, max_overlap: 'float' = 0.0, max_overlap_with: 'str | None' = None, all_scores: 'list[dict[str, Any]] | None' = None) -> 'None'`

### `OverlapChecker`

*重叠率校验器。

Args:
    storage: 底层持久化存储
    threshold: 重叠率阈值，默认 0.7（70%）
    signature_weight: 签名相似度权重，默认 0.4
    root_weight: 根分类匹配权重，默认 0.3
    boundary_weight: 边界重叠度权重，默认 0.3*

- 签名: `OverlapChecker(self, storage: 'Storage', threshold: 'float' = 0.7, signature_weight: 'float' = 0.4, root_weight: 'float' = 0.3, boundary_weight: 'float' = 0.3) -> 'None'`

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
    pending_queue: 反馈暂存队列
    overlap_threshold: 重叠率阈值，默认 0.7*

- 签名: `OfflinePlanner(self, storage: 'Storage', pending_queue: 'PendingQueue', overlap_threshold: 'float' = 0.7) -> 'None'`

### `PlanningDecision`

*单次规划决策记录。*

- 签名: `PlanningDecision(self, package: 'UnclassifiedFailurePackage', candidate_category_id: 'str' = '', candidate_signature: 'str' = '', candidate_boundary: 'str' = '', phases: 'list[PlanningPhase]' = <factory>, created_entry: 'RoutingTableEntry | None' = None, compiled_skill: 'SpecializedSkill | None' = None, overlap_result: 'dict[str, Any] | None' = None, rejected: 'bool' = False, rejection_reason: 'str' = '', timestamp: 'datetime' = <factory>) -> None`

### `PlanningPhase`

*单阶段规划结果。*

- 签名: `PlanningPhase(self, phase: 'str', status: 'str', reason: 'str' = '', data: 'dict[str, Any]' = <factory>) -> None`

### `PlanningReport`

*整批规划结果汇总。*

- 签名: `PlanningReport(self, total_processed: 'int' = 0, accepted: 'int' = 0, rejected: 'int' = 0, decisions: 'list[PlanningDecision]' = <factory>, errors: 'list[str]' = <factory>) -> None`

---

## `routing_table`

*路由表模块 — 规避洞察路由表的核心操作层。

职责：
- CRUD：基于 Storage 的路由表条目增删改查
- 排序：基于 ScoreCalculator 的四维排序 + 时间衰减
- 分裂（Split）：高频父节点自动下钻，生成子节点
- 剪枝（Prune）：长期垫底节点自动合并

所有写操作都会在 local_map.maintenance_log 中追加记录。*

### `RoutingTable`

*路由表操作层。

Args:
    storage: 底层持久化存储
    score_config: 排序计算器配置，默认使用标准权重*

- 签名: `RoutingTable(self, storage: 'Storage', score_config: 'ScoreConfig | None' = None) -> 'None'`

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

Args:
    storage: 底层持久化存储
    default_templates: 默认步骤模板，可覆盖*

- 签名: `SkillCompiler(self, storage: 'Storage', default_templates: 'list[StepTemplate] | None' = None) -> 'None'`

### `StepTemplate`

*单步模板定义。

每个模板描述了一个步骤的行为特征，编译器据此生成 SkillStep。*

- 签名: `StepTemplate(self, step_id: 'str', action: 'str', boundary_rules_suffix: 'str', precondition: 'str | None' = None, postcondition: 'str | None' = None, retry_policy: 'dict[str, Any] | None' = None) -> None`

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

### `SkillExecutionResult`

*Skill 工作流执行结果。*

- 签名: `SkillExecutionResult(self, skill_id: 'str', skill_name: 'str', steps: 'list[SkillExecutionStepResult]' = <factory>, overall_success: 'bool' = False, total_steps: 'int' = 0, successful_steps: 'int' = 0) -> None`

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

- 签名: `DistilledFix(self, error_signature: 'str', fix_action: 'str', impact_scope: 'str', session_id: 'str', timestamp: 'datetime', confidence: 'float' = 1.0) -> None`

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

---
