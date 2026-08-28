# Skill 质量层架构文档（Phase 10–13）

> **版本**：v1.4  
> **完成日期**：2026-08  
> **来源**：`docs/工具介绍.md`（Skill-Creator / Skill-Judge / Skill-Builder / Agent-Builder 四份元技能）  
> **设计蓝本**：本机用户主目录下的 `.agents/skills/{skill-judge,skill-creator,skill-builder,agent-builder}/`（即 `%USERPROFILE%\.agents\skills\...`）

---

## 一、质量层整体架构

```
        ┌──────────────────────────────────────────────────────┐
        │                 路由表路由表 (RoutingTable)            │
        │     每节点带 LocalMindMap（边界/逻辑/维护日志）         │
        └──────────────┬───────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │                 │
              ▼                 ▼
   ┌─────────────────┐  ┌─────────────────┐
   │  SkillCompiler   │  │  SubAgentPool    │
   │  (Skill 编译层)   │  │  (子代理工厂层)   │
   └───────┬─────────┘  └───────┬─────────┘
           │                    │
           ▼                    ▼
   ┌─────────────────┐  ┌─────────────────┐
   │ SpecializedSkill│  │ SubAgentPool     │
   │ (Tool/Domain/   │  │ ├─ _general      │
   │  Workflow/Memory│  │ │   (通用)        │
   │  + tools + ctx) │  │ └─ _specialized  │
   └─────────────────┘  │    [{root}]      │
                        └─────────────────┘
```

### 质量流

```
SubAgent.maintain()          SubAgent.compile_skills()
     │                            │
     ▼                            ▼
  D1 评分              D1 评分门禁
  (quality_gated)     (skill_compile_skipped)
     │                            │
     ▼                            ▼
  标记低质量节点         跳过低质量节点
  写入 maintenance_log   写入 maintenance_log
     │                            │
     └──────────┬─────────────────┘
                ▼
         路由表节点质量分级
         ├─ expert  (δ≥0.5) → 保留
         ├─ adequate(δ≥0.3) → 可接受
         ├─ poor    (δ≥0.1) → 标记待改进
         └─ redundant(δ<0.1)→ 加入剪枝候选
```

---

## 二、Phase 10 — Skill-Judge D1 知识增量评分

### 2.1 核心公式

```
知识增量 (delta) = E / (E + A + R)

其中：
  E = Expert 知识信号（具体策略、决策树、反模式、边界案例）
  A = Activation 知识信号（通用提醒、已知概念标注）
  R = Redundant 知识信号（"处理X"、"修复X"、"检查X"等空话）
```

### 2.2 模式定义

| 类型 | 正则特征 | 示例 |
|------|---------|------|
| **E（专家）** | `\d{3}`, `指数退避`, `禁止`, `当.*时`, `fallback` | `禁止使用连接重试；指数退避 2^n ms` |
| **A（激活）** | `处理`, `修复`, `建议`, `注意` | `建议设置超时 10s` |
| **R（冗余）** | `^仅处理`, `^聚焦.*修复$`, `^待优化`, `^自动.*生成` | `仅处理网络相关问题，不处理其他错误类型` |

### 2.3 质量等级

| 等级 | 阈值 | 处理 |
|------|------|------|
| `expert` | δ ≥ 0.5 | 保留 |
| `adequate` | δ ≥ 0.3 | 可接受 |
| `poor` | δ ≥ 0.1 | 标记待改进 |
| `redundant` | δ < 0.1 | 加入剪枝候选 |

### 2.4 集成点

```python
# src/quality_scorer.py
class NodeQualityScorer:
    def score(self, entry: RoutingTableEntry) -> NodeQualityScore

# src/sub_agent.py
def maintain(self, ..., quality_delta_min: float = 0.1):
    score = self._quality_scorer.score(entry)
    if score.knowledge_delta < quality_delta_min:
        # 标记为 quality_gated → 写入 maintenance_log

def compile_skills(self, ..., quality_delta_min: float = 0.1):
    score = self._quality_scorer.score(entry)
    if score.knowledge_delta < quality_delta_min:
        # 跳过编译 → 写入 skill_compile_skipped
```

---

## 三、Phase 11 — Skill-Creator 质量评分门禁

### 3.1 compile_skills() 改造

```python
def compile_skills(self, top_k: int = 5, quality_delta_min: float = 0.1):
    for breakdown in self._rt.top_k(k=top_k):
        entry = self._storage.get_routing_entry(breakdown.category_id)
        if entry.primary_skill_id is not None:
            continue  # 已有 Skill，跳过
        score = self._quality_scorer.score(entry)
        if score.knowledge_delta < quality_delta_min:
            entry.local_map.append_log("skill_compile_skipped", ...)
            self._storage.upsert_routing_entry(entry)
            continue  # 低质量节点跳过编译
        skill = self._compiler.compile_from_entry(entry)
        compiled.append(skill)
```

### 3.2 效果

当前子代理自动生成的节点（`boundary_rules = "基于反馈举证自动生成"`）会被自动标记为 `redundant`，不再被编译为 Skill。

---

## 四、Phase 12 — Skill-Builder 模板模式适配

### 4.1 模式映射

| 根分类 | 模式 | 步骤 | 默认工具集 |
|-------|------|------|-----------|
| `network` | **Tool** | 参数校验 → 带重试执行 → 结果验证 | `http_client`, `retry` |
| `data_parsing` | **Domain** | 格式检测 → 解析负载 → 输出校验 | `json_parser`, `xml_parser` |
| `llm_inference` | **Workflow** | 预处理输入 → 运行推理 → 后处理输出 | `llm_api`, `token_counter` |
| `permission` | **Memory** | 策略查询 → 权限评估 → 决策记录 | `memory_store`, `policy_engine` |
| `resource_exhaustion` | **Tool** | 同 network | `http_client`, `retry` |

### 4.2 SkillCompiler 改造

```python
class SkillCompiler:
    def _select_pattern(self, entry: RoutingTableEntry) -> str:
        root = entry.category_id.split(".")[0]
        return _PATTERN_BY_ROOT.get(root, "generic")

    def _infer_tools(self, entry, pattern):
        # 基于模式 + 边界关键词推断工具集
        ...

    def _infer_context_keys(self, entry):
        # 从 focus/boundary 推断上下文依赖
        ...
```

### 4.3 SpecializedSkill 扩展

```python
@dataclass
class SpecializedSkill:
    skill_id: str
    name: str
    pattern: str = "generic"
    overview_map: LocalMindMap
    steps: list[SkillStep]
    tools: list[str]         # 新增：运行时工具集
    context_keys: list[str]  # 新增：上下文键
    tags: set[Tag]
```

---

## 五、Phase 13 — Agent-Builder 子代理专业化

### 5.1 SubAgentPool 架构

```
SubAgentPool(storage, pending_queue)
    ├── _general_agent: SubAgent         # 通用子代理
    │   ├── distill()      → 处理所有日志
    │   ├── maintain()     → 处理所有节点
    │   └── compile_skills()
    │
    └── _specialized: dict[root → SpecializedSubAgent]
        ├── create_specialized(root)
        ├── auto_balance(threshold=50)
        ├── maintain()    → 只处理本分类节点
        └── compile_skills() → 只编译本分类 Skill
```

### 5.2 自动平衡

```python
pool.auto_balance(threshold=50)
# 当某根分类节点数 > 50 → 自动创建专用子代理
# 创建后专用子代理只处理该根分类的路由表节点
```

### 5.3 委托链

```
pool.maintain()
    → 通用子代理.maintain()  # 全量质量评分 + 剪枝
    → 专用子代理.maintain()  # 本分类质量评分

pool.compile_skills()
    → 通用子代理.compile_skills()
    → 专用子代理.compile_skills()
```

---

## 六、模块依赖关系

```
                    ┌──────────────┐
                    │  models.py    │
                    │  NodeQuality │
                    │  Score       │
                    │  Specialized │
                    │  Skill       │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
    ┌────────────────┐ ┌────────────┐ ┌──────────────┐
    │ quality_scorer │ │ sub_agent  │ │ skill_compil │
    │                │ │            │ │ er           │
    │ NodeQualityScorer│ │ SubAgent  │ │ SkillCompiler│
    └───────┬────────┘ └────┬───────┘ └──────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                             ▼
                    ┌────────────────┐
                    │ sub_agent_pool │
                    │                │
                    │ SubAgentPool   │
                    └────────────────┘
```

---

## 七、质量门禁

| 检查 | 结果 |
|------|------|
| `ruff check src/` | ✅ All checks passed |
| `mypy src/ --strict` | ✅ 0 error（新增模块） |
| `pytest tests/` | ✅ 287 passed |
| 新增测试文件 | `test_quality_scorer.py`（9 用例）+ `test_sub_agent_pool.py`（24 用例）|

---

## 八、文件清单

| 文件 | 职责 |
|------|------|
| `src/quality_scorer.py` | D1 知识增量评分器 |
| `src/sub_agent_pool.py` | SubAgentPool 工厂 + SpecializedSubAgent |
| `src/sub_agent.py` | `maintain()` + `compile_skills()` 质量门禁 |
| `src/skill_compiler.py` | 4 种模式模板 + `_select_pattern()` + `_infer_tools()` |
| `src/models.py` | `NodeQualityScore` + `SpecializedSkill.pattern/tools/context_keys` |
| `tests/test_quality_scorer.py` | 评分器单元测试 |
| `tests/test_sub_agent_pool.py` | 子代理池 + Skill 运行时测试 |
| `skill-todo.md` | Phase 10–13 完整规划 |

---

> **设计原则**：质量层先做评分门禁（Phase 10-11，最小改动），再做模式适配（Phase 12，中改动），最后做子代理专业化（Phase 13，大改动）。层层递进，每层都建立在上一层的质量保障之上。
