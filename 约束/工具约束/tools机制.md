`tools` 模块是 DeepSeek Harness 中**模型与外部世界交互的唯一通道**——文件 I/O、Shell 执行、Web 检索，全部都是已注册的工具。以下是关键信息汇总：

---

### 1. 核心定位
- **包路径**：`core/tools`，对应上下文键 `ctx.tools`
- **职责**：作用域工具注册表 + 受守卫保护的执行管道
- **性质**：接缝（seam）——工具是注册的效应，支持运行时替换和拆卸

### 2. 工具定义与注册
- 使用 **`defineTool`** 定义工具，它会校验参数模式（类型、必填键、字面量约束、精确单一联合、嵌套值），违规时抛出 `ToolArgsError`
- 通过 **`ctx.tools.register(...)`** 注册到注册表；注册借用只读引用，注册后不可修改模式或替换回调
- 热替换需**销毁所属效应后重新注册**
- 注册自动推导 `ToolArgsMap` 与 `ToolOutputMap` 精确类型

### 3. 执行管线六阶段
每个工具调用按序流经：

| 阶段 | 事件名 | 语义 |
|------|--------|------|
| 预执行瀑布流 | `tools/pre-execute` | 门控、修改或否决（可被监听器重排规避） |
| 单调守卫 | — | 安全关键的拒绝逻辑，**拒绝是绝对的** |
| 调度 | `tools/execute` | 环绕调度：超时（`AbortSignal.timeout()`）、重试、指标记录 |
| 主体执行 | — | 实际运行工具 `execute` 函数 |
| 后执行瀑布流 | `tools/post-execute` | 接受、替换、丰富或阻塞规范化结果 |
| 结果发射 | `tools/result` | 最终结果提交 |

### 4. 关键约束与安全属性
- **执行身份受保护**：工具签名不可伪造
- **必须遵从 `exec.signal`**：`AbortSignal` 触发时取消进行中工作，这是强制性的
- **单调守卫优先于预执行监听器**：安全拒绝逻辑应实现为守卫而非监听器
- **中止安全性**：中止停止新启动、排空进行中调度、为跳过的调用记录合成 `isError` 结果（保持重放有效性）
- **结果按原始调用顺序提交**：即使并发调度，已稳定槽位会等待更早槽位提交

### 5. 多 Agent 作用域隔离
- `tools/result` 事件由 `exec.agent` 键控，每个 Agent 的工具策略独立运行无交叉污染
- `tools/change` 事件不过滤：全局注册表变更对所有 Agent 可见（影响下一次提示词组装）

### 6. 扩展机制
| 目标 | 机制 |
|------|------|
| 添加面向模型的能力 | 在 `ctx.tools` 上注册；模式自动加入提示词组装 |
| 挂载管线监听器 | 使用 `ctx.on('tools/pre-execute', ...)` 等事件 |
| 测试工具 | 使用 `@deepseek-ai/dsh-tools/testing` 的 `defineContentToolFixture` |

---

核心源码在 [index.ts](packages/core/tools/src/index.ts) 中；类型与模式推导分别见 [schema.ts](packages/core/tools/src/schema.ts) 和 [ts-types.ts](packages/core/tools/src/ts-types.ts)。

想深入哪个方向？

[工具执行流水线](13-tool-execution-pipeline)
[添加工具与适配器](19-adding-tools-and-adapters)
[沙箱与审批策略](16-sandbox-and-approval-policy)