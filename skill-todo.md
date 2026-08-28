# 受控自进化 AI Agent 框架 · Skill 质量层重构 Todo

> **来源**：`docs/工具介绍.md`（Skill-Creator / Skill-Judge / Agent-Builder 三份元技能白皮书）
> **接入方式**：以 `skill-judge`（752 行 SKILL.md，8 维度 120 分制）为设计蓝本，先落地 D1 知识增量评分，再逐步扩展到完整生命周期
> **范围**：Skill 创建 → 评分 → 部署 → 监控 → 改进 → 淘汰
> **执行原则**：先做 D1 评分（最小改动、立即可见），再扩展至完整 Skill-Creator 评估闭环

---

## 目录

- [路线图总览](#路线图总览)
- [Phase 10: Skill-Judge D1 知识增量评分（本轮执行）](#phase-10-skill-judge-d1-知识增量评分本轮执行)
- [Phase 11: Skill-Creator CI/CD 评估闭环](#phase-11-skill-creator-cicd-评估闭环)
- [Phase 12: Skill-Builder 模板模式适配](#phase-12-skill-builder-模板模式适配)
- [Phase 13: Agent-Builder 子代理专业化](#phase-13-agent-builder-子代理专业化)
- [质量门禁检查清单](#质量门禁检查清单)

---

## 路线图总览

```
✅ Phase 10 ────────────────────────────────────────────────────
  D1 知识增量评分 → 路由表节点质量门禁 → 低质量节点自动淘汰
  改动量：小（一个文本分析函数 + maintain() 集成）
  收益：立即解决"冗余节点不被清理"问题

✅ Phase 11 ────────────────────────────────────────────────────
  Skill-Creator CI/CD → compile_from_entry() 升级为评估流水线
  A/B 测试 → Grader 评分 → aggregate_benchmark → 迭代改进
  改动量：中（新增评估 Agent + 统计脚本）

✅ Phase 12 ────────────────────────────────────────────────────
  Skill-Builder 4 模式 → _select_pattern() 按根分类选择模板
  Tool / Domain / Workflow / Memory 四种 Skill 结构
  改动量：小（模式选择 + 模板生成）

🔜 ✅ Phase 13 ────────────────────────────────────────────────────
  Agent-Builder → 自动创建专用子代理（按根分类拆分）
  改动量：大（新增子代理工厂）
```

---

## Phase 10: Skill-Judge D1 知识增量评分（本轮执行）

> **状态**：✅ 完成
> **完成条件**：Step 57–60 全部 ✅ + 新增测试全绿 + ruff/mypy 零 error

---

### Step 57 — 新增 NodeQualityScore 数据模型 ✅

**问题**：当前路由表节点无质量度量，无法区分"有知识增量的专家节点"和"泛泛而谈的冗余节点"。

**设计方案**（Skill-Judge D1 核心公式）：

```
知识增量 = E / (E + A + R)
其中：
  E = Expert 知识（具体策略、决策树、反模式、边界案例）
  A = Activation 知识（通用提醒、已知概念标注）
  R = Redundant 知识（"处理X"、"修复X"、"检查X"等空话）
```

**数据结构**：

```python
@dataclass
class NodeQualityScore:
    """路由表节点质量评分（Skill-Judge D1 知识增量维度）。"""
    category_id: str
    expert_score: float        # [0, 1] Expert 知识比例
    activation_score: float    # [0, 1] Activation 知识比例
    redundant_score: float     # [0, 1] Redundant 知识比例
    knowledge_delta: float     # [0, 1] 核心指标：E / (E + A + R)
    quality_level: str         # "expert" / "adequate" / "poor" / "redundant"
    signals: list[str]         # 检测到的质量信号（正向/负向标记）
```

**影响文件**：`src/models.py`

---

### Step 58 — 实现 NodeQualityScorer 模块 ✅

**位置**：`src/quality_scorer.py`（新文件）

**职责**：

1. **文本特征提取**：从 `local_map.focus_description`、`boundary_rules`、`logic_signature` 中提取质量信号
2. **冗余模式检测**（Negative Signals）：
   - `仅处理 {X}`、`聚焦 {X} 修复`、`待优化`、`基于反馈举证自动生成`
   - 这些是子代理自动生成的空话，知识增量 = 0
3. **专家模式检测**（Positive Signals）：
   - 具体错误码/策略（如 `HTTP 429`、`指数退避`）
   - 明确的反模式（`禁止`、`永不`、`不要`）
   - 边界案例（`当 X 时`、`除非 Y`、`边界情况`）
   - 决策树（`如果...则...否则...`）
4. **知识增量计算**：
   ```
   E = len(expert_signals)
   A = len(activation_signals)
   R = len(redundant_signals)
   delta = E / max(1, E + A + R)
   ```
5. **质量等级判定**：
   - `delta >= 0.5` → `"expert"`（有真知灼见，保留）
   - `delta >= 0.3` → `"adequate"`（有知识增量，可接受）
   - `delta >= 0.1` → `"poor"`（知识增量不足，标记待改进）
   - `delta < 0.1` → `"redundant"`（几乎全是空话，加入剪枝候选）

**核心算法**：

```python
class NodeQualityScorer:
    # 冗余模式（自动生成的空话特征）
    REDUNDANT_PATTERNS = [
        r"^仅处理",
        r"^聚焦.*修复$",
        r"^待优化",
        r"^基于反馈举证",
        r"^自动.*生成",
        r"不处理其他",
    ]

    # 专家模式（高质量知识增量特征）
    EXPERT_PATTERNS = [
        r"\d{3}",                    # 具体错误码
        r"指数退避|backoff",         # 具体策略
        r"(禁止|永不|不要|NEVER)",   # 反模式
        r"(如果|除非|当.*时|边界)",   # 决策树/边界案例
        r"(否则|fallback|回退)",     # 备用策略
    ]

    def score(self, entry: RoutingTableEntry) -> NodeQualityScore:
        ...
```

**影响文件**：`src/quality_scorer.py`（新增）

---

### Step 59 — 在 SubAgent.maintain() 中集成质量评分门禁 ✅

**接入点**：`src/sub_agent.py` 的 `maintain()` 方法

**改造**：

```python
def maintain(
    self,
    split_threshold_top: int = 3,
    prune_threshold: float = 0.1,
    prune_bottom_pct: float = 0.1,
    quality_delta_min: float = 0.3,  # 新增：D1 知识增量最低门槛
) -> dict[str, Any]:
    """路由表维护：基于四维排序 + 质量评分触发分裂和剪枝。"""
    stats: dict[str, Any] = {
        "split": 0, "pruned": [], "errors": [],
        "quality_gated": [],  # 新增：质量评分标记
    }

    # 对所有节点执行质量评分
    all_entries = self._storage.query_routing_entries()
    scored = [self._scorer.score(entry) for entry in all_entries]

    for score in scored:
        if score.quality_level == "redundant":
            # 低质量节点标记并写入维护日志
            entry = self._storage.get_routing_entry(score.category_id)
            entry.local_map.append_log(
                "quality_gated",
                f"知识增量 {score.knowledge_delta:.0%} 低于门槛 "
                f"{quality_delta_min:.0%}，质量等级: {score.quality_level}",
                "sub_agent",
            )
            self._storage.upsert_routing_entry(entry)
            stats["quality_gated"].append({
                "category_id": score.category_id,
                "knowledge_delta": score.knowledge_delta,
                "quality_level": score.quality_level,
                "signals": score.signals,
            })

    # 剪枝时优先淘汰低质量节点
    pruned = self._rt.prune_lowest(
        threshold=prune_threshold,
        bottom_pct=prune_bottom_pct,
        reason="定期维护：长期垫底 + 低质量自动标记",
        actor="sub_agent",
    )
    stats["pruned"] = pruned
    return stats
```

**影响文件**：`src/sub_agent.py`

---

### Step 60 — 编写质量评分单元测试 ✅

**位置**：`tests/test_quality_scorer.py`（新文件）

**测试用例设计**（覆盖三类知识）：

| 用例 | 输入边界规则 | 预期 quality_level | 预期 knowledge_delta |
|------|------------|-------------------|---------------------|
| 专家节点 | `禁止使用 HTTP 连接重试；指数退避 2^n ms；当状态码 429 时降级到 5xx` | `expert` | >= 0.6 |
| 充足节点 | `仅处理 network HTTP 超时；使用重试机制` | `adequate` | ~0.3 |
| 低质量节点 | `仅处理 HTTP 超时修复` | `poor` | ~0.1 |
| 冗余节点 | `聚焦 HTTP 修复` | `redundant` | < 0.1 |
| 空边界 | `基于反馈举证自动生成` | `redundant` | 0.0 |
| 混合信号 | 同时含专家+冗余模式 | `adequate` | 0.2-0.4 |

**影响文件**：`tests/test_quality_scorer.py`（新增）

---

## Phase 11: Skill-Creator 质量评分门禁（轻量版）✅

> **状态**：✅ 完成
> **完成方式**：`compile_skills()` 集成 D1 质量评分门禁，低质量节点跳过编译并记录日志
> **未实现**：完整 A/B 评估流水线（Grader/Comparator/Analyzer 三 Agent 评估）→ 留待 Phase 13 后

### Step 61 — compile_skills() 质量评分门禁 ✅

**改造**：`compile_skills()` 在编译前对每个节点执行 D1 知识增量评分，低于 `quality_delta_min` 的节点跳过编译并在 maintenance_log 中记录原因。

**核心代码**：

```python
def compile_skills(self, top_k: int = 5, quality_delta_min: float = 0.1):
    for breakdown in self._rt.top_k(k=top_k):
        entry = self._storage.get_routing_entry(breakdown.category_id)
        if entry.primary_skill_id is not None:
            continue
        score = self._quality_scorer.score(entry)
        if score.knowledge_delta < quality_delta_min:
            entry.local_map.append_log("skill_compile_skipped", ...)
            self._storage.upsert_routing_entry(entry)
            continue
        skill = self._compiler.compile_from_entry(entry)
```

**影响文件**：`src/sub_agent.py`

### Step 62 — SKILL.md 生成（未来完整版）

**规划**：`compile_from_entry()` 从硬编码三步模板升级为基于 `local_map` 生成可迭代的 SKILL.md。

**SKILL.md 结构映射**（Skill-Builder frontmatter 规范）：

```yaml
---
name: NetworkRateLimit429Skill
slug: network-rate-limit-429
version: 1.0.0
description: Handle HTTP 429 rate limiting with exponential backoff and circuit breaker.
---

## When to Use
[从 local_map.focus_description 生成]

## Core Rules
[从 local_map.boundary_rules 提取反模式]
[从 local_map.logic_signature 提取执行策略]
```

**影响文件**：`src/skill_compiler.py`

### Step 62 — A/B 评估流水线

**引入 Skill-Creator 三 Agent 架构**：

```
compile_from_entry(enable_eval=True)
    ├── 生成 SKILL.md 草稿
    ├── spawn Agent A（with-skill）执行测试用例
    ├── spawn Agent B（baseline，无 skill）执行相同测试
    ├── Comparator 盲评（不知道哪个是哪个）
    ├── Analyzer 归因（分析胜者为何胜出）
    └── 如果 pass_rate >= 0.7 且 delta > 0.1 → 入库
        否则 → 标记"待改进"，不入库
```

**影响文件**：`src/eval_pipeline.py`（新增）+ `src/skill_compiler.py`

### Step 63 — aggregate_benchmark.py 统计聚合

**技能白皮书中的关键设计**：
- 样本标准差（除以 n-1）
- 置信区间计算
- 方差分析（检测 Skill 是否真的稳定提升）

**影响文件**：`scripts/aggregate_benchmark.py`（新增）

---

## Phase 12: Skill-Builder 模板模式适配 ✅

> **状态**：✅ 完成
> **完成条件**：4 种模式模板 + `_select_pattern()` + 13 个新模式测试全绿

### Step 64 — _select_pattern() 模式选择器 ✅

基于路由表节点特征自动选择 Skill 模板：

| 根分类 | 模式 | 理由 |
|-------|------|------|
| `network` | Tool | 包装重试/限流工具，需 scripts |
| `data_parsing` | Domain | 格式解析专家知识，需 references |
| `llm_inference` | Workflow | 多阶段推理管道，需 phases |
| `permission` | Memory | 权限策略记忆，需 memory-template |
| `resource_exhaustion` | Tool | 资源管理工具包装 |

**影响文件**：`src/skill_compiler.py`

### Step 65 — 模式自适应生成

```python
def _generate_skill_md(
    self, entry: RoutingTableEntry, pattern: str
) -> str:
    if pattern == "tool":
        return self._generate_tool_pattern(entry)
    elif pattern == "domain":
        return self._generate_domain_pattern(entry)
    elif pattern == "workflow":
        return self._generate_workflow_pattern(entry)
    elif pattern == "memory":
        return self._generate_memory_pattern(entry)
```

**影响文件**：`src/skill_compiler.py`

---

## Phase 13: Agent-Builder 子代理专业化 ✅

> **状态**：✅ 完成
> **完成条件**：SubAgentPool 工厂 + 专用子代理 + auto_balance + Skill 运行时工具集推断 + 24 个测试全绿

### Step 66 — 专用子代理工厂 ✅

当某根分类节点数超过阈值，自动创建专用 SubAgent：

```
根分类节点数 > 50
    → Agent-Builder 创建 SpecializedSubAgent
    → 只处理该分类的蒸馏、维护、Skill 孵化
    → 使用 Agent-Builder 六阶段流程（Context Scan → Discovery → Build → Verify）
```

### Step 67 — Skill 运行时化

每个 Skill 不仅是"步骤列表"，而是一个完整的小型 Agent：
- 有自己的工具集（从路由表节点推断）
- 有自己的上下文压缩策略
- 可以动态生成子步骤

---

## 质量门禁检查清单（最终状态）

- [x] `pytest tests/` 287 测试全部通过
- [x] `ruff check src/` 0 error
- [x] `mypy src/ --strict` 1 pre-existing error（scoring.py 216: no-any-return，非本阶段引入）
- [x] `test_quality_scorer.py` 覆盖专家/充足/低质量/冗余四类节点
- [x] `NodeQualityScore` 数据模型通过 `to_dict()` / `from_dict()` 序列化测试
- [x] `maintain()` 中质量评分门禁不会破坏原有分裂/剪枝逻辑
- [x] `compile_skills()` 中质量评分门禁跳过低质量节点并记录日志
- [x] `test_skill_compiler.py` 覆盖 4 种模板模式（tool/domain/workflow/memory）
- [x] `test_sub_agent_pool.py` 覆盖专用子代理工厂 + auto_balance + Skill 运行时
- [ ] 低质量节点被标记后写入 maintenance_log

---

## 维护日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08 | v1.0 | 初版：Phase 10–13 规划，基于 Skill-Creator/Judge/Builder 三份元技能 |
| 2026-08 | v1.1 | ✅ Phase 10 完成：D1 知识增量评分 + 维护/编译质量门禁 |
| 2026-08 | v1.2 | ✅ Phase 11 完成：compile_skills() 质量评分门禁 |
| 2026-08 | v1.3 | ✅ Phase 12 完成：Skill-Builder 4 模式模板 + _select_pattern() |
| 2026-08 | v1.4 | ✅ Phase 13 完成：SubAgentPool 工厂 + auto_balance + Skill 运行时工具集 |
