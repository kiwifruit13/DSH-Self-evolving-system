好，信息已经足够了。以下是子 Agent 与工作流系统的完整展开。

---

# 子 Agent 与工作流系统

## 一、架构定位与设计哲学

子代理接缝和工作流接缝共同构成线束的**委派与编排层**——父代理借此将工作委派给子代理，而模型编写的 JavaScript 脚本借此大规模协调此类委派。

两者都是**可选的能力接缝**：它们不属于核心代理循环，类型存放在专用包中，且它们的存在取决于部署激活了哪些提供者和工具。

关键分层关系：
- **子代理接缝**处理**单个**子代理委派（一次性前台运行 + 可继续的后台对话）
- **工作流接缝**是子代理接缝的**消费者**——在上层叠加脚本执行引擎，允许代理在隔离的 `node:worker_threads` VM 中通过 `parallel()`、`pipeline()`、`agent()` 组合子扇出子代理调用
- 工作流**不重复**子代理的组合逻辑，而是消费它

代码入口：[index.ts](packages/subagent/subagent/src/index.ts#L1-L32)、[index.ts](packages/workflow/workflow/src/index.ts#L1-L5)

---

## 二、子代理服务（`ctx.subagents`）核心概念

### 2.1 命名提供者注册表

与 bash 接缝（每个上下文一个执行器，二次加载抛出异常）不同，子代理接缝支持**多个提供者共存**——每个以唯一名称注册，调用者按名称选择。设计镜像 LLM 适配器注册表，而非单服务执行器。

注册 API 见 [index.ts](packages/subagent/subagent/src/index.ts#L385-L401)：

```typescript
registerProvider(provider: SubagentProvider): () => void
```

注册是 **effect 作用域**的——移除提供者会阻止新的启动，但**不会撤销**已经返回给持有者的运行。HMR 安全。

内置提供者一览：

| 包名 | 注册名 | 进程模型 | 说明 |
|---|---|---|---|
| `dsh-subagent-spawn-in-process` | `spawn` | 进程内 | 全新进程内子代理 |
| `dsh-subagent-fork-in-process` | `fork` | 进程内 | 以父级已完成轮次前缀为种子分叉（上下文继承） |
| `dsh-subagent-acp` | `acp` | 进程外 | 委派给 ACP 协议服务器（Python SDK 走这条路） |
| `dsh-subagent-codex` | `codex` | 进程外 | 委派给 Codex |
| `dsh-subagent-claude-code` | `claude-code` | 进程外 | 委派给 Claude Code |
| `dsh-subagent-dsh-sdk` | `dsh-sdk` | 进程外 | 委派给 DSH SDK 运行时 |

这种三层分离（**服务定义 → 提供者 → 消费者**）意味着：部署可以替换提供者（如从进程内 spawn 换为进程外 ACP），而模型看到的工具界面不变；硬化的引擎可以替换参考实现，而部署不需要改动。

### 2.2 能力标志与请求结构

服务在委派前校验提供者能力。请求 `SubagentStartRequest`（[types.ts](packages/subagent/subagent/src/types.ts#L100-L149)）与能力标志 `SubagentCapabilities`（[types.ts](packages/subagent/subagent/src/types.ts#L86-L91)）一一对应：

| 能力标志 | 请求字段 | 语义 |
|---|---|---|
| `outputSchema` | `outputSchema` | 子代理返回根据调用者提供的 JSON Schema 验证的结构化 JSON 值 |
| `depthLimit` | `maxDepth` | 子代理的绝对委派深度上限；其计算深度必须 ≤ 此值 |
| `toolFilter` | `toolFilter` | 作用域工具限制：命名工具从子代理提示词中消失**且**拒绝执行（一处可见性，无静默降级） |
| `persona` | `persona` | 子代理专属角色覆盖——遮蔽部署级 persona，仅对此子代理生效 |

**核心规则：Fail Loud, No Silent Degradation**

如果请求需要所选提供者缺乏的能力，服务以 `SubagentError('UNSUPPORTED_CAPABILITY')` 拒绝，而非接受后静默忽略。校验逻辑见 [index.ts](packages/subagent/subagent/src/index.ts#L497-L512)：

```typescript
private assertCapabilities(provider, request): void {
  const needs = [
    { when: request.outputSchema !== undefined, cap: 'outputSchema' },
    { when: request.maxDepth !== undefined, cap: 'depthLimit' },
    { when: request.toolFilter !== undefined, cap: 'toolFilter' },
    { when: request.persona !== undefined, cap: 'persona' },
  ]
  for (const { when, cap } of needs) {
    if (when && !provider.capabilities[cap])
      throw new SubagentError(`…does not support the "${cap}" capability`, 'UNSUPPORTED_CAPABILITY')
  }
}
```

### 2.3 深度计算

委派深度记录在 `AgentOptions.subagentDepth` 中（[depth.ts](packages/subagent/subagent/src/depth.ts#L11-L16)），零表示顶层代理，子代理为父深度 +1。

关键不变量——**持久会话头是权威且单调的**：

```typescript
function delegationDepthOf(agent: Agent): number {
  const runtime = agent.options.subagentDepth
  return Math.max(agent.session.header.delegationDepth ?? 0, runtime ?? 0)
}
```

运行时 `subagentDepth` 可以**加深**但永远不能**降低**持久深度——否则一个恢复的子代理会获得顶层代理的委派能力，这是安全漏洞。

### 2.4 核心操作

`SubagentRuntime` 类（[index.ts](packages/subagent/subagent/src/index.ts#L171-L515)）暴露以下操作：

#### `start(name, request)` → `SubagentRun`
建立一次性前台运行。能力校验 → 深度校验 → 快照描述符 → 委派提供者 → 发布生命周期事件。返回的 `SubagentRun` 是持有者拥有的——提供者的所有权持续到 promise 兑现，拒绝时没有运行需要处置。

#### `startContinuable(spec)` → `ContinuableStart`
建立持久可继续子代理，投递初始提示词。当子代理的收件箱接受提示词时即返回（不等轮次开始或消息到达会话日志）。

#### `followup(parent, childId, content, options)` → `MessageId`
向可继续子代理追加内容。驻留子代理的 Agent 收件箱直接接受（唤醒 `waiting` 的 Activation）；不驻留的从持久 Session 冷恢复。Agent 收件箱是唯一的队列，因此每个被接受的消息有一个可观测的顺序。

#### `interrupt(targetSessionId, authority)` → void
中断一个活跃的可继续子代理当前轮次。即发即返——取消信号在返回前发出，但目标可能继续运行直到观测到信号。未认领的待处理收件箱工作、Activation 和已发布的后代均保留；已认领的工作不会重新入队。

#### `reportFrom(child, content, options)` → `MessageId`
子代理**主动**向其持久直接父级报告选定内容。报告**不会**结束子代理的轮次或 Activation。消息来源类型为 `'subagent-report'`，与结算通知（`'subagent-settled'`）严格区分——两者语义不同，混入会让孩子获得它从未写过的话语。

#### `drainContinuableDescendants(parents)` → Promise\<void\>
关闭指定父代理以下的可继续准入，子优先地同步停止可见后代 Activation，然后等待已准入的具作用域物化释放这些森林。用于拆除路径。

#### `listChildren(parentSessionId, signal)` / `listDescendants(rootSessionId, signal)`
枚举直接子代理 / 完整后代树。基于投影注册表的**三档阶梯**解析：活注册表水印快照 → 持久投影缓存行 → 一次持久检查折叠。不加载或恢复任何 Agent。

### 2.5 可继续子代理的生命周期

可继续子代理由内部管理器 `SubagentContinuationManager`（[continuation.ts](packages/subagent/subagent/src/continuation.ts#L1-L22)）驱动，核心概念：

- **一个可继续子代理拥有一个持久 Session** 和**至多一个进程本地 Activation**（一个驻留时期对应一个重建的子 Agent）
- Activation 不是请求、结果、取消或 Task 边界——它可以执行多个 FIFO 轮次，在其创建的后代仍在运行时保持驻留
- Agent 收件箱是唯一的轮次队列——管理器拥有驻留权，Agent 循环拥有所有轮次排序和执行
- **管理器独立拥有激活**：后续调用者取消既不会取消已接受的轮次，也不会处置子代理

### 2.6 生命周期事件

事件通过 Cordis 作用域过滤派发——在父代理作用域下注册的监听器只能看到自己的委派：

| 事件 | 模式 | 载体 | 说明 |
|---|---|---|---|
| `subagent/provider-added` | emit | `SubagentProvider` | 提供者注册 |
| `subagent/provider-removed` | emit | `string` | 提供者注销 |
| `subagent/start` | emit（作用域过滤） | `SubagentRunInfo` | 子代理已启动，与 `subagent/end` 通过 `runId` 配对 |
| `subagent/end` | emit（作用域过滤） | `SubagentRunEndInfo` | 子代理已结算 |

### 2.7 结算通知

当驻留激活结算时，管理器向子代理的持久直接父级交付**结算通知**，描述该时期如何结束并附带最终的助手内容。对于每个已结算的可继续子代理，此交付是无条件的。

---

## 三、工作流服务（`ctx.workflowEngine`）核心概念

### 3.1 工作流引擎

`WorkflowEngine`（[index.ts](packages/workflow/workflow/src/index.ts#L157-L187)）是一个抽象 Cordis 服务，核心契约：

- 无效请求在发布前抛出
- 活运行是持有者拥有的，其 `result` 永不拒绝
- 取消和处置有界，处置在界限内等待子代理清理
- 生命周期监听器故障被**容纳**（不传播）
- `workflow/end` 恰好触发一次

唯一抽象方法：

```typescript
abstract start(request: WorkflowStartRequest): WorkflowRun
```

### 3.2 脚本元数据（Meta）

每个工作流脚本携带一个 `meta` 块（[types.ts](packages/workflow/workflow/src/types.ts#L46-L55)），引擎在脚本体执行前验证：

```typescript
interface WorkflowMeta {
  name: string            // kebab-case 名称（显示 + 持久化键）
  description: string     // 一行描述
  whenToUse?: string      // 何时使用此工作流的指引
  phases?: WorkflowPhase[] // 阶段声明（仅用于进度分组，无执行语义）
}
```

### 3.3 脚本组合子

工作流脚本在隔离的 `node:worker_threads` VM 中执行，可使用三个组合子：

| 组合子 | 语义 |
|---|---|
| `agent()` | 启动一个子代理（等价于直接调用子代理接缝） |
| `parallel()` | 并行扇出多个 `agent()` 调用 |
| `pipeline()` | 顺序管道多个 `agent()` 调用，前一步结果流入下一步 |

### 3.4 工作流生命周期事件

| 事件 | 载体 | 说明 |
|---|---|---|
| `workflow/start` | `WorkflowRunInfo` | 脚本 meta 验证通过，体即将执行 |
| `workflow/phase` | `WorkflowRunInfo` + `title` | 脚本进入一个阶段（`phase(title)` 调用） |
| `workflow/log` | `WorkflowRunInfo` + `message` | 脚本发出叙述行（`log(message)` 调用） |
| `workflow/agent-start` | `WorkflowRunInfo` + `WorkflowAgentInfo` | 一个 `agent()` 调用建立了发布的子代理运行 |
| `workflow/agent-end` | `WorkflowRunInfo` + `WorkflowAgentEndInfo` | 一个 `agent()` 调用结算，与 `agent-start` 通过 `seq` 配对 |
| `workflow/end` | `WorkflowRunInfo` + `WorkflowResultInfo` | 工作流运行结算，恰好触发一次 |

### 3.5 停止原因与错误处理

停止原因（[types.ts](packages/workflow/workflow/src/types.ts#L63)）是封闭联合：

| 原因 | 语义 |
|---|---|
| `completed` | 脚本运行到最终 `return` |
| `cancelled` | 运行被取消（调用者 `cancel()`/signal） |
| `error` | 脚本抛出、致命 `WorkflowError` 传播、或结果物化失败 |

**致命与非致命的区分**（[index.ts](packages/workflow/workflow/src/index.ts#L121-L148)）：

- `WorkflowError` 带有 `fatal` 标志，默认为 `true`
- `parallel()`/`pipeline()` **重新抛出**致命错误（拼错的选项或触发的上限必须响亮地杀死脚本）
- 子代理运行失败和普通阶段内脚本错误映射为该项的 `null`（非致命）

错误码分类：

| 错误码 | 类别 |
|---|---|
| `SCRIPT_PARSE` / `META_INVALID` / `INVALID_ARGUMENT` / `UNSUPPORTED_OPTION` / `UNSUPPORTED_SCHEMA` | 请求错误 |
| `AGENT_CAP` / `ITEM_CAP` | 资源上限 |
| `AGENT_START` / `AGENT_RESULT` | 子代理基础设施故障 |
| `RESULT_UNSERIALIZABLE` | 边界值不可序列化 |
| `CANCELLED` | 取消 |

---

## 四、子代理与工作流的交互规则

工作流是子代理接缝的消费者，这意味着：

1. **配置继承**：工作流脚本继承部署的子代理提供者、深度限制和工具过滤，无需显式配置
2. **限制组合**：工作流级别的 `maxTotalAgents` 与子代理级别的 `maxDepth` 组合——引擎计算总 `agent()` 调用数，子代理服务强制执行逐子深度
3. **取消传播**：工作流的取消信号通过子代理接缝的标准 `signal` 通道传播到每个子代理
4. **事件配对**：工作流 `agent-start` / `agent-end` 与子代理 `subagent/start` / `subagent/end` 分别配对——前者是工作流视角的调用生命周期，后者是子代理接缝视角的委派生命周期

---

## 五、面向模型的工具（消费者层）

| 包 | 角色 | 说明 |
|---|---|---|
| `dsh-tool-subagent` | 消费者 | 面向模型的 `subagent` 工具 |
| `dsh-tool-subagent-control` | 消费者 | 子代理控制工具（列出、中断等） |
| `dsh-tool-subagent-report` | 消费者 | 子代理主动报告工具 |
| `dsh-tool-workflow` | 消费者 | 面向模型的 `workflow` 工具 |
| `dsh-tool-ralph` | 消费者 | 专用工作流消费者变体 |

---

## 六、包结构总览

```
packages/subagent/
├── subagent/              # 服务定义（SubagentRuntime、类型、深度、生命周期、继续性管理器）
├── subagent-spawn-in-process/   # 提供者：进程内 spawn
├── subagent-fork-in-process/    # 提供者：进程内 fork（上下文继承）
├── subagent-in-process-driver/  # 共享驱动逻辑（继承、预设、结构化输出）
├── subagent-acp/                # 提供者：进程外 ACP
├── subagent-codex/              # 提供者：进程外 Codex
├── subagent-claude-code/        # 提供者：进程外 Claude Code
├── subagent-dsh-sdk/            # 提供者：进程外 DSH SDK
├── tool-subagent/               # 消费者：subagent 工具
├── tool-subagent-control/       # 消费者：控制工具
└── tool-subagent-report/        # 消费者：报告工具

packages/workflow/
├── workflow/                    # 服务定义（WorkflowEngine、类型、事件）
├── workflow-worker-thread/      # 工作线程执行引擎（host/worker/realm/session/protocol）
├── tool-workflow/               # 消费者：workflow 工具
└── tool-ralph/                  # 消费者：ralph 变体
```

---

要进一步深入，建议按以下顺序：

[服务与依赖注入](9-services-and-dependency-injection) — 理解接缝的服务定义/提供者/消费者三层模型
[Python SDK 与 ACP 桥接](21-python-sdk-and-acp-bridge) — ACP 提供者的完整工作原理
[工具执行流水线](13-tool-execution-pipeline) — 子代理工具在工具管道中的执行路径