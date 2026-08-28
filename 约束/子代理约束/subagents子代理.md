以下是主代理调用子代理的所有关键信息，按逻辑分层整理：

---

## 一、架构定位：委派与编排层

子代理接缝（`ctx.subagents`）和工作流接缝共同构成线束的**委派与编排层**。两者都是**可选的能力接缝**——不属于核心代理循环，类型存放在专用包中，存在与否取决于部署激活了哪些提供者和工具。

- **子代理接缝**：处理单个子代理委派（一次性前台运行 + 可继续的后台对话）
- **工作流接缝**：在上层叠加脚本执行引擎，允许代理在隔离的 `node:worker_threads` VM 中通过 `parallel()`、`pipeline()`、`agent()` 组合子扇出子代理调用

参见 [index.ts](packages/subagent/subagent/src/index.ts#L1-L32)

---

## 二、提供者注册表（Named Provider Registry）

与 LLM 适配器注册表类似，**多个提供者共存**，每个以唯一名称注册，调用者按名称选择。内置提供者如下：

| 包名 | 注册名 | 进程模型 | 说明 |
|---|---|---|---|
| `dsh-subagent-spawn-in-process` | `spawn` | 进程内 | 全新进程内子代理 |
| `dsh-subagent-fork-in-process` | `fork` | 进程内 | 以父级已完成轮次前缀为种子分叉（上下文继承） |
| `dsh-subagent-acp` | `acp` | 进程外 | 委派给 ACP 协议服务器 |
| `dsh-subagent-codex` | `codex` | 进程外 | 委派给 Codex |
| `dsh-subagent-claude-code` | `claude-code` | 进程外 | 委派给 Claude Code |
| `dsh-subagent-dsh-sdk` | `dsh-sdk` | 进程外 | 委派给 DSH SDK 运行时 |

这种三层分离意味着：部署可以替换提供者（如从进程内 spawn 换为进程外 ACP），而**模型看到的工具界面不变**。

---

## 三、核心操作：启动与继续

服务对外暴露三个关键操作（见 [index.ts](packages/subagent/subagent/src/index.ts#L16-L24)）：

| 操作 | 语义 |
|---|---|
| `start()` | 返回一个已发布的、拥有所有权的一次性运行（`SubagentRun`） |
| `startContinuable()` | 建立一个持久的可继续子代理（后台对话） |
| `followup()` | 向可继续子代理追加内容，不暴露子代理是否驻留 |

---

## 四、请求结构与能力标志

一次性启动请求 `SubagentStartRequest`（见 [types.ts](packages/subagent/subagent/src/types.ts#L100-L149)）包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `prompt` | `ContentBlock[]` | 传递给子代理的用户消息 |
| `parent` | `Agent` | 发起代理（进程内提供者从中推导工作区、世系、委派深度） |
| `signal` | `AbortSignal` | 取消信号——启动前后均可触发取消 |
| `outputSchema` | `ObjectJsonSchema` | 结构化输出模式（子代理返回经模式验证的 JSON） |
| `maxDepth` | `number` | 子代理的绝对委派深度上限 |
| `toolFilter` | `ToolRestriction` | 工具作用域限制（命名工具从提示词中消失且拒绝执行） |
| `persona` | `string` | 子代理专属角色覆盖（遮蔽部署级 persona） |
| `label` | `string` | 可选短标签 |

对应的能力标志 `SubagentCapabilities`（见 [types.ts](packages/subagent/subagent/src/types.ts#L86-L91)）：

```typescript
interface SubagentCapabilities {
  readonly outputSchema: boolean
  readonly depthLimit: boolean
  readonly toolFilter: boolean
  readonly persona: boolean
}
```

**关键规则**：如果请求需要所选提供者缺乏的能力，服务以 `SubagentError('UNSUPPORTED_CAPABILITY')` **拒绝**，而非接受后静默忽略——"fail loud, no silent degradation"。

---

## 五、生命周期与结算

1. **管理器独立拥有激活**：后续调用者取消既不会取消已接受的轮次，也不会处置子代理
2. **结算通知**：当驻留激活结算时，管理器向子代理的持久直接父级交付结算通知，描述该时期如何结束并附带最终的助手内容
3. **主动报告**：子代理可通过 `SubagentRuntime.reportFrom()` 主动向其父级报告，管理器使用相同的父级解析逻辑路由
4. **事件对**：`subagent/start` 与 `subagent/end` 通过 `runId` 配对

---

## 六、深度限制与工具过滤的组合

- 工作流脚本**继承**部署的子代理提供者、深度限制和工具过滤，无需显式配置
- 工作流级别的 `maxTotalAgents` 与子代理级别的 `maxDepth` **组合**——引擎计算总 `agent()` 调用数，子代理服务强制执行逐子深度
- 工作流的取消信号通过子代理接缝的标准 `signal` 通道传播到每个子代理

---

## 七、面向模型的工具（消费者层）

| 包 | 角色 | 说明 |
|---|---|---|
| `dsh-tool-subagent` | 消费者 | 面向模型的 `subagent` 工具 |
| `dsh-tool-subagent-control` | 消费者 | 子代理控制工具（列出、中断等） |
| `dsh-tool-subagent-report` | 消费者 | 子代理主动报告工具 |
| `dsh-tool-workflow` | 消费者 | 面向模型的 `workflow` 工具 |
| `dsh-tool-ralph` | 消费者 | 专用工作流消费者变体 |

---

想更深入理解这些子系统的话，按以下顺序阅读效率最高：

[子 Agent 与工作流系统](20-subagent-and-workflow-system)
[Python SDK 与 ACP 桥接](21-python-sdk-and-acp-bridge)
[服务与依赖注入](9-services-and-dependency-injection)