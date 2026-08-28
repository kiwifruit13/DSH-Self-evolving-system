暴露给 `dsh-tools` 命名空间的事件接口共 **7 个**，分为两大类：**实时扩展点**（waterfall，可拦截）和 **会话持久事件**（emit，不可拦截）。

---

## 一、实时扩展点（waterfall 事件）

这 4 个事件是**管线拦截面**，监听器必须调用 `next()` 以委托，均由 `dsh-tools` 声明，**按 Agent 作用域过滤**（Agent 作用域监听器仅接收该 Agent 的调用）：

| 事件 | 派发模式 | 用途 | 管线阶段 |
|------|----------|------|----------|
| `tools/pre-execute` | **waterfall** | 工具执行前：门控、修改或否决 | 阶段 2 |
| `tools/execute` | **waterfall** | 工具执行：包裹或替换（环绕调度） | 阶段 5 |
| `tools/post-execute` | **waterfall** | 工具执行后：接受、替换、丰富或阻塞规范化结果 | 阶段 6 |
| `tools/code-dispatch-log` | **waterfall** | 代码运行时分派日志拦截：替换 `run_code` 子调度持久日志副本中的内容 | 阶段 5 内部 |

### 各事件详细语义

**`tools/pre-execute`** — 监听器返回三种决策：
```ts
{ kind: 'allow' }                    // 进入单调守卫
{ kind: 'deny', reason: string }     // 跳过工具主体
{ kind: 'ask' }                      // 请求人工批准（→ 阶段 4）
```

**`tools/execute`** — 环绕调度瀑布流，监听器接收 `(exec, next)`：
- 超时：用 `AbortSignal.timeout()` 替换 `exec.signal`
- 重试：瞬态故障时重新调用 `next()`
- 指标：在 `next()` 周围记录调度持续时间 / token 成本 / 错误分类

**`tools/post-execute`** — 监听器可接受、替换、丰富或阻塞规范化结果。被拒绝的调用也进入此瀑布流。

**`tools/code-dispatch-log`** — 专用于 `run_code` 子调度的持久日志内容替换，不影响执行语义。

---

## 二、会话持久事件（emit 事件）

这 3 个事件是**即发即弃广播**，监听器无法拦截或修改结果，失败被隔离在每个监听器内：

| 事件 | 派发模式 | 用途 | 持久化 |
|------|----------|------|--------|
| `tool/call` | **emit**（会话追加） | 记录工具调用请求：携带 `turn`、`step`、`callId`、`name`、原始 `arguments` | 是 — 追加到会话日志 |
| `tool/result` | **emit**（会话追加） | 记录冻结的工具执行结果，通过 `sourceEventSeqs` 链接到起源的 `tool/call` | 是 — 追加到会话日志 |
| `tools/change` | **emit** | 工具注册表变更通知（注册/注销） | 否 — 仅实时通知 |

### 关键不变量

- **`tool/call` 与 `tool/result` 严格配对**：每个 `tool/call` 事件与恰好一个 `tool/result` 事件配对，即使对于被跳过/拒绝的调用也会记录合成的 `isError` 结果（保持重放有效性）
- **`tool/result` 按 `exec.agent` 键控**：多 Agent 进程中每个 Agent 的工具结果独立运行，无交叉污染
- **`tools/change` 刻意不过滤**：全局注册表变更涉及每个 Agent 的下一次提示词组装，因此所有作用域监听器可见

来源：[index.ts](packages/core/tools/src/index.ts#L430-L476), [tool-calls.ts](packages/core/agent-loop/src/tool-calls.ts#L129-L145)

---

## 三、工具拥有的会话事件

工具主体在执行期间还可以发出**工具拥有的持久会话事件**，这些是追加到会话日志的持久记录，但**不会重新进入模型上下文**：

| 事件 | 用途 |
|------|------|
| `todo/write` | 写入待办事项 |
| `fs/observed` | 文件系统观察记录 |
| `hook/invoked` | 钩子调用记录 |
| `hook/result` | 钩子结果记录 |
| `tool/code-dispatch` | 代码分派记录 |

---

## 四、完整关系图

```
模型输出 tool_call
    │
    ▼
  tool/call  ──────────────────────>  (emit, 追加会话日志)
    │
    ▼
  tools/pre-execute  ──────────────>  (waterfall, allow/deny/ask)
    │
    ▼
  [单调守卫] + [批准解析]
    │
    ▼
  tools/execute  ──────────────────>  (waterfall, 环绕调度)
    │   └─ tools/code-dispatch-log  (waterfall, 日志拦截)
    ▼
  execute(args, exec)
    │   └─ 工具拥有事件: todo/write, fs/observed, hook/invoked, hook/result, tool/code-dispatch
    ▼
  tools/post-execute  ─────────────>  (waterfall, 接受/替换/丰富/阻塞)
    │
    ▼
  tool/result  ─────────────────────>  (emit, deepFreeze(), 追加会话日志)
    │
    ▼
  tools/change  ───────────────────>  (emit, 注册表变更, 不过滤)
```

---

想进一步了解事件系统的派发模式与类型化声明机制？

[类型化事件与分发模式](10-typed-events-and-dispatch-modes)
[添加工具与适配器](19-adding-tools-and-adapters)
[沙箱与审批策略](16-sandbox-and-approval-policy)