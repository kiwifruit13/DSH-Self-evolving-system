子 Agent 与系统之间暴露的事件接口分为三个层面：**Cordis 声明式生命周期事件**、**面向模型的工具接口**、**服务端 API**。逐一展开：

---

## 一、Cordis 声明式生命周期事件

在 `SubagentRuntime` 的 `declare module` 块中注册（[index.ts](packages/subagent/subagent/src/index.ts#L129-L168)），任何插件均可通过 `ctx.on('subagent/xxx', ...)` 订阅：

| 事件名 | 分发模式 | 载体类型 | 作用域过滤 | 说明 |
|---|---|---|---|---|
| `subagent/provider-added` | emit | `SubagentProvider` | ❌（全局） | 提供者注册到注册表 |
| `subagent/provider-removed` | emit | `string` | ❌（全局） | 提供者从注册表注销，已接受的运行保持持有者拥有 |
| `subagent/start` | emit | `SubagentRunInfo` | ✅（按委派父代理） | 子代理已启动，与 `subagent/end` 通过 `runId` 配对 |
| `subagent/end` | emit | `SubagentRunEndInfo` | ✅（按委派父代理） | 子代理已结算 |

### 载体详情

**`SubagentRunInfo`**（[types.ts](packages/subagent/subagent/src/types.ts#L36-L50)）——`subagent/start` 的载体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `runId` | `SubagentRunId` | 唯一标识，与配对的 `subagent/end` 共享 |
| `provider` | `string` | 创建子代理时记录的提供者名称（冷恢复时可能缺失） |
| `id` | `SessionId` | 子代理的会话 ID |
| `local` | `boolean` | 快照：`SubagentRun.localAgent` 在启动兑现时是否存在 |

**`SubagentRunEndInfo`**（[types.ts](packages/subagent/subagent/src/types.ts#L56-L73)）——`subagent/end` 的载体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `runId` | `SubagentRunId` | 与配对 `subagent/start` 共享的标识 |
| `provider` | `string` | 同配对启动事件 |
| `id` | `SessionId` | 子代理会话 ID |
| `local` | `boolean` | 同配对启动事件 |
| `stopReason` | `SubagentStopReason` | 终端停止原因 |
| `lastAssistantMessage` | `ContentBlock[]` | 子代理最终的助手输出（基础设施拒绝或无产出时缺失） |

### 停止原因联合

`SubagentStopReason`（[types.ts](packages/subagent/subagent/src/types.ts#L200-L214)）是合并可扩展的封闭联合：

| 值 | 语义 |
|---|---|
| `completed` | 子代正常完成轮次 |
| `aborted` | 通过请求 signal 或处置取消 |
| `error` | 模型或传输失败 |
| `max-tokens` | 子代触及 token 上限 |
| `refusal` | 子代拒绝了任务 |

### 作用域过滤规则

`subagent/start` 和 `subagent/end` 使用**作用域过滤派发**——载体键为委派父代理。在特定父代理作用域下注册的监听器**只能看到自己的委派**，永远看不到其他父代理的子代理事件。提供者增删事件无作用域过滤（全局可达）。

### 监听器故障包含

生命周期发射器（[lifecycle.ts](packages/subagent/subagent/src/lifecycle.ts#L100-L120)）对每个监听器**独立包含**故障：同步抛出或异步拒绝仅记录警告，不会饿死同级监听器、改变运行、或（对于从销毁器触发的 `provider-removed`）破坏拆卸。

---

## 二、面向模型的工具接口

模型通过以下三个工具与子代理系统交互：

### 2.1 `subagent` 工具 — `dsh-tool-subagent`

（[index.ts](packages/subagent/tool-subagent/src/index.ts#L1-L9)）

配置（[index.ts](packages/subagent/tool-subagent/src/index.ts#L29-L79)）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `provider` | （必填） | 委派到的提供者名称 |
| `toolName` | `'subagent'` | 面向模型的工具名 |
| `enableRunInBackground` | `true` | 是否暴露 `run_in_background` 参数 |
| `backgroundMode` | `'one-shot'` | 后台执行策略：`one-shot` 或 `continuable` |
| `agentOptions` | — | 应用到每个子代的 Agent 选项 |
| `persona` | — | 子代角色覆盖 |
| `toolFilter` | — | 子代工具过滤（`allow`/`deny`） |
| `maxDepth` | `3` | 最大子代深度（数字或 `'provider-managed'`） |

### 2.2 `send_message` 工具 — `dsh-tool-subagent-control`

（[index.ts](packages/subagent/tool-subagent-control/src/index.ts#L26-L77)）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subagent_id` | `string` | ✅ | 后台子代理的 ID |
| `message` | `string` | ✅ | 投递给子代的消息 |

**语义**：消息成为子代的下一个 FIFO 轮次。若子代仍在工作，消息排队等到当前轮次结束。返回 `messageId` 确认投递，不返回子代的应答。

### 2.3 `interrupt_agent` 工具 — `dsh-tool-subagent-control`

（[index.ts](packages/subagent/tool-subagent-control/src/index.ts#L79-L119)）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | `string` | ✅ | 要中断的代理 ID |

**语义**：请求取消目标代理的当前轮次。已排队的消息保留、子代创建的代理继续运行、代理本身可用于后续 `send_message`。即发即返——目标可能短暂继续运行直到观测到信号。中断已完成的代理是合法的无操作。返回 `{ accepted: true }`。

### 2.4 `report` 工具 — `dsh-tool-subagent-report`

（[index.ts](packages/subagent/tool-subagent-report/src/index.ts#L49-L100)）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `output` | `string` | ✅ | 报告给父代理的可操作内容 |

**语义**：子代主动向其直接父级报告选定内容。报告**不结束**子代的轮次或工作。失败调用可能仍然已经到达，因此不应盲目重试。

**安装范围**：仅在**可继续的进程内子代理**中安装。根代理、一次性子代、远程提供者、无代理执行永远不会看到此工具。

**投递策略**（[index.ts](packages/subagent/tool-subagent-report/src/index.ts#L27-L38)）：

| 策略 | 语义 |
|---|---|
| `next-step`（默认） | 唤醒父代理，在其最近的步骤边界进入 |
| `quiet` | 添加相同上下文但不唤醒，驻留的父代理等待下一个唤醒输入 |

---

## 三、服务端 API（`ctx.subagents`）

`SubagentRuntime` 暴露给宿主和插件的编程接口（[index.ts](packages/subagent/subagent/src/index.ts#L171-L515)）：

| 方法 | 返回类型 | 说明 |
|---|---|---|
| `registerProvider(provider)` | `() => void` | 注册提供者（effect 作用域，HMR 安全） |
| `getProvider(name)` | `SubagentProvider \| undefined` | 按名称查找提供者 |
| `list()` | `string[]` | 列出已注册提供者名称（插入序） |
| `start(name, request)` | `Promise<SubagentRun>` | 启动一次性前台运行 |
| `startContinuable(spec)` | `Promise<ContinuableStart>` | 建立可继续子代并投递初始提示词 |
| `followup(parent, childId, content, options)` | `Promise<MessageId>` | 向可继续子代追加消息 |
| `interrupt(targetSessionId, authority)` | `void` | 中断可继续子代当前轮次 |
| `reportFrom(child, content, options)` | `Promise<MessageId>` | 子代向父代主动报告 |
| `registerContinuableSetup(contribution)` | `() => void` | 组合部署能力到每个可继续子代的创建上下文 |
| `drainContinuableDescendants(parents)` | `Promise<void>` | 排空指定父代下的所有可继续后代 |
| `drainContinuableChildren(parent, childIds)` | `Promise<void>` | 释放指定父代的选定直接子代 |
| `listChildren(parentSessionId, signal)` | `Promise<SubagentListEntry[]>` | 枚举直接子代 |
| `listDescendants(rootSessionId, signal)` | `Promise<SubagentDescendantListEntry[]>` | 枚举完整后代树（前序） |

---

## 四、消息来源类型（归属追踪）

系统通过 `MessageSourceMap` 扩展（[continuation.ts](packages/subagent/subagent/src/continuation.ts#L57-L98)）区分三种消息来源，确保会话记录中归属清晰：

| kind | form | 语义 |
|---|---|---|
| `coordinator` | `relay` | 协调者（模型）的 follow-up，`senderSessionId` 为发起工具调用的代理会话 |
| `subagent-report` | `relay` | 子代主动报告，`senderSessionId` 为报告子代 |
| `subagent-settled` | `notice` | 运行时结算通知，`summary` 为一行描述，`senderSessionId` 为结算子代 |

关键区分：**report 是子代选择的内容，settled 是管理器陈述子代变成了什么**——混合两者会让孩子获得它从未写过的话语。

---

## 五、一次性运行的返回结构

`SubagentRun`（[types.ts](packages/subagent/subagent/src/types.ts#L256-L282)）是 `start()` 返回的持有者拥有的句柄：

| 字段/方法 | 类型 | 说明 |
|---|---|---|
| `id` | `SessionId` | 运行 ID（本地运行等于子代会话 ID） |
| `localAgent` | `Agent \| undefined` | 已发布的进程内子代（远程运行为 `undefined`） |
| `result` | `Promise<SubagentResult>` | 结算结果，**永不拒绝**——子代级失败以 `stopReason: 'error'` 兑现 |
| `dispose()` | `Promise<void>` | 取消剩余工作、达到静息、释放资源（幂等） |

`SubagentResult`（[types.ts](packages/subagent/subagent/src/types.ts#L219-L245)）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `output` | `ContentBlock[]` | 最终助手输出（跳过空内容消息） |
| `structured` | `unknown` | 满足 `outputSchema` 的结构化结果 |
| `diagnostic` | `string` | 提供者编写的非助手故障详情（≤4096 UTF-8 字节，不含敏感数据） |
| `stopReason` | `SubagentStopReason` | 运行结束原因 |

---

## 六、总览图

```
┌─────────────────────────────────────────────────────────┐
│                    Cordis 事件总线                        │
│  subagent/provider-added  (全局)                         │
│  subagent/provider-removed (全局)                        │
│  subagent/start  ──(作用域过滤)──▶ SubagentRunInfo       │
│  subagent/end    ──(作用域过滤)──▶ SubagentRunEndInfo    │
└─────────────────────────────────────────────────────────┘
          ▲                           ▲
          │ 生命周期发射              │ 消费/订阅
          │                           │
┌─────────┴───────────┐   ┌──────────┴──────────┐
│  SubagentRuntime     │   │  插件 / 宿主 / UI    │
│  (ctx.subagents)     │   │  ctx.on('subagent/…')│
│  start()             │   └─────────────────────┘
│  startContinuable()  │
│  followup()          │
│  interrupt()         │
│  reportFrom()        │
│  drain…()            │
│  list…()             │
└──────────┬──────────┘
           │ 注册/调用
           ▼
┌───────────────────────────────────────────────────────┐
│              面向模型的工具接口                         │
│  subagent     → 委派子代理（前台/后台）                 │
│  send_message → 向可继续子代追加消息                    │
│  interrupt_agent → 中断子代当前轮次                    │
│  report       → 子代向父代主动报告（仅可继续子代可见）   │
└───────────────────────────────────────────────────────┘
```

---

想继续深入事件分发机制或作用域过滤的底层原理：

[类型化事件与分发模式](10-typed-events-and-dispatch-modes)
[插件生命周期与副作用](8-plugin-lifecycle-and-effects)
[服务与依赖注入](9-services-and-dependency-injection)