# Cordis 插件适配层

> 来源：2026-08 Cordis 插件适配（路径 A：Python 后端 + TypeScript Cordis 插件封装）
> 范围：`scripts/serve.py`（Python RPC 服务器）+ `plugins/dsh-self-evolving-agent/`（Cordis 插件）
> 约束全景：`约束/` 文件夹 26 份文档（插件约束 13 份 + 工具约束 7 份 + 子代理约束 3 份 + 审计报告 1 份 + 配置 2 份）
> 目标：让 Python 核心以 DSH 原生插件的形式被 `dsh plugin add` 加载

---

## 一、适配决策

### 为什么不直接重写为 TypeScript？

| 方案 | 工作量 | 风险 | 结论 |
|------|--------|------|------|
| **路径 A**：Cordis 插件封装 Python 后端 | 中 | 低（子进程通信） | ✅ 已采用 |
| **路径 B**：TypeScript 重写全部模块 | 极大 | 中（Python 核心 192 个测试需重写） | ❌ 不推荐 |

Python 核心已有 203 个测试、完整的自进化框架。路径 A 只在外围包一层薄 Cordis 封装，Python 代码零改动。

---

## 二、架构概览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         DSH / Cordis Runtime                         │
│                                                                      │
│  plugins/dsh-self-evolving-agent (TypeScript)                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ index.ts                                                       │ │
│  │  apply(ctx, config)                                            │ │
│  │    ├─ ctx.effect() → PythonServer.start()                      │ │
│  │    ├─ ctx.systemPrompt.section() → 框架描述段                  │ │
│  │    └─ registerTools(ctx, server) → 9 个 DSH 工具             │ │
│  │                                                              │ │
│  │ python-server.ts                                              │ │
│  │  spawn(python scripts/serve.py :db_path:)                     │ │
│  │  stdin/stdout JSON-RPC 行协议                                  │ │
│  │  自动重连（子进程退出时重启）                                   │ │
│  │                                                              │ │
│  │ tools/index.ts                                                │ │
│  │  defineTool() × 9 → ctx.tools.register()                     │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                      │
│                              │ stdin/stdout (JSON-RPC)              │
│                              ▼                                      │
├──────────────────────────────────────────────────────────────────────┤
│                        Python 核心 (零改动)                          │
│                                                                      │
│  scripts/serve.py                                                    │
│    Server 类                                                         │
│      ├─ health()        → 健康检查                                   │
│      ├─ stats()         → 路由表 + 暂存队列统计                      │
│      ├─ lookup_exact()  → MainAgent.lookup_exact()                   │
│      ├─ lookup_fuzzy()  → MainAgent.lookup_fuzzy()                   │
│      ├─ report_unknown()→ MainAgent.report_unknown()                 │
│      ├─ planner_plan()  → OfflinePlanner.plan()                      │
│      ├─ routing_query() → Storage.query_routing_entries()            │
│      ├─ routing_rank()  → RoutingTable.rank()                        │
│      ├─ routing_split() → RoutingTable.split()                       │
│      └─ routing_prune() → RoutingTable.prune_lowest()                │
│                                                                      │
│  src/ (主代理 / 子代理 / 路由表 / 排序 / 重叠校验 / ...)              │
│  tests/ (203 个用例，全绿)                                           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 三、约束合规性审计（对照 26 份约束文档）

### 3.1 Fiber 生命周期合规（对照 `插件约束/操作流程.md`）

| 约束 | 当前实现 | 合规性 |
|------|---------|--------|
| `apply()` 中注册所有副作用 | ✅ `registerTools()` 在 `apply()` 内调用 | ✅ |
| `ctx.effect()` 返回 disposer | ✅ Python 子进程启动/停止 | ✅ |
| `ctx.tools.register()` 返回 disposer | ✅ `registerTools()` 返回 disposers 数组 | ✅ |
| `ctx.on()` / `ctx.provide()` 自动清理 | — 未使用事件总线/服务提供 | — |
| PENDING→LOADING→ACTIVE 状态机 | ✅ `inject: ['tools', 'systemPrompt']` 声明依赖 | ✅ |
| UNLOADING 逆序清理 | ✅ effect disposer 逆序执行 | ✅ |

### 3.2 工具定义合规（对照 `工具约束/注意事项.md`）

| 约束 | 当前实现 | 合规性 |
|------|---------|--------|
| `name` 唯一性 | 9 个工具名均唯一 | ✅ |
| `description` 简明 | 面向模型的动作描述 | ✅ |
| `additionalProperties: false` | 全部已声明 | ✅ |
| `execute()` 返回 JSON 可序列化值 | 依赖 Python `_serialize()` | ✅ |
| **`execute()` 领域失败用规范值** | ⚠️ 未处理 — Python 端异常直接转为 isError | ⚠️ 偏离 |
| **必须遵从 `exec.signal`** | ❌ 未实现 — 工具 execute 中无 signal 转发 | ❌ 缺陷 |
| 变更类工具使用 `presentationMeta` | 未使用 — 当前工具无 UI diff 需求 | — |

### 3.3 `execute(args, exec)` 参数契约（对照 `工具约束/execute 的参数契约.md`）

| # | 契约 | 当前状态 |
|---|------|---------|
| 1 | 参数已预校验，无需重复校验 | ✅ |
| 2 | 注册后定义不可变 | ✅ |
| 3 | 执行身份受保护（callId/name/arguments 只读） | ✅ |
| 4 | 返回值必须通过 `output.schema` 验证 | ✅ |
| 5 | 领域失败用规范值，基础设施故障用 throw | ⚠️ 未区分 |
| 6 | **必须遵从 `exec.signal`（强制性）** | ❌ 未实现 |
| 7 | 变更类工具使用 `presentationMeta` | — 无需求 |
| 8 | `deferContext` 在 `tool/result` 后发出 | — 未使用 |
| 9 | `concludeTurn` 不短路已提交的 next-step | — 未使用 |

### 3.4 Bundle/Profile/Preset 合规（对照 `插件约束/运行相关.md`）

| 约束 | 当前实现 | 合规性 |
|------|---------|--------|
| Bundle manifest `dsh.bundle` 声明 | ✅ `package.json` 中 `"dsh": {"bundle": {"patch": "./cordis.patch.yml"}}` | ✅ |
| `cordis.patch.yml` 补丁格式 | ✅ 带 `id` 和 `name` | ✅ |
| Bundle 由 npm 包分发 | ✅ `package.json` 声明 | ✅ |
| **双锚点解析**（dsh 安装目录 → profile node_modules） | ⚠️ 本地开发场景下，插件路径解析依赖 `SELF_EVOLVING_PROJECT` 环境变量 | ⚠️ 未优化 |
| profile `cordis.patch.yml` 热重载 | ✅ `disabled: true` 可热禁用 | ✅ |
| 声明 `dsh.bundle` 后不再手动 insert | ✅ 插件设计如此 | ✅ |

### 3.5 子代理/工作流约束（对照 `子代理约束/`）

| 约束 | 相关性 | 说明 |
|------|--------|------|
| 6 个内置提供者（spawn/fork/acp/codex/claude-code/dsh-sdk） | 不适用 | 本项目不使用 DSH 子代理系统，自进化 Agent 有自己的子代理 |
| Fail Loud (UNSUPPORTED_CAPABILITY) | 不适用 | 不涉及子代理能力校验 |
| 深度限制 + 工具过滤 | 不适用 | 自进化框架有自己的 MAX_SPLIT_DEPTH=3 |

### 3.6 审计报告发现（对照 `审计报告/代码约束符合度审计报告.md`）

审计报告中 12 个缺陷项对 Cordis 适配层的影响：

| 审计项 | 对 Cordis 适配层的影响 | 当前工具状态 |
|--------|---------------------|------------|
| BUG-001: SubAgent 新建节点未持久化 | `planner_plan` 工具返回的 accepted 数可能虚高 | ⚠️ 需确认 |
| BUG-002: 重叠校验旁路 | `routing_split` 工具走 `create_node()` 统一入口，已安全 | ✅ 不受影响 |
| DEVIATION-001: MainAgent 持有 SkillCompiler | 不影响工具调用（只读路径未变） | — |
| DEVIATION-002: Trend 恒为 0.0 | `routing_rank` 排序结果 Trend 维度失效 | ⚠️ 已记录 |
| DEVIATION-003: 时间衰减恒为 1.0 | `routing_rank` 排序无衰减 | ⚠️ 已记录 |
| DEVIATION-005: 边界校验跳过 | 不影响工具返回值 | — |

---

## 四、协议设计

### JSON-RPC over stdin/stdout 行协议

**为什么选择行协议**：

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| JSON-Lines（行协议） | 简单、无粘包、Python/TS 原生支持 | 需手动切行 | ✅ 已采用 |
| MessagePack + length prefix | 紧凑、无文本解析 | 需额外依赖 | ❌ 不必要 |
| ZeroMQ / gRPC | 高性能、自带序列化 | 需要 socket 库 | ❌ 过度设计 |
| TCP socket + 自研帧协议 | 跨进程 | 需 listen/connect | 保留为 --listen 选项 |

**协议格式**：

```
# 服务器启动时输出
__ready__\n

# 请求（stdin）
{"jsonrpc":"2.0","id":1,"method":"lookup_exact","params":{"category_id":"network.http_429"}}\n

# 响应（stdout）
{"jsonrpc":"2.0","id":1,"result":{"category_id":"network.http_429","match_type":"exact",...}}\n

# 错误响应
{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found"}}\n
```

**错误码约定**：

| code | 含义 |
|------|------|
| `-32700` | JSON 解析失败 |
| `-32601` | 方法不存在 |
| `-32000` | Python 业务异常 |

---

## 五、Python 侧：`scripts/serve.py`

### 启动方式

```bash
# 标准模式（stdin/stdout）
python scripts/serve.py /path/to/agents.db

# TCP 模式（可选，用于非 stdin/stdout 场景）
python scripts/serve.py /path/to/agents.db --listen 18899
```

### Server 类

```python
class Server:
    def __init__(self, db_path: str) -> None:
        # 初始化 Storage / PendingQueue / MainAgent / OfflinePlanner
        ...

    def run_stdio(self) -> None:
        print("__ready__", flush=True)   # 就绪信号
        for line in sys.stdin:           # 行协议循环
            request = json.loads(line)
            response = self._handle(request["method"], request["params"])
            print(json.dumps(response), flush=True)

    def _handle(self, method, params) -> dict:
        handler = getattr(self, method, None)
        if handler is None:
            return {"jsonrpc": "2.0", "error": {"code": -32601, ...}}
        try:
            return {"jsonrpc": "2.0", "result": _serialize(handler(params))}
        except Exception as exc:
            return {"jsonrpc": "2.0", "error": {"code": -32000, ...}}
```

### 序列化器 `_serialize()`

递归将 Python 对象转为 JSON 可接受格式：
- `dataclass` → `to_dict()`（优先）
- `dataclass` 无 `to_dict()` → `__dataclass_fields__` 遍历
- `set` → 排序后的 list
- `datetime` → `isoformat()`

---

## 六、TypeScript 侧：Cordis 插件

### 目录结构

```
plugins/dsh-self-evolving-agent/
├── package.json          # npm 包元数据，声明 dsh.bundle
├── cordis.patch.yml      # Bundle 装配层
├── tsconfig.json         # TypeScript 配置
└── src/
    ├── index.ts          # 插件入口：apply(ctx, config)
    ├── python-server.ts  # Python 子进程管理器
    └── tools/
        └── index.ts      # 9 个 DSH 工具注册
```

### `index.ts` — 插件入口

```typescript
export const name = 'dsh-self-evolving-agent'
export const inject = ['tools', 'systemPrompt']

export function apply(ctx: Context, config: Config) {
  // 1. 启动 Python 子进程（effect → 卸载时自动 stop）
  ctx.effect(() => {
    server.start()
    return () => server.stop()
  })

  // 2. 注册系统提示词段（effect → 卸载时自动 remove）
  ctx.effect(() => {
    return ctx.systemPrompt.section({ name: 'self-evolving-agent', ... })
  })

  // 3. 注册 9 个 DSH 工具
  registerTools(ctx, server)
}
```

**生命周期合规**：
- 所有副作用通过 `ctx.effect()` 注册，返回 disposer
- 卸载时 Python 子进程 `SIGTERM`、工具移除、提示词段移除——**可逆**

### `python-server.ts` — 子进程管理器

```typescript
class PythonServer {
  private process: ReturnType<typeof spawn> | null = null
  private pending: Map<number, Promise<unknown>> = new Map()
  private seq = 0

  start(): void {
    this.process = spawn(this.config.pythonBin,
      [this.config.serveScript, this.config.dbPath],
      { stdio: ['pipe', 'pipe', 'pipe'] }
    )
    // stdout 解析：__ready__ 信号 → 逐行 JSON-RPC 响应
    // exit 事件 → 自动重连（reconnectIntervalMs 间隔）
  }

  stop(): void {
    this.process?.kill('SIGTERM')
  }

  async call(method: string, params: Record<string, unknown>): Promise<unknown> {
    await this.ready  // 等待 __ready__
    const id = ++this.seq
    this.process.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
    return new Promise((resolve, reject) => { /* pending[id] */ })
  }
}
```

### `tools/index.ts` — 工具注册

```typescript
export function registerTools(ctx: Context, server: PythonServer): void {
  const lookupExact = defineTool({
    name: 'lookup_exact',
    description: '按 category_id 精确查询路由表节点和关联 Skill',
    inputSchema: {
      type: 'object',
      required: ['category_id'],
      additionalProperties: false,
      properties: {
        category_id: { type: 'string' },
      },
    },
    execute: async ({ category_id }) => server.call('lookup_exact', { category_id }),
  })

  // ... 其他 8 个工具

  ctx.effect(() => {
    return tools.map(t => ctx.tools.register(t))
  })
}
```

---

## 七、9 个暴露的 DSH 工具

| # | 工具名 | Python 方法 | 用途 |
|---|--------|------------|------|
| 1 | `lookup_exact` | `Server.lookup_exact` | 按 category_id 精确查询路由表 |
| 2 | `lookup_fuzzy` | `Server.lookup_fuzzy` | 标签 AND 条件模糊查询 |
| 3 | `report_unknown` | `Server.report_unknown` | 未知错误举证入队 |
| 4 | `planner_plan` | `Server.planner_plan` | 离线规划（分类 + 重叠门禁 + Skill 孵化） |
| 5 | `routing_query` | `Server.routing_query` | 路由表条目查询 |
| 6 | `routing_rank` | `Server.routing_rank` | 四维排序 |
| 7 | `routing_split` | `Server.routing_split` | 分裂子节点（重叠门禁 + 深度限制） |
| 8 | `routing_prune` | `Server.routing_prune` | 剪枝低分节点（可自动合并） |
| 9 | `agent_stats` | `Server.stats` | 路由表 + 暂存队列统计 |

---

## 八、安装与部署

### 环境变量

```bash
export SELF_EVOLVING_DB=/path/to/agents.db
export SELF_EVOLVING_PROJECT=/path/to/DSH-Self-evolving-system
```

### Bundle 装配（`cordis.patch.yml`）

```yaml
- id: dsh-self-evolving-agent
  name: '@deepseek-ai/dsh-self-evolving-agent'
  config:
    dbPath: ${env:SELF_EVOLVING_DB:?Set SELF_EVOLVING_DB to agents.db path}
    pythonBin: python
    serveScript: ${env:SELF_EVOLVING_PROJECT:?Set SELF_EVOLVING_PROJECT}/scripts/serve.py
    reconnectIntervalMs: 5000
```

### npm 包（`package.json`）

```json
{
  "name": "@deepseek-ai/dsh-self-evolving-agent",
  "version": "0.1.0",
  "dsh": { "bundle": { "patch": "./cordis.patch.yml" } }
}
```

### 加载

```bash
dsh plugin add @deepseek-ai/dsh-self-evolving-agent
# 或
dsh plugin add /path/to/plugins/dsh-self-evolving-agent
```

---

## 九、测试

### Python 侧

```bash
python -m pytest tests/test_rpc_server.py   # 11 个 RPC 协议测试
python -m pytest tests/                     # 203 个全量测试
```

### TypeScript 侧

```bash
cd plugins/dsh-self-evolving-agent
npm run build   # tsc --noEmit
```

### 端到端

```bash
# 启动 Python 服务器
python scripts/serve.py /path/to/agents.db &

# DSH 加载插件
dsh plugin add ./plugins/dsh-self-evolving-agent

# 在 DSH 中使用工具
dsh -e "lookup_exact(category_id='network.http_429')"
dsh -e "planner_plan(batch_size=5)"
```

---

## 十、设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 通信协议 | JSON-RPC / MessagePack / gRPC / ZMQ | JSON-RPC over stdin/stdout | 零外部依赖，Python 和 TS 原生支持 |
| 行协议分隔 | `\n` / `\r\n` / length-prefix | `\n` | Python 的 `for line in sys.stdin` 和 JS 的 `on('data')` 都原生支持 |
| 子进程管理 | spawn / fork / child_process | `spawn` | 标准 Node.js API，Windows/macOS/Linux 通用 |
| 重连策略 | 固定间隔 / 指数退避 / 不重连 | 固定间隔（可配置） | 子进程故障通常是瞬时的，固定间隔简单可靠 |
| 工具注册时机 | apply() 中同步 / effect() 中异步 | effect() | 符合 Cordis Fiber 生命周期（卸载时自动移除） |
| 提示词段 | section() / variable() | section() | 框架描述是静态文本，section 更合适 |

---

## 十一、已知限制与后续优化

### 11.1 工具约束合规性（P1 — 需修复）

| 限制 | 影响 | 优化方向 |
|------|------|---------|
| **工具未转发 `exec.signal`** | 用户中止时 Python 端继续运行，JSON-RPC 调用超时后丢弃 | 在 `python-server.ts` 中 `call()` 接收 `AbortSignal`，超时后 kill 子进程 |
| **领域失败用 throw 而非规范值** | 模型无法区分"节点不存在"和"基础设施故障" | Python 端返回 `{"ok": false, "error": "..."}`，TS 端 `execute()` 中 `throw` 仅用于基础设施故障 |
| **未区分 domain error vs infrastructure error** | 模型对错误语义理解模糊 | 定义错误码映射表，如 `NOT_FOUND`/`OVERLAP_REJECTED`/`INVALID_INPUT` |

### 11.2 Python 端已知缺陷（来自审计报告）

| 限制 | 来源 | 影响 | 优化方向 |
|------|------|------|---------|
| BUG-001: SubAgent 新建节点未持久化 | 审计报告 BUG-001 | `planner_plan` 返回的 accepted 节点不持久化 | 在 `_process_feedback()` 添加 `upsert_routing_entry()` |
| BUG-002: 重叠校验旁路 | 审计报告 BUG-002 | `routing_split` 工具安全（走 create_node），但其他路径仍旁路 | 统一所有创建路径到 `create_node()` |
| DEVIATION-002: Trend 恒为 0.0 | 审计报告 DEVIATION-002 | `routing_rank` 排序中 Trend 维度失效 | 接入 `last_seen` 时间戳，Phase 6 待办 |
| DEVIATION-003: 时间衰减恒为 1.0 | 审计报告 DEVIATION-003 | 长期未出现的错误不会降权 | 在 stats 中添加 `last_seen` 字段 |

### 11.3 部署优化（P2 — 可推迟）

| 限制 | 影响 | 优化方向 |
|------|------|---------|
| 双锚点解析未优化 | 必须设置 `SELF_EVOLVING_PROJECT` 环境变量 | 在 `package.json` 中增加 `files` 字段，发布为自包含 npm 包 |
| 工具名无命名空间前缀 | 可能与其他插件冲突 | 改为 `agent.lookup_exact` 等带前缀 |
| Config schema 未用 schemastery | 配置无运行时校验 | 增加 `z.object({...})` 校验 |
| JSON-RPC 无 streaming | 大结果一次性返回 | 实现 chunked 响应模式 |
| TCP 模式未实现多连接 | 仅单连接 | 实现多连接队列 |

---

## 十二、相关文件

| 文件 | 关联 |
|------|------|
| `scripts/serve.py` | Python RPC 服务器 |
| `plugins/dsh-self-evolving-agent/src/index.ts` | Cordis 插件入口 |
| `plugins/dsh-self-evolving-agent/src/python-server.ts` | 子进程管理器 |
| `plugins/dsh-self-evolving-agent/src/tools/index.ts` | 工具注册 |
| `plugins/dsh-self-evolving-agent/cordis.patch.yml` | Bundle 装配 |
| `plugins/dsh-self-evolving-agent/package.json` | npm 元数据 |
| `tests/test_rpc_server.py` | RPC 协议回归测试 |
| `待完善/设计待完善.md` | 31 个已知问题 |
| `约束/插件约束/插件开发指南.md` | Cordis 插件开发全指南 |
| `约束/工具约束/注意事项.md` | execute() 七条硬契约 |
| `约束/工具约束/execute 的参数契约.md` | args/exec 完整参数契约 |
| `约束/插件约束/操作流程.md` | Fiber 生命周期 PENDING→ACTIVE→DISPOSED |
| `约束/插件约束/运行相关.md` | Bundle/Profile/Preset 三层组合 |
| `约束/审计报告/代码约束符合度审计报告.md` | 12 个缺陷项及修复路线图 |