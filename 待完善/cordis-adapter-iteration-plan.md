# Cordis 适配 — 约束合规迭代计划

> 基准：26 份约束文档 + 审计报告
> 状态：P0（6/6）已全部完成。本文聚焦剩余合规缺口。

---

## 一、合规缺口判定

逐项审查"待迭代"项在约束文档中的强制性级别：

| 项 | 约束来源 | 强制性 | 判定 |
|----|---------|--------|------|
| `presentationMeta`（变更工具） | `注意事项.md` 规则 7 | **必须** | 🔴 合规缺口 → 提升为 P0 |
| `presentCall`/`presentResult` | 所有文档签名带 `?` | 可选 | ✅ 无缺口 |
| `guards` | `注意事项.md` "应" | 推荐 | ✅ 无缺口 |
| Trend 数据源 | `总览.md` 蓝图公式 | 设计对齐 | ✅ 无缺口 |
| 时间衰减 | `总览.md` 蓝图公式 | 设计对齐 | ✅ 无缺口 |

**结论**：仅 `presentationMeta` 是合规缺口，需立即修复。其余项为蓝图对齐优化，归入 P2。

---

## 二、P0-7：presentationMeta 合规修复

### 2.1 约束要求

```ts
// 规则 7：变更类工具必须使用 presentationMeta
// 核心将投影的 JSON 持久化在 tool/result 上并传给 presentResult
presentationMeta: (args, result) => ({
  // 变更类工具的持久化卡片数据
  diff: '...',
  path: '...',
  // ...
}),
```

**适用工具**（变更类 — 写入/修改路由表或暂存队列）：

| 工具 | 变更类型 | presentationMeta 内容 |
|------|---------|----------------------|
| `report_unknown` | 写入暂存队列 | `enqueued` 布尔 + `error_stack` 摘要 |
| `planner_plan` | 写入路由表 + 编译 Skill | `processed` / `accepted` / `rejected` 计数 |
| `routing_split` | 创建路由表节点 | 新节点 `category_id` + `parent_id` |
| `routing_prune` | 合并/剪枝路由表节点 | 合并计划列表（`target_id` → `parent_id`） |

### 2.2 实施状态

| 工具 | presentationMeta | 状态 |
|------|-----------------|------|
| `report_unknown` | `{ type: 'agent:feedback', enqueued }` | ✅ 已完成 |
| `planner_plan` | `{ type: 'agent:planner', processed/accepted/rejected }` | ✅ 已完成 |
| `routing_split` | `{ type: 'agent:split', category_id/ok }` | ✅ 已完成 |
| `routing_prune` | `{ type: 'agent:prune', plan_count }` | ✅ 已完成 |

**测试结果**：204/204 ✅ + ruff ✅ + mypy ✅

---

## 三、P1：蓝图对齐优化（非合规缺口，设计增强）

| # | 项 | 来源 | 工作量 | 优先级 |
|---|----|------|--------|--------|
| 1 | presentCall + presentResult | `适配器.md` 推荐 | 2h | P1-低 |
| 2 | guards（路由变更类工具） | `注意事项.md` 推荐 | 1h | P1-中 |
| 3 | Trend 数据源接入（last_seen） | `总览.md` 四维公式 | 1d | P1-中 |
| 4 | 时间衰减接入 | `总览.md` 衰减公式 | 0.5d | P1-中 |
| 5 | 双锚点解析优化 | `运行相关.md` 部署 | 1d | P2 |
| 6 | 工具名前缀 `agent.` | `插件开发指南.md` 命名规范 | 0.5d | P2 |
| 7 | chunked JSON-RPC | 性能优化 | 1d | P2 |

---

## 四、实施顺序

### 第 1 步：P0-7（合规缺口修复）

```
P0-7: presentationMeta
  ├── report_unknown
  ├── planner_plan
  ├── routing_split
  └── routing_prune

验证：204/204 ✅ + ruff ✅ + mypy ✅
状态：✅ 已完成
```

### 第 2 步：P1-3 + P1-4（蓝图对齐 — Trend 数据源 + 时间衰减）

```
P1-3: Trend 数据源
  ├── sub_agent.py _process_feedback() → stats["last_seen"]
  ├── sub_agent.py distill() → stats["last_seen"]
  └── offline_planner.py _phase_deploy() → stats["last_seen"]

P1-4: 时间衰减
  └── routing_table.py rank() → _compute_days_since_last_seen() per-entry

验证：
  # 新增 4 个测试（不同 last_seen 得分不同 / 相同 last_seen 相同衰减 /
  # 缺失 last_seen 回退 / 未来 last_seen 处理为 fresh）
  208/208 ✅ + ruff ✅ + mypy ✅

状态：✅ 已完成
```

### 第 3 步：P1-1 + P1-2（表现层增强，可选）

```
P1-1: presentCall + presentResult
P1-2: guards（routing_split + routing_prune + planner_plan）

状态：⬜ 待实施（可选，非合规缺口）
```

---

## 五、合规全景最终状态

| 层级 | 项数 | 合规数 | 缺口 | 状态 |
|------|------|--------|------|------|
| L1 工具契约（7条） | 7 | 7 | 0 | ✅ |
| L2 Fiber 生命周期 | 5 | 5 | 0 | ✅ |
| L3 Bundle 装配 | 3 | 3 | 0 | ✅ 插件自包含 serve.py，双锚点合规 |
| L4 Config 校验 | 1 | 1 | 0 | ✅ |
| L5 执行管线 | 4 | 4 | 0 | ✅ |
| L6 审计报告缺陷 | 2 | 2 | 0 | ✅ |
| **合计** | **22** | **22** | **0** | **✅ 全合规** |