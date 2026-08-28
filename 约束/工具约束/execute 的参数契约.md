`execute(args, exec)` 是工具的核心入口，其两个参数分别由 `defineTool` 的 `parameters` 推导类型和管线运行时构造。以下是完整的参数契约。

---

## 一、`args` — 冻结的调用参数

### 类型来源

`args` 的类型由 `defineTool` 的 `parameters` 字段**编译期推导**：

```ts
defineTool({
  parameters: {
    path:   { type: 'string', required: true },
    limit:  { type: 'number' },              // 可选
    mode:   { type: 'string', enum: ['read', 'write'] },  // 字面量联合
  },
  // 推导出: args: { path: string; limit?: number; mode?: 'read' | 'write' }
})
```

### 五层契约

| 层 | 契约 | 执行者 |
|----|------|--------|
| **1. 编译期推导** | `parameters` → TypeScript 类型，`args.path: string` 等无需手动类型转换 | `defineTool` |
| **2. 运行时校验** | `execute` 运行前，`defineTool` 拒绝违反模式的模型生成参数——类型、必填键、字面量约束、精确单一联合、嵌套值均检查；抛出带路径的 `ToolArgsError` | 注册表 |
| **3. 物化与分离** | 注册表在一次递归遍历中将 `arguments` 物化为**分离的无损 JSON**——与原始模型输出断开引用 | 注册表 |
| **4. 冻结** | 物化后立即 `deepFreeze()`，策略启动前冻结该值，后续阶段无法篡改 | 注册表 |
| **5. 只读身份** | `callId`、`name`、`arguments`、`agent`、`token` 以及调用者拥有的 `signal` 均为只读，所有包装器中保持不变 | 管线 |

> **核心不变量：** `args` 是深度冻结的、无损快照的参数。你的 `execute` 拿到的永远是一个不可变副本，无论管线中多少个监听器在它之前运行过。

---

## 二、`exec` — `ToolRunContext` 运行时上下文

`exec` 提供调用身份、中止机制和两个运行时动作。所有字段均为只读。

### 完整字段

| 字段 | 类型 | 用途 | 约束 |
|------|------|------|------|
| `exec.signal` | `AbortSignal` | 中止信号——触发时必须取消进行中的工作 | **强制性遵从**，非可选 |
| `exec.agent` | `Agent` (作用域引用) | 异步通知键——`tools/result` 事件由此键控，实现多 Agent 隔离 | 只读 |
| `exec.token` | 不透明品牌类型 | 调用身份令牌——由注册表分配，不可构造或伪造 | 只读，受身份保护 |
| `exec.callId` | `CallId` (品牌字符串) | 本次调用的唯一标识符 | 只读，与 `tool/call` 事件配对 |
| `exec.name` | `string` | 工具名称 | 只读 |
| `exec.arguments` | `string` (原始 JSON) | 模型输出的原始参数字符串 | 只读，端到端均为原始 JSON |
| `exec.deferContext(context)` | `(UserMessage) => void` | 将后续指令/嵌套调度上下文附加到此调用结果 | Agent 循环在 `tool/result` 之后才发出 |
| `exec.concludeTurn()` | `() => void` | 将当前 Agent 回合标记为终止 | 循环在提交此结果批次后停止 |

### 关键方法详解

#### `exec.signal` — 中止契约（最重要！）

```ts
// ✅ 正确：所有 I/O 转发 signal
async execute(args, exec) {
  const resp = await fetch(url, { signal: exec.signal })
  const body = await resp.text()
  return body
}

// ❌ 错误：忽略 signal
async execute(args, exec) {
  return await fetch(url)  // 用户中止时无法取消
}
```

**为什么是强制的？** 注册表通过 `tools/execute` 瀃布流的环绕调度替换 `exec.signal`（例如 `AbortSignal.timeout(timeoutMs)`），保留调用者的原始取消操作。但**你的代码必须配合转发**，否则中止信号无法生效。

#### `exec.deferContext(context: UserMessage)` — 延迟上下文注入

```ts
// 复合工具（如 run_code）运送嵌套调度上下文
async execute(args, exec) {
  const result = await runCodeInSubprocess(args.code)
  exec.deferContext({
    role: 'user',
    content: `Subprocess completed with exit code ${result.exitCode}`,
  })
  return result
}
```

**关键语义：** Agent 循环**直到 `tool/result` 事件被记录后**才发出 `deferContext` 的内容，确保模型在任何注入指令之前先看到工具结果。复合工具（如 `run_code`）通过此机制运送嵌套调度上下文；叶工具可以铸造新鲜的插件源指令。

#### `exec.concludeTurn()` — 终止回合

```ts
// 工具执行后直接结束当前 Agent 回合
async execute(args, exec) {
  await performFinalAction(args)
  exec.concludeTurn()
  return { done: true }
}
```

**传播机制：** 该标记搭载在 `ToolExecutionSuccess.concludesTurn: true` 上。调度器观察到此标志时**向上传播**——嵌套调用的复合工具从嵌套结果转发它，因此**只有权威的嵌套成功才能结束封闭运行**。

> `concludesTurn: true` **不会**短路已提交的 `next-step` 工作——同步骤的 `additionalContexts` 或竞争的引导仍会执行，轮次仅在收件箱排空后关闭。

#### `exec.agent` — 异步通知

```ts
// 向 Agent 注入持久上下文（下一次模型请求可见）
exec.agent.inject({
  content: 'File was modified by this tool call',
  source: { kind: 'plugin', plugin: 'my-tool' },
})
```

⚠️ `agent.inject()` 追加**持久上下文**（下一次模型请求可见），但它**不是唤醒信号**。需要 try/catch 防御已销毁的 Agent。

---

## 三、`execute()` 返回值契约

```
execute(args, exec) → Promise<InferredValue>
         │
         ▼
    注册表递归遍历物化为分离的无损 JSON
         │
         ▼
    output.schema 校验（ValueSchemaSpec）
         │
         ├── 校验通过 → deepFreeze() → 传给 output.render → ContentBlock[]
         │
         └── 校验失败 / 抛异常 → isError: true → ToolExecutionResult
```

| 返回方式 | 管线行为 | 语义 |
|----------|----------|------|
| 返回通过 `output.schema` 的值 | 快照 → 冻结 → render → `tool/result` | 领域成功 |
| 返回不符合 schema 的值 | 包含为 `isError` 结果 | 编程错误 |
| `throw` 异常 | 捕获 → 结构化 `{ name, code }` 元数据 → `isError` 结果 | 基础设施故障 |

### 领域失败 vs 基础设施故障

```ts
// ✅ 领域失败：用规范值表示（模型可以理解并重试）
execute(args, exec) {
  if (!fileExists) return { ok: false, error: 'File not found' }
}

// ✅ 基础设施故障：用 throw 表示（管线归类为 isError）
execute(args, exec) {
  if (networkDown) throw new Error('Network unreachable')
}
```

---

## 四、契约速查表

| # | 契约 | 违反后果 |
|---|------|----------|
| 1 | 参数已预校验，无需重复校验 | 多余代码 |
| 2 | 注册后定义不可变 | 未定义行为 |
| 3 | 执行身份受保护（`callId/name/arguments/agent/token/signal` 均只读） | 管线拒绝 |
| 4 | 返回值必须通过 `output.schema` 验证 | `isError` 结果 |
| 5 | 领域失败用规范值，基础设施故障用 throw | 结果语义错误 |
| 6 | 必须遵从 `exec.signal`（强制性） | 用户中止时工具继续运行 |
| 7 | 变更类工具必须使用 `presentationMeta` | 重放时 UI 差异卡片丢失 |
| 8 | `deferContext` 内容在 `tool/result` 之后才发出 | 模型看不到工具结果就被注入指令 |
| 9 | `concludeTurn` 不短路已提交的 `next-step` | 同步骤上下文被静默丢弃 |

来源：[schema.ts](packages/core/tools/src/schema.ts#L480-L570), [index.ts](packages/core/tools/src/index.ts#L200-L260), [index.ts](packages/core/tools/src/index.ts#L430-L476)

---

[工具执行流水线](13-tool-execution-pipeline)
[添加工具与适配器](19-adding-tools-and-adapters)
[Cordis 插件教程](3-cordis-plugin-tutorial)