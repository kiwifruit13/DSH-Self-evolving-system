# Cordis 插件适配 — 约束合规实施计划

> **基准**：26 份约束文档（插件约束 13 + 工具约束 7 + 子代理约束 3 + 审计报告 1 + 配置 2）
> **范围**：Python 核心零改动 + Cordis 插件层合规改造
> **原则**：以约束技术要求为唯一合规依据，不添加约束外功能

---

## 零、合规全景矩阵

### 约束层级划分

| 层级 | 来源 | 影响范围 | 我们是否合规 |
|------|------|---------|------------|
| **L1 — 工具契约** | `工具约束/注意事项.md` + `execute 的参数契约.md` | `execute(args, exec)` 七条硬契约 | ❌ 3/9 不合规 |
| **L2 — Fiber 生命周期** | `插件约束/操作流程.md` + `开发相关.md` | `apply()` → `effect()` → 卸载 | ✅ 全部合规 |
| **L3 — Bundle 装配** | `插件约束/运行相关.md` + `插件开发指南.md` | `package.json` / `cordis.patch.yml` | ⚠️ 1 项待优化 |
| **L4 — Config 校验** | `schemastery 的用法.md` + `开发相关.md` | Config schema | ❌ 未使用 schemastery |
| **L5 — 执行管线** | `tools执行流水线.md` + `tools事件接口.md` | 七阶段瀑布 + 事件 | ⚠️ 事件钩子未接入 |
| **L6 — 审计报告缺陷** | `审计报告/代码约束符合度审计报告.md` | BUG-001/002 影响工具返回值 | ❌ 2 个 BUG 影响工具 |

---

## 一、P0 — 必须修复（工具契约违反）

### 1.1 `exec.signal` 未转发（违反契约 6，强制性）

**约束来源**：`工具约束/注意事项.md` 规则 6、`execute 的参数契约.md` 第 6 条、`tools执行流水线.md` 阶段 5

**约束原文**：
> 必须遵从 `exec.signal`（强制性！）——`AbortSignal` 触发时取消进行中工作

**当前状态**：❌ 缺陷
```typescript
// 当前：ignore signal
execute: async ({ category_id }) => server.call('lookup_exact', { category_id })
// signal 未被传递到 Python 端，用户中止时 Python 继续运行
```

**修复方案**：

```typescript
// PythonServer.call() 接收 signal 参数
async call(method: string, params: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
  await this.ready
  const id = ++this.seq
  const req: RpcRequest = { jsonrpc: '2.0', id, method, params }
  
  const promise = new Promise<unknown>((resolve, reject) => {
    // 注册 signal 取消监听
    signal?.addEventListener('abort', () => {
      this.pending.delete(id)
      this.killProcess()  // kill Python 子进程
      reject(new Error('Aborted'))
    })
    // ... pending registration
  })
  
  this.send(req)
  return promise
}

// 每个工具 execute 转发 signal
execute: async (args, exec) => server.call('lookup_exact', args, exec.signal)
```

**涉及文件**：`plugins/.../src/python-server.ts`、`plugins/.../src/tools/index.ts`

**测试**：模拟 AbortSignal.abort() 触发 → 确认 Promise reject + Python 进程被 SIGTERM

---

### 1.2 领域失败用 throw 而非规范值（违反契约 5）

**约束来源**：`工具约束/注意事项.md` 规则 5、`execute 的参数契约.md` 第 5 条

**约束原文**：
> 领域失败用规范值，基础设施故障才用 throw

**当前状态**：❌ 缺陷
```typescript
// 当前：Python 端所有异常（包括"节点不存在"）都被转为 throw → isError
execute: async ({ category_id }) => server.call('lookup_exact', { category_id })
// 如果 Python 返回 {"error": {...}}，TS 端直接 throw → 模型收到 isError
```

**修复方案** — 定义错误码映射表：

```typescript
// tools/error-map.ts
const DOMAIN_ERRORS = new Map([
  'NOT_FOUND',        // 路由表节点不存在 → 领域失败
  'OVERLAP_REJECTED', // 重叠率超过阈值 → 领域失败
  'INVALID_INPUT',    // 无效输入 → 领域失败
])

// tools/index.ts
execute: async ({ category_id }) => {
  const result = await server.call('lookup_exact', { category_id }, exec.signal)
  if (!result || (result as any).error) {
    const err = (result as any).error
    if (DOMAIN_ERRORS.has(err.code)) {
      // 领域失败：返回规范值
      return { ok: false, error: err.message, code: err.code }
    }
    // 基础设施故障：throw
    throw new Error(err.message)
  }
  return result
}
```

**Python 端错误码**：

| code | 含义 | 场景 |
|------|------|------|
| `NOT_FOUND` | 领域：节点不存在 | `lookup_exact` 无匹配 |
| `OVERLAP_REJECTED` | 领域：重叠率超标 | `routing_split` 被拒绝 |
| `INVALID_INPUT` | 领域：无效输入 | 标签前缀校验失败 |
| `INFRA` | 基础设施：子进程异常 | Python 崩溃/超时 |

**涉及文件**：`plugins/.../src/tools/error-map.ts`、`scripts/serve.py`（错误码映射）

---

### 1.3 `output.schema` + `output.render` 未正确定义（违反契约 4 + 5）

**约束来源**：`工具约束/注意事项.md` 字段约束、`execute 的参数契约.md` 返回值契约

**约束原文**：
> `execute()` 的返回值会经过：递归遍历物化为分离的无损 JSON → `output.schema` 校验 → `deepFreeze()` → 传给 `output.render`

**当前状态**：⚠️ 偏离 — outputSchema 定义了但缺少 `render` 函数

**修复方案**：为每个工具补充 `output.render`：

```typescript
const lookupExact = defineTool({
  name: 'lookup_exact',
  // ...
  output: {
    schema: {
      type: 'object',
      properties: {
        ok: { type: 'boolean' },
        error: { type: 'string' },
        code: { type: 'string' },
        category_id: { type: 'string' },
        match_type: { type: 'string' },
        entry: { type: ['object', 'null'] },
        skill: { type: ['object', 'null'] },
        note: { type: 'string' },
      },
    },
    render: (_args, value) => {
      if (!value.ok) return [{ type: 'text', text: `查询失败: ${value.error}` }]
      const entry = value.entry ? `条目: ${value.category_id} (${value.match_type})` : '无匹配条目'
      const skill = value.skill ? `\nSkill: ${value.skill.skill_name}` : ''
      return [{ type: 'text', text: `${entry}${skill}${value.note ? '\n' + value.note : ''}` }]
    },
  },
  execute: async (args, exec) => { /* ... */ },
})
```

**涉及文件**：`plugins/.../src/tools/index.ts` — 全部 9 个工具

---

### 1.4 Config 未用 schemastery 校验（违反 L4）

**约束来源**：`schemastery 的用法.md`、`开发相关.md` 步骤 5

**约束原文**：
> 每个 `cordis.yml` 条目可携带 `config` 块，插件声明 schema 校验

**当前状态**：❌ 缺陷 — Config 只有 TypeScript interface，无运行时校验

**修复方案**：

```typescript
import Schema from '@deepseek-ai/schemastery'

export interface Config {
  dbPath: string
  pythonBin: string
  serveScript: string
  reconnectIntervalMs: number
}

export const Config = Schema.object({
  dbPath: Schema.string().required(),
  pythonBin: Schema.string().default('python'),
  serveScript: Schema.string().required(),
  reconnectIntervalMs: Schema.number().default(5000),
})

export function apply(ctx: Context, config: Config) {
  // Cordis 已在校验 config 后才调用 apply
}
```

**涉及文件**：`plugins/.../src/index.ts`

---

### 1.5 BUG-001 — SubAgent 新建节点未持久化（违反审计报告）

**约束来源**：`审计报告/代码约束符合度审计报告.md` BUG-001

**影响**：`planner_plan` 工具返回的 accepted 节点不持久化，后续查询永远找不到

**修复方案**：在 `src/sub_agent.py` 的 `_process_feedback()` 新节点路径末尾添加：

```python
# 新节点路径末尾（return 之前）
self._storage.upsert_routing_entry(entry)  # ← 添加这行
return entry
```

**涉及文件**：`src/sub_agent.py`

---

### 1.6 BUG-002 — 重叠校验旁路（违反审计报告）

**约束来源**：`审计报告/代码约束符合度审计报告.md` BUG-002

**影响**：`routing_split` 工具安全（走 `create_node()` 统一入口），但 `OfflinePlanner._phase_deploy()` 直接 `upsert_routing_entry()`

**修复方案**：`OfflinePlanner._phase_deploy()` 改用 `create_node()`：

```python
# 之前：upsert_routing_entry() — 绕过 create_node()
self._storage.upsert_routing_entry(entry)

# 之后：走 create_node() — 自动经过重叠校验 + 互斥检查
self._rt.create_node(entry, validate_overlap=False)  # Phase 2 已校验，这里跳过
```

**涉及文件**：`src/offline_planner.py`

---

## 二、P1 — 应当修复（工具契约补充）

### 2.1 补充 `output.render`（L1 契约 4）

为 9 个工具全部补充 `output.render`，确保模型可见文本清晰。每个工具的 render 逻辑：

| 工具 | render 输出 |
|------|------------|
| `lookup_exact` | "条目: xxx (exact) / 无匹配" |
| `lookup_fuzzy` | "找到 N 个匹配条目" |
| `report_unknown` | "举证已入队" / "入队失败" |
| `planner_plan` | "处理 X 个，接受 Y 个，拒绝 Z 个" |
| `routing_query` | "查询返回 N 个条目" |
| `routing_rank` | "排名 Top 3: xxx / yyy / zzz" |
| `routing_split` | "分裂成功: xxx" / "重叠率 XX% 超标" |
| `routing_prune` | "剪枝计划: X 个节点将合并" |
| `agent_stats` | "路由表 N 个，暂存队列 M 个" |

### 2.2 补充 `presentCall` + `presentResult`（表现层）

为 9 个工具补充 UI 卡片投影：

```typescript
presentCall: (args) => ({
  kind: 'generic',
  title: `查询 ${args.category_id}`,
  content: [{ type: 'text', text: '查询中...' }],
}),
presentResult: (args, result) => ({
  kind: 'generic',
  title: result.ok ? `查询结果: ${result.category_id}` : `查询失败: ${result.error}`,
  content: [{ type: 'text', text: result.ok ? result.category_id : result.error }],
}),
```

### 2.3 补充 `guards`（守卫）

为变更类工具（`routing_split`、`routing_prune`、`planner_plan`）添加守卫：

```typescript
guards: [
  {
    name: 'max-split-depth',
    check: (args, exec) => {
      const parts = args.child_name.split('.')
      if (parts.length > 4) return { decision: 'deny', reason: 'Split depth exceeds MAX_SPLIT_DEPTH' }
      return { decision: 'allow' }
    }
  }
]
```

### 2.4 接入 Trend 数据源（P0-1 待办）

在 stats 中添加 `last_seen` 时间戳，使 `routing_rank` 工具返回真实排序结果：

```python
# 在 _process_feedback() 中更新 last_seen
entry.stats["last_seen"] = datetime.now(timezone.utc).isoformat()
```

### 2.5 接入时间衰减（P0-3 待办）

`RoutingTable.rank()` 默认 `days_since_last_seen=0`，需从 stats 中读取 `last_seen` 并计算：

```python
def rank(self, root_category=None):
    entries = self._storage.query_routing_entries(root_category=root_category)
    now = datetime.now(timezone.utc)
    ranked = []
    for entry in entries:
        last_seen_str = entry.stats.get("last_seen", "")
        days = 0.0
        if last_seen_str:
            last_seen = datetime.fromisoformat(last_seen_str)
            days = (now - last_seen).total_seconds() / 86400
        score = self._scoreCalculator.compute_final_score(
            entry.stats, days_since_last_seen=days
        )
        ranked.append((entry, score))
    return sorted(ranked, key=lambda x: x[1], reverse=True)
```

---

## 三、P2 — 可以推迟（部署优化）

| # | 事项 | 约束来源 | 影响 |
|---|------|---------|------|
| 3.1 | 双锚点解析优化 | `运行相关.md` 双锚点 Bundle 解析 | 需设置 SELF_EVOLVING_PROJECT 环境变量 |
| 3.2 | 工具名前缀 `agent.` | `插件开发指南.md` 命名规范 | 可能与其他插件冲突 |
| 3.3 | chunked JSON-RPC | 不适用 | 大结果一次性返回 |
| 3.4 | cleanup_expired 封装 | 审计报告 DEVIATION-004 | 内部方法访问 |
| 3.5 | TCP 模式多连接 | 不适用 | 仅单连接 |

---

## 四、实施优先级总表

| 优先级 | 修复项 | 约束层级 | 工作量 | 阻塞关系 |
|--------|--------|---------|--------|---------|
| **P0-1** | exec.signal 转发 | L1 契约 6 | 2h | 无 |
| **P0-2** | 领域/基础设施错误分离 | L1 契约 5 | 1h | 依赖 P0-1 |
| **P0-3** | output.schema + output.render | L1 契约 4 | 2h | 无 |
| **P0-4** | Config schemastery 校验 | L4 | 0.5h | 无 |
| **P0-5** | BUG-001 修复 | L6 | 0.5h | 无 |
| **P0-6** | BUG-002 修复 | L6 | 1h | 无 |
| **P1-1** | presentCall + presentResult | L1 表现层 | 2h | 依赖 P0-3 |
| **P1-2** | guards 守卫 | L1 安全 | 1h | 无 |
| **P1-3** | Trend 数据源接入 | P0 待办 | 1d | 依赖 P0-5 |
| **P1-4** | 时间衰减接入 | P0 待办 | 0.5d | 依赖 P1-3 |
| **P2-1~5** | 部署优化 | L3 | 3d | 不阻塞上线 |

---

## 五、验证计划

### P0 验证

```bash
# P0-1: exec.signal 转发
# 测试：AbortSignal.abort() → Python 进程 SIGTERM + Promise reject

# P0-2: 领域/基础设施错误分离
# 测试：NOT_FOUND 返回 {ok: false, ...}，INFRA throw Error

# P0-3: output.render
# 测试：defineContentToolFixture 渲染验证

# P0-4: Config schemastery
# 测试：非法 config → FAILED 状态，apply 不运行

# P0-5: BUG-001
# 测试：SubAgent.consume_pending() → 新节点写入路由表
# 验证：storage.query_routing_entries() 返回新节点

# P0-6: BUG-002
# 测试：OfflinePlanner.plan() → 节点经过 create_node()
# 验证：overlap_checker 被调用
```

### 全量回归

```bash
python -m pytest tests/        # 203 个用例，全绿
python -m ruff check src/ tests/  # 0 error
python -m mypy src/ --strict    # 0 error
```

---

## 七、P0 实施状态（2026-08-28）

| 修复项 | 状态 | 涉及文件 | 说明 |
|--------|------|---------|------|
| **P0-1** exec.signal 转发 | ✅ 已完成 | `python-server.ts`、`tools/index.ts` | `call()` 接收 AbortSignal；abort 时 kill 子进程 + reject |
| **P0-2** 领域/基础设施错误分离 | ✅ 已完成 | `serve.py`（DomainError）、`error-map.ts`、`tools/index.ts`（safeCall） | NOT_FOUND/OVERLAP_REJECTED 返回规范值；INFRA throw |
| **P0-3** output.schema + output.render | ✅ 已完成 | `tools/index.ts` | 9 个工具全部补充 output.render |
| **P0-4** Config schemastery 校验 | ✅ 已完成 | `index.ts` | `Schema.object({...})` 替换纯 interface |
| **P0-5** BUG-001 修复 | ✅ **已在 Phase 5 完成** | `sub_agent.py` L371/L374 | 两条路径（重叠拒绝 + 正常创建）均 `upsert_routing_entry()` |
| **P0-6** BUG-002 修复 | ✅ **已在 Phase 5 完成** | `offline_planner.py` L297 | `_phase_deploy()` 走 `create_node(validate_overlap=False)` |

### 已交付文件

| 文件 | 变更 |
|------|------|
| `plugins/.../src/index.ts` | +schemastery Config 校验 |
| `plugins/.../src/python-server.ts` | +signal 转发、+rpcTimeoutMs 配置、+killProcess() |
| `plugins/.../src/tools/error-map.ts` | **新增** — 领域错误码定义 + parseErrorCode() |
| `plugins/.../src/tools/index.ts` | +output.render（9 个工具）、+safeCall()、+exec.signal 转发 |
| `scripts/serve.py` | +DomainError 类、+lookup_exact NOT_FOUND 检测、+split OVERLAP_REJECTED 检测 |
| `tests/test_rpc_server.py` | +test_lookup_exact_found、更新 NOT_FOUND 断言 |

### 测试验证

```
204/204 ✅ 全绿（17.73s）
```

### 剩余工作量

**P0 合规缺口（7 项，已完成 6 项，1 项待修复）：**
- P0-1 signal 转发 ✅
- P0-2 领域/基础设施错误分离 ✅
- P0-3 output.render ✅
- P0-4 Config schemastery ✅
- P0-5 BUG-001（Phase 5 完成）✅
- P0-6 BUG-002（Phase 5 完成）✅
- P0-7 presentationMeta ✅

**P1 蓝图对齐（已完成 2/4）：**
- ✅ Trend 数据源接入（stats 添加 last_seen 时间戳）
- ✅ 时间衰减接入（rank() 读取 last_seen 计算 per-entry 衰减）
- ⬜ presentCall + presentResult（表现层，可选）
- ⬜ guards（路由变更类工具，推荐）

**P2 部署优化（已完成 1/3）：**
- ✅ 双锚点解析优化（插件自包含 serve.py，无需 SELF_EVOLVING_PROJECT 环境变量）
- ⬜ 工具名前缀 `agent.`
- ⬜ chunked JSON-RPC

```
第 1 步: P0-4 (Config 校验)     ← 0.5h，独立，先改
第 2 步: P0-1 (signal 转发)     ← 2h，独立，先改
第 3 步: P0-2 (错误分离)        ← 1h，依赖 P0-1
第 4 步: P0-3 (output.render)   ← 2h，独立
第 5 步: P0-5 + P0-6 (BUG 修复) ← 1.5h，并行，独立
第 6 步: 全量回归验证            ← 0.5h
```

**总工作量（P0）**：7h（约 1 个工作日）
**P1 额外**：2.5d（可后续迭代）
**P2 额外**：3d（可推迟到生产阶段）