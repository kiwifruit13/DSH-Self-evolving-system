下面是对**工具执行流水线**的完整展开。

---

## 核心定义

工具执行管线是一个**有序的、策略驱动的七阶段瀑布流通道**。每个由模型请求的工具调用，在其主体运行前、运行中和运行后都要经过该通道。它将可扩展的 Cordis 瀑布流与注册表拥有的单调守卫、批准提示词、文件系统门控和内容规范化交错在一起，为每次调用生成一个**冻结的、无损 JSON 的 `tool/result` 结果**。

> 该管线**不是**一个简单的前置/后置钩子对。它具有严格的顺序不变量，可防止重排攻击并保证**拒绝绝不会被后续的监听器逆转**。

来源：[index.ts](packages/core/tools/src/index.ts#L156-L210)

---

## 七阶段全景流程

```
模型输出工具调用块
        │
        ▼
┌─ 阶段1: 调度输入 ─┐
│  记录 tool/call    │
│  解析参数           │
│  调用 presentCall  │
└────────┬──────────┘
         ▼
┌─ 阶段2: 预执行瀑布流 (tools/pre-execute) ─┐
│  allow → 进入守卫                          │
│  deny   → 跳过主体，进入规范化              │
│  ask    → 进入批准解析                      │
└────────┬──────────────────────────────────┘
         ▼
┌─ 阶段3: 单调守卫 ─┐
│  全部弃权 → 允许    │
│  任一拒绝 → 绝对拒绝│
└────────┬──────────┘
         ▼
┌─ 阶段4: 批准解析 (ctx.approval) ─┐
│  allowed-once → 进入调度         │
│  rejected/cancelled → 拒绝       │
│  throw → 短路至规范化             │
└────────┬─────────────────────────┘
         ▼
┌─ 阶段5: 环绕调度瀑布流 (tools/execute) + 工具主体 ─┐
│  超时: AbortSignal.timeout()                      │
│  重试: 瞬态故障时重新调用 next()                   │
│  指标: 记录调度持续时间/token成本/错误分类          │
│  主体: execute(args, exec)                         │
└────────┬──────────────────────────────────────────┘
         ▼
┌─ 阶段6: 后执行瀑布流 (tools/post-execute) ─┐
│  接受 / 替换 / 丰富 / 阻塞规范化结果         │
└────────┬────────────────────────────────────┘
         ▼
┌─ 阶段7: 规范化与结果发射 ─┐
│  output.schema 验证        │
│  output.render 投影        │
│  冻结 tool/result 事件     │
│  调用 presentResult        │
└────────────────────────────┘
```

---

## 阶段 1 — 调度输入

当模型输出包含工具调用块时，Agent 循环的 `executeToolCalls` 开始调度：

- **记录 `tool/call` 事件**：携带 `turn`、`step`、`callId`、`name` 以及原始 `arguments` 字符串到会话日志
- **调用 `presentCall(args)`**：向 UI 发送待定状态卡片——这是一个**纯的、无副作用的**对已解析参数的投影，实时流传输和会话日志重放期间均运行同一函数，因此**必须仅依赖 `args`**
- **深度冻结参数**：参数在此刻被冻结为无损快照，后续阶段无法篡改

输入重写被**刻意排除**，因为参数已经在阶段 1 中被记录和展示。

来源：[tool-calls.ts](packages/core/agent-loop/src/tool-calls.ts#L271-L278), [index.ts](packages/core/tools/src/index.ts#L323-L337)

---

## 阶段 2 — 预执行瀑布流 (`tools/pre-execute`)

`tools/pre-execute` 是一个 **waterfall** 事件。监听器按注册顺序运行，每个监听器调用 `next()` 以委托给下一个。监听器返回三种决策之一：

| 决策 | 效果 | 典型生产者 |
|------|------|------------|
| `{ kind: 'allow' }` | 进入单调守卫 | 沙箱策略、钩子过滤器 |
| `{ kind: 'deny', reason }` | 跳过工具主体；reason 成为错误内容 | 权限拒绝、未知工具拒绝 |
| `{ kind: 'ask' }` | 请求人工批准（进入阶段 4） | 需人工确认的危险操作 |

> ⚠️ **预执行监听器的拒绝可以通过监听器重排来规避。** 安全关键的拒绝逻辑不应放在这里——应放在阶段 3 的单调守卫中。

来源：[index.ts](packages/core/tools/src/index.ts#L156-L170)

---

## 阶段 3 — 单调守卫

在预执行瀑布流稳定为 `allow` 之后，注册表评估所有已注册的**单调守卫**。

**守卫签名：**
```ts
(execution: Readonly<ToolExecution>) => string | undefined
```
- 返回拒绝原因字符串 → 拒绝
- 返回 `undefined` → 弃权（弃权）

**单调性含义：** 守卫没有 `allow` 结果。一旦任何守卫拒绝，后续任何监听器或重排都**无法逆转**该拒绝。这是管线核心的**安全性不变量**。

受身份保护的 `ToolExecution` 对象跨越守卫边界：在此点之后，可扩展策略不再能访问可变的执行状态，确保守卫基于工具主体将看到的**相同冻结身份**做出决策。

> 💡 **安全关键的拒绝逻辑应始终实现为注册的守卫，而不是 `tools/pre-execute` 监听器。** 守卫注册是匿名的（无名称键），因此两个插件不会因名称冲突——它们直接堆叠。

来源：[index.ts](packages/core/tools/src/index.ts#L656-L680), [index.ts](packages/core/tools/src/index.ts#L736-L745)

---

## 阶段 4 — 批准解析

当预执行监听器返回 `{ kind: 'ask' }` 时，注册表通过 `ctx.approval` 进行解析——这是一个面向用户的一次性提示词。

三种可能结果：

| 结果 | 语义 | 后续 |
|------|------|------|
| `allowed-once` | 单次授权，非全局许可 | 进入环绕调度 |
| `rejected` / `cancelled` / `unavailable` | 人类明确拒绝 / 请求撤回 / 服务不可用 | 跳过工具主体 |
| `throw` | 批准流程异常 | **短路至外部规范化**（绕过后执行） |

**安全失败设计：** 缺失的批准服务会导致需要人工确认的调用**无法静默通过**。这保留了会话重放有效性——事件流始终是一个完整的、平衡的序列。

来源：[index.ts](packages/core/tools/src/index.ts#L430-L476), [tool-calls.ts](packages/core/agent-loop/src/tool-calls.ts#L129-L145)

---

## 阶段 5 — 环绕调度瀑布流 (`tools/execute`) 与工具主体

`tools/execute` 是一个 **around-dispatch** 瀑布流：每个监听器包裹下一个，接收 `(exec, next)`，其中 `next()` 返回规范化的 `ToolExecutionResult`。

这是处理**横切调度关注点**的阶段：

| 关注点 | 实现方式 |
|--------|----------|
| **超时** | 用 `AbortSignal.timeout()` 替换 `exec.signal`；在主体前恢复原始信号 |
| **重试** | 在预算内遇到瞬态故障时重新调用 `next()` |
| **指标** | 在 `next()` 周围记录调度持续时间、token 成本或错误分类 |

最内层的 `next()` 最终调用工具的 `execute(args, exec)` 主体。`exec` 提供：

- **`exec.signal`**：中止信号（强制性遵从）
- **`exec.agent`**：异步通知键（用于 `tools/result` 事件的作用域隔离）
- **`deferContext(context)`**：将后续指令或嵌套调度上下文附加到此调用的结果中
- **`concludeTurn()`**：将当前 Agent 回合标记为终止

来源：[index.ts](packages/core/tools/src/index.ts#L171-L183), [index.ts](packages/core/tools/src/index.ts#L265-L312), [index.ts](packages/core/tools/src/index.ts#L408-L476)

---

## 阶段 6 — 后执行瀑布流 (`tools/post-execute`)

在工具主体稳定之后（或调用被拒绝后），`tools/post-execute` 瀑布流进行评估。监听器可以：

| 动作 | 说明 |
|------|------|
| **接受** | 透传原始规范化结果 |
| **替换** | 用新结果替换原始结果 |
| **丰富** | 在结果上附加额外元数据 |
| **阻塞** | 阻止结果发射（静默丢弃） |

被拒绝的调用也会进入后执行——监听器仍可观察拒绝原因，但无法逆转拒绝。

---

## 阶段 7 — 规范化与结果发射

`output` 声明强制执行**规范值契约**：

1. `execute()` 返回值必须通过 `output.schema` 验证
2. `output.render()` 将该值投影为面向模型的 `ContentBlock[]`
3. 核心将结果**冻结**为无损 JSON，记录 `tool/result` 事件到会话日志
4. 调用 `presentResult(args, result)` 生成 UI 完成状态卡片

这种两阶段分离意味着管线可以**验证、快照和重新渲染，而无需重新执行工具主体**——这是重放安全性的基石。

---

## 调度器关键属性

| 属性 | 说明 |
|------|------|
| **模型顺序提交** | 无论调度重叠情况如何，结果均按原始调用顺序提交；已稳定的槽位会等待直到所有更早的槽位提交 |
| **注册表变更可见性** | 提交发生在重新分类后续调用之前，执行期间修改注册表的工具会影响未启动的调用 |
| **中止安全性** | 中止停止新启动、排空进行中的调度、为跳过的调用记录合成 `isError` 结果（保持重放有效性）；内部调度器失败排空而**不捏造**恢复结果 |
| **作用域过滤** | 所有管线瀑布流（`tools/pre-execute`、`tools/execute`、`tools/post-execute`）由 `@deepseek-ai/dsh-scope` 按 Agent 作用域过滤——Agent 作用域监听器仅接收该 Agent 的调用 |
| **`tools/result` 按 `exec.agent` 键控** | 多 Agent 进程中每个 Agent 的工具策略独立运行，无交叉污染 |
| **`tools/change` 不过滤** | 全局注册表变更涉及每个 Agent 的下一次提示词组装，因此所有作用域监听器可见 |

---

## `concludeTurn()` 与 `deferContext()` 机制

- **`deferContext(context)`**：将后续指令或嵌套调度上下文附加到调用结果。Agent 循环**直到 `tool/result` 事件被记录后**才发出它，确保模型在任何注入指令之前看到工具结果。复合工具（如 `run_code`）通过此机制运送嵌套调度上下文。
- **`concludeTurn()`**：将当前 Agent 回合标记为终止。标记搭载在 `ToolExecutionSuccess.concludesTurn: true` 上。调度器观察到此标志时向上传播——嵌套调用的复合工具从嵌套结果转发它，因此**只有权威的嵌套成功才能结束封闭运行**。

> `concludesTurn: true` **不会**短路已提交的 `next-step` 工作——同步骤的 `additionalContexts` 或竞争的引导仍会执行，轮次仅在收件箱排空后关闭。

---

## ToolDefinition 契约总结

| 成员 | 管线阶段 | 约束 |
|------|----------|------|
| `output: ToolOutputDefinition` | 规范化 | schema + render() + 可选 presentationMeta() |
| `execute(args, exec)` | 调度 | 必须返回通过 output.schema 验证的值 |
| `concurrent?: boolean` | 调度 | `true` 选择并行组；默认互斥 |
| `presentCall?(args)` | 调用记录 | 纯、无副作用、重放安全 |
| `presentResult?(args, result)` | 结果发射 | 纯、无副作用、重放安全 |

---

想继续深入哪个方向？

[文件系统与子进程接缝](14-filesystem-and-subprocess-seams)
[沙箱与审批策略](16-sandbox-and-approval-policy)
[Agent 轮次与步骤生命周期](12-agent-turn-and-step-lifecycle)