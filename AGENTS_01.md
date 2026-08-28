# AGENTS_01 — 受控自进化 AI Agent 框架 · 工程落地指南

> 本文档是「受控自进化 AI Agent 框架」的项目级工程指南，服务于从蓝图到 MVP 到成品的全生命周期。
> 与 AGENTS.md（平台级通用原则）形成分层管理：AGENTS.md 定义"如何工作"，本文档定义"如何落地本项目"。
> v1.0 · 2026-08 · 由 AI Agent 维护，持续演进

---

## 文档关系

```
AGENTS.md           ← 平台级：智能体通用工作原则（不修改）
总览.md             ← 蓝图级：框架完整设计（文字复述 + 全局/局部脑图）
Gherkin.md          ← 验收级：3 个 Feature 覆盖核心闭环（不修改，作为验收基准）
AGENTS_01.md        ← 落地级：本文档，工程实现指南 ← 你在这里
```

- **总览.md** 是"要造什么"（What）
- **Gherkin.md** 是"怎么才算造对了"（Verification）
- **本文档** 是"怎么造"（How）

三者构成 `What → Verification → How` 闭环，缺一不可。

---

## 一、项目核心哲学

### 1.1 两大基石

| 基石 | 含义 | 工程落点 |
|------|------|----------|
| **CQRS 读写分离** | 主代理（前台）只读不写；子代理（后台）只写不读 | 代码层通过接口契约强制隔离；主代理代码路径不得出现 `INSERT/UPDATE/DELETE` |
| **受控自主** | 子代理高度自治，但根分类骨架由人类锁定 | 根分类（`network` / `data_parsing` / `llm_inference` / `resource_exhaustion` / `permission`）硬编码于配置，不可被算法改写 |

### 1.2 四大数据资产

| 资产 | 描述 | 对应代码模块 |
|------|------|-------------|
| **规避洞察路由表** | 树状结构，存储错误分类、统计热力值、规避策略指针 | `routing_table.py` |
| **专类 Skills 库** | 子代理动态孵化的可执行工作流（DAG） | `skill_compiler.py` |
| **多维标签系统** | 三类强制前缀（`状态_`/`代价_`/`场景_`），支持模糊属性查询 | `tag_system.py` |
| **反馈暂存队列** | 未分类举证的暂存区，连接主代理与子代理 | `pending_queue.py` |

### 1.3 运转闭环

```
经验蒸馏 → 四维排序 → 地图演化 → Skill孵化 → 前台查询 → 未知反馈 → 后台再蒸馏
```

---

## 二、模块工程规范

### 2.1 路由表（Routing Table）

**职责**：存储错误分类树，每个节点附带局部思维导图。

**核心数据结构**：

```python
@dataclass
class LocalMindMap:
    """局部思维导图 — 每个路由表节点和 Skill 步骤的元数据"""
    node_id: str
    parent_path: str
    focus_description: str       # 本节点聚焦解决什么
    boundary_rules: str          # 绝对不管什么（防御边界）
    logic_signature: str         # 自然语言逻辑描述
    maintenance_log: list        # 变更记述：谁/何时/为何分裂/合并

@dataclass
class RoutingTableEntry:
    category_id: str                     # 如 "network.rate_limit.429"
    stats: dict                          # freq / impact / trend / recover_cost
    local_map: LocalMindMap
    tags: set                            # 状态_*/代价_*/场景_*
    primary_skill_id: str | None         # 指向孵化好的 Skill
```

**工程约束**：

- `category_id` 使用点号分隔的层级命名（如 `network.timeout.connect`），第一级必须属于人类锁定的根分类
- `boundary_rules` 必须是非空字符串，不可省略（这是本框架的执念核心）
- 新增/删除/合并节点必须在 `maintenance_log` 中追加记录
- 路由表实现必须支持**模糊标签查询**（非精确分类匹配），如"查所有 `代价_低消耗` + `场景_第三方依赖`"

### 2.2 四维排序计算器

**公式**：

```
综合优先级 = Freq × 0.25 + Impact × 0.35 + Trend × 0.20 + Recover_Cost × 0.20
```

加时间衰减因子（半衰期约 7 天）：

```
衰减因子 = 2^(-days_since_last_seen / 7)
最终得分 = 综合优先级 × 衰减因子
```

**工程约束**：

- `Freq`（频率）：过去 N 天该节点被命中的次数（N 默认 30）
- `Impact`（影响度）：修复后恢复的成功率，范围 0–1
- `Trend`（趋势）：最近 7 天相比前 7 天的增长率，范围 -1 到 +1
- `Recover_Cost`（恢复代价）：修复所需的平均工具调用次数或时间成本，反向归一化为 0–1
- 所有指标必须可追溯来源，不可硬编码

### 2.3 地图分裂与剪枝

**触发条件**：

| 动作 | 触发条件 | 操作 |
|------|----------|------|
| **分裂（Split）** | 父节点综合优先级连续 3 次进入 Top 3，且某子分类占比 > 70% | 在父节点下创建子节点，分配独立 `boundary_rules` |
| **剪枝合并（Merge）** | 节点长期垫底（连续 2 个评估周期排名末位 10%） | 合并到最近邻节点，记述合并原因 |

**工程约束**：

- 分裂后父节点的 `maintenance_log` 必须追加 `"action: auto_split, reason: ..."`
- 子节点默认遗传父节点的所有标签，但允许算法根据新节点的统计特征进行**变异**（如移除 `代价_高延迟`）
- 分裂不得跨越根分类边界（人类锁定层不可变）

### 2.4 标签系统

**三类强制前缀**：

| 前缀 | 示例值 | 用途 |
|------|--------|------|
| `状态_` | `稳定` / `实验性` / `废弃` | 标记节点的置信度与成熟度 |
| `代价_` | `高延迟` / `低消耗` / `中消耗` | 标记执行成本，供主代理选择最小代价方案 |
| `场景_` | `第三方依赖` / `内部微服务` / `本地计算` | 标记适用场景，供主代理模糊匹配 |

**工程约束**：

- 所有标签必须带前缀，裸标签（如 `"稳定"`）禁止使用
- 标签支持**遗传**（父节点 → 子节点）和**变异**（子节点覆盖或移除遗传标签）
- 标签查询支持 AND 逻辑（如 `状态_稳定 AND 代价_低消耗`）

### 2.5 Skill 编译器

**职责**：基于路由表 Top K 分类，自动构建/更新专类 Skill 工作流（DAG）。

**核心数据结构**：

```python
@dataclass
class SkillStep:
    step_id: str
    action: str                      # 核心动作描述
    local_map: LocalMindMap          # 步骤局部地图
    precondition: str | None         # 前置条件
    postcondition: str | None        # 后置条件
    retry_policy: dict | None        # 重试策略

@dataclass
class SpecializedSkill:
    skill_id: str
    name: str                        # 如 "GraphQLFieldFixSkill"
    overview_map: LocalMindMap       # 继承自路由表节点
    steps: list[SkillStep]
    tags: set
```

**工程约束**：

- Skill 的 `overview_map` 必须继承自对应路由表节点的 `local_map`
- 每个 Skill 步骤必须有独立的 `local_map`，记述本步骤的聚焦目标和边界
- Skill 步骤的 `boundary_rules` 必须精确（如"仅校验 SSL 证书，不处理 TLS 握手"）
- Skill 编译器必须支持**增量更新**：路由表节点变化时，只编译受影响的 Skill，不重建全部

### 2.6 反馈暂存队列（Pending Queue）

**职责**：主代理遇到未知错误时，将举证包异步写入暂存区；子代理定时消费。

**核心数据结构**：

```python
@dataclass
class UnclassifiedFailurePackage:
    error_stack: str                 # 完整错误栈
    context_snapshot: dict           # 上下文快照（session_id / 工具链 / 请求摘要）
    attempted_strategies: list[str]  # 已尝试的失败方案
    location_guess: str              # 猜测归属（如 "data_parsing"）
    confidence: float                # 置信度（0–1）
    timestamp: datetime
```

**工程约束**：

- 主代理写入暂存区后**立即返回**，不阻塞用户
- 子代理消费时必须校验新分类与现有节点的重叠率 < 70% 才允许创建
- 暂存区必须有容量上限和过期策略（默认保留 7 天）

---

## 三、主代理与子代理的协作品格

### 3.1 主代理（前台 · 只读）

**允许操作**：

- 读取路由表（精确分类匹配）
- 读取路由表（模糊标签匹配）
- 调用 Skill 工作流（只执行，不修改）
- 将未知错误举证包写入反馈暂存队列

**禁止操作**：

- 创建/修改/删除路由表节点
- 创建/修改/删除 Skill
- 修改标签系统
- 执行 `INSERT` / `UPDATE` / `DELETE` 到路由表或 Skill 库

### 3.2 子代理（后台 · 只写）

**允许操作**：

- 扫描 DSH Session 日志，执行蒸馏
- 创建/更新/分裂/合并路由表节点
- 编译/更新/废弃 Skill
- 更新标签系统（遗传与变异）
- 消费反馈暂存队列

**禁止操作**：

- 直接响应用户请求（用户交互由主代理负责）
- 修改人类锁定的根分类骨架
- 跳过 `maintenance_log` 直接修改节点

### 3.3 异步通信协议

```
主代理 ──[写]──→ 反馈暂存队列
子代理 ──[读]──→ 反馈暂存队列
       ──[写]──→ 路由表 / Skill 库
       ──[读]──→ DSH Session 日志
```

**工程约束**：

- 反馈暂存队列是主代理与子代理之间**唯一**的写入通道
- 子代理对路由表的写操作必须**批量化**，避免频繁单条写入导致锁竞争
- 子代理运行周期建议 15–60 分钟一次，不可实时（避免与主代理争抢资源）

---

## 四、开发流程（基于 Gherkin TDD）

### 4.1 MVP 落地优先级

根据 Gherkin.md 的建议，落地顺序如下：

| 优先级 | 范围 | 内容 | 状态 |
|--------|------|------|------|
| **P0** | Feature 1 场景 1 + Feature 2 场景 1 | 基础闭环：能修已知错 | ⬜ |
| **P1** | Feature 3 | 自进化灵魂：未知反馈举证 | ⬜ |
| **P2** | Feature 1 场景 2 + Feature 2 场景 2 | 进阶：地图分裂 + 标签模糊匹配 | ⬜ |

### 4.2 每轮开发的步骤

1. **读 Gherkin**：确认当前要实现的 Feature/Scenario
2. **写接口契约**：先定义 Python dataclass / JSON Schema
3. **写单元测试**：覆盖 Gherkin 场景（正常 / 异常 / 边界 / 权限）
4. **写实现**：基于接口契约和测试
5. **运行测试**：全部通过才算完成
6. **更新维护日志**：在代码注释或文档中记录本次变更

### 4.3 测试覆盖要求

| 测试层级 | 工具 | 要求 |
|----------|------|------|
| 单元测试 | `pytest` | 每个模块 ≥ 80% 行覆盖率，核心函数 ≥ 90% |
| 验收测试 | `pytest-bdd` | 将 Gherkin Feature 转为可执行测试 |
| 集成测试 | `pytest` | 验证主代理 ↔ 暂存队列 ↔ 子代理的数据流 |

### 4.4 Gherkin 场景补充

Gherkin.md 目前只覆盖了正常场景，后续需补充：

- **异常场景**：如路由表为空时主代理的行为、暂存队列满时的降级
- **边界场景**：如置信度 = 0 时 `location_guess` 的处理、标签为空的节点匹配
- **权限场景**：子代理尝试修改根分类骨架时的拒绝逻辑

补充后的 Feature 应回写到 Gherkin.md 或本文档的附录中。

---

## 五、技术栈建议

| 层次 | 推荐技术 | 理由 |
|------|----------|------|
| 语言 | Python 3.10+ | 数据科学友好、DSH 生态原生支持 |
| 数据存储 | SQLite（MVP）→ PostgreSQL（生产） | 树状路由表 + JSON 字段支持 |
| 任务队列 | 内存队列（MVP）→ Redis / RabbitMQ（生产） | 反馈暂存 + 子代理任务调度 |
| 测试 | `pytest` + `pytest-bdd` + `pytest-cov` | Gherkin 驱动 + 覆盖率门禁 |
| 静态检查 | `ruff` + `mypy --strict` | 0 error 0 warning |
| 文档生成 | `generate-api-docs.py` | 以代码为唯一真相源 |

---

## 六、质量门禁

所有代码合并前必须通过：

- [ ] `pytest` 100% 通过（不允许 skip / xfail）
- [ ] `ruff check` 0 error
- [ ] `mypy --strict` 0 error
- [ ] 核心模块（标记 `@critical`）行覆盖率 ≥ 90%，分支 ≥ 85%
- [ ] Gherkin 验收测试全部通过
- [ ] 无硬编码凭证（`grep -rE "(key|secret|password|token)\s*=\s*['\"]"` 为空）
- [ ] 圈复杂度 ≤ 10 / 函数，函数长度 ≤ 50 行

详细阈值见 CLAUDE.md「质量门禁标准」。

---

## 七、维护日志

| 日期 | 版本 | 变更内容 | 维护者 |
|------|------|----------|--------|
| 2026-08 | v1.0 | 初版：基于总览.md 蓝图 + Gherkin.md 验收标准，确立项目工程指南 | AI Agent |

---

> **使用说明**：本文档是项目落地的唯一工程指南。新增模块、修改数据结构、调整工作流时，必须同步更新本文档对应章节，并在维护日志中记录。