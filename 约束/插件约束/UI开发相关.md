## UI 开发关键信息全览

按从架构到细节的顺序，共七大领域。

---

### 一、双面架构：Host / Client 分离

系统被划分为通过传输无关的 API 网关通信的两面：

| 面 | 运行环境 | 内容 |
|---|---|---|
| **Host（宿主）** | Node.js | Agent 运行时、LLM 适配器、工具执行器、文件系统接缝、API 网关、Web 服务器 |
| **Client（客户端）** | 浏览器 | React UI、插件模块加载器、RPC 连接、HMR 桥接 |

**Host 层级包含**：

| 包 | 关键服务 |
|---|---|
| `host/apiproxy` | API 代理（传输无关） |
| `host/webserver` | HTTP Web 服务器 |
| `host/frontend-static` | 静态前端服务 |
| `host/directory-picker*` | 目录选择器 |

**Client 包**（`packages/client/*`）提供浏览器 UI 和运行时：React 组件套件（`ui-*`）、RPC 连接、HMR 桥接、模块加载器、区域设置数据以及 Web 应用入口点。

---

### 二、UI 模块体系

客户端侧有一组丰富的 UI 模块驱动 Agent 句柄并从 `session/event` 流进行渲染：

| 模块 | 职责 |
|---|---|
| `ui-conversation` | 对话流渲染 |
| `ui-renderer` | 通用内容渲染器 |
| `ui-tool` | 工具调用/结果卡片渲染 |
| `ui-settings` | 设置界面 |
| `ui-workflow-run` | 工作流运行节点（从会话事件折叠） |
| `client/hmr` | 热模块替换——不完全重载下的实时插件更新 |

**核心数据流**：`session/event` 流 → UI 模块 → React 组件渲染

---

### 三、工具卡片表现层——工具作者的 UI 接缝

这是工具插件作者最直接接触的 UI 编程接口。

#### 两阶段卡片

| 阶段 | 函数 | 触发时机 | 约束 |
|---|---|---|---|
| 待定状态 | `presentCall(args) → ToolCallView` | Stage 1（tool/call 记录后） | 纯函数，无副作用，重放安全 |
| 完成状态 | `presentResult(args, result) → ToolResultView` | Stage 7（tool/result 记录后） | 纯函数，无副作用，重放安全 |

#### 待定状态卡片（`presentCall`）

| 卡片种类 | 形态 | 使用时机 |
|---|---|---|
| `generic` | `{ card: 'generic', title, kind?, rawInput?, content? }` | 通用操作 |
| `terminal` | `{ card: 'terminal', command }` | Shell 命令启动 |
| `diff` | `{ card: 'diff', path, ... }` | 文件编辑操作 |

#### 完成状态卡片（`presentResult`）

| 卡片种类 | 关键字段 | 使用时机 |
|---|---|---|
| `generic` | 可选的 title 与 content | 简单文本结果 |
| `terminal` | 原始输出 + 可选退出元数据 | Shell 命令完成 |
| `diff` | 文件路径 + 差异块 | 文件写入/编辑完成 |
| `search` / `searchMatches` / `searchPaths` | 匹配结果 | 搜索完成 |
| `read` | 文件内容 | 文件读取完成 |
| `web` / `webSearch` / `webFetch` | Web 内容/来源 | Web 操作完成 |

#### 表现层关键契约

1. **纯函数**——`presentCall` 和 `presentResult` 必须**仅**依赖于 `args`（+ 结果），无 I/O、不读取会话状态、不依赖时钟/随机数
2. **同一函数在实时流和会话日志重放时运行**——重放安全性要求
3. **仅用于 UI 的格式化不应混入模型结果**——围栏 ` ```console ` 块、差异对比、相对化路径不属于规范值
4. `output.render` 掌管面向模型的文本；`presentationMeta` + 卡片表现器掌管可重放的 UI 元数据
5. **显示绝不能导致重放崩溃**——`defineTool` 软校验：畸形或较旧的已记录参数使包装器返回 `undefined`（通用回退）而非抛异常

#### presentationMeta — 持久卡片数据

```typescript
// 变更工具（写入/编辑）需要 presentationMeta
// 核心层将投影后的 JSON 持久化于 tool/result 并传递给 presentResult
// 因此需要结果时事实的差异卡片可通过重放携带它们
defineTool({
  name: 'edit_file',
  // ...
  presentationMeta: (args, result) => ({
    diffBefore: result.before,
    diffAfter: result.after,
  }),
})
```

---

### 四、管线中的 UI 渲染时机

工具执行管线的 UI 可见阶段：

```
Stage 1: tool/call 记录
    │
    ├──→ UI: presentCall(args)            ← 待定状态卡片
    │
    ▼
Stage 2-6: 守卫 → pre-execute → execute → 主体 → post-execute
    │
    ▼
Stage 7: 规范化 + deepFreeze + 日志追加
    │
    ├──→ UI: presentResult(args, result)  ← 完成状态卡片
    │
    └──→ session.append('tool/result', ...)
         ctx.emit('tools/result', exec, frozenResult)
```

**两阶段分离**意味着管线可以验证、快照和重新渲染，**而无需重新执行工具主体**。

---

### 五、客户端构建系统

#### clientBundle — 双产物工厂

```typescript
clientBundle(id, libEntry, options?)
```

生成**双产物**：
1. **Node 端库**——由 Cordis 宿主加载器在运行时消费
2. **浏览器端客户端包**——调用 `window.__ModuleLoader__.load({id, factory})`

**无需 import 映射、无需全局变量、无需打包器运行时**——工厂闭包即为完整的插件载荷。

#### 客户端包预设

`packages/client/tsdown.client.ts` 中的共享预设是所有 UI 插件包的核心构建装备。

`hostPhase` 选项控制 Node 端库的输出阶段：
- `host`——需要被宿主反射的包
- `client`（默认）——标准客户端包

入口切换：
- **开发环境**：`src/client/index.ts`（解析工作区源码）
- **生产环境**：`lib/types/client/index.js`（消费输出的 JavaScript）

#### 构建顺序约束

**宿主构建必须在客户端类型检查之前完成**——客户端包通过共享叶子引用依赖于宿主端发出的 `.d.ts` 文件。跳过宿主构建将导致过时或缺失的声明，引发类型错误。

#### CSS 处理

- `.module.css` → `lightningcss` 内联编译 → 哈希类映射 → 工厂执行时注入带标记的 `<style>` 元素
- `.css?inline` → 导出编译后的文本，供插件拥有的生命周期效应使用

#### 独立守卫

`rejectStandaloneServe()` 插件在直接调用 `vite dev` 或 `vite preview` 时抛出错误——外壳需要宿主进程注入的 `window.__DSH_BOOT__`，裸 Vite 服务提供不可用页面。正确入口点是 `pnpm dsh web` 或 `pnpm run dev:web`。

---

### 六、会话事件驱动的 UI 渲染

UI 从 `session/event` 流驱动渲染。关键事件类型：

| 事件 | UI 用途 |
|---|---|
| `tool/call` | 配对 `callId` 渲染待定卡片 |
| `tool/result` | 配对 `callId` 渲染完成卡片 |
| `tool/code-dispatch-start` | Code Mode 子调度启动（实时运行状态） |
| `tool/code-dispatch` | Code Mode 子调度完成（按 `subCallId` 配对） |
| `turn/start` / `turn/end` | 轮次边界 |
| `step/start` / `step/end` | 步骤边界 |
| `user/message` | 用户消息渲染 |
| `assistant/message` | 助手消息渲染 |

**UI 或编辑器集成**的扩展模式：驱动 `ctx.agents` 并从 `session/event` 渲染。

---

### 七、HMR 与运行时更新

| 机制 | 作用域 | 需要重启？ |
|---|---|---|
| `client/hmr` | 浏览器端插件模块热替换 | 否 |
| `cordis.patch.yml` 监视 | Profile 用户补丁层 | 否（长生命周期界面通过 `watchUserPatches` 实时生效） |
| Bundle/Profile 结构变更 | 进程级插件树 | 是（需要重启） |
| Settings 变更 | 运行时 | 否（即时生效） |

---

若要沿着 UI 开发的上下游继续：

[宿主与客户端构建系统](22-host-and-client-build-system) — 双面架构的完整构建配置
[添加工具与适配器](19-adding-tools-and-adapters) — presentCall/presentResult 的详细用法与契约
[会话日志与事件溯源](11-session-log-and-event-sourcing) — UI 渲染的数据源


## 宿主与客户端构建系统：双面架构的完整构建配置

DeepSeek Harness 采用**双面架构**构建：运行于 Node.js 的 *Host* 面与运行于浏览器的 *Client* 面。构建系统从 TypeScript 项目引用层级一直到打包器输出布局，严格执行这种分离，最终生成两个**完全隔离的类型检查聚合体**，它们仅共享声明产物。

---

### 一、两面职责划分

| 面 | 运行环境 | 包位置 | 内容 |
|---|---|---|---|
| **Host** | Node.js | `packages/host/*`, `packages/core/*`, `packages/llm/*`, `packages/tools/*` 等 | Agent 运行时、LLM 适配器、工具执行器、文件系统接缝、API 网关、Web 服务器 |
| **Client** | 浏览器 | `packages/client/*` | React UI 组件套件（`ui-*`）、RPC 连接、HMR 桥接、模块加载器、区域设置数据、Web 应用入口点 |
| **SDK（共享）** | 双面 | `packages/sdk/*` | 传输无关的协议、类型化客户端与服务端 |

#### Host 层级包

| 包 | 关键服务 |
|---|---|
| `host/apiproxy` | API 代理（传输无关的网关） |
| `host/webserver` | HTTP Web 服务器 |
| `host/frontend-static` | 静态前端服务、插件清单 |
| `host/directory-picker*` | 目录选择器实现 |
| `host/plugin-inventory` | 插件清单与发现 |

#### Client 层级包

| 包 | 关键服务 |
|---|---|
| `client/connection` | WebSocket 下行链路（双面拆分） |
| `client/ui-*` | React 组件套件（conversation、renderer、tool、settings 等） |
| `client/hmr` | 热模块替换桥接 |
| `client/loader` | 插件模块加载器 |

---

### 二、双面拆分包

多个包同时包含宿主端和客户端源码，因此携带**两个包级 tsconfig 文件**而非一个：

| 包 | Host tsconfig | Client tsconfig | 拆分内容 |
|---|---|---|---|
| `packages/client/connection` | `tsconfig.host.json` | `tsconfig.client.json` | WebSocket 下行链路 |
| `packages/api/remotes` | `tsconfig.host.json` | `tsconfig.client.json` | 远程适配器服务与远程类型契约 |

**测试分区**：Host 聚合体的 `exclude` 块过滤掉 `**/*.client.spec.ts`；Client 聚合体的 `exclude` 则移除 `**/*.host.spec.ts`。后缀即可完成路由，无需逐文件配置。

**共享叶子包**（session、llm、tools 等）具有单一的 tsconfig，两个聚合体通过各自的项目引用链对其进行引用。

---

### 三、完整构建流水线

`package.json` 中的完整构建管线：

```
build:lib:host    →  tsc -b tsconfig.host.json    +  tsdown --env.DSH_BUILD_FACE=host
build:lib:client  →  tsc -b tsconfig.client.json  +  tsdown --env.DSH_BUILD_FACE=client
```

#### Host 面构建

```
┌─ Host Face ─────────────────────────────────────────────┐
│                                                         │
│  tsc -b tsconfig.host.json                              │
│    → 生成 .d.ts 声明文件（供 Client 面消费）             │
│                                                         │
│  tsdown --env.DSH_BUILD_FACE=host                       │
│    → Typert 插件运行：从宿主端类型声明生成                │
│      类型化 RPC 描述符和编解码贡献                        │
│    → 输出 lib/types/{index,invariant,startup}.js 入口    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Typert 插件**是 Host 面独有的：它从宿主端类型声明自动生成类型化的 RPC 描述符和编解码器，使 API 契约由类型系统驱动而非手写。

#### Client 面构建

```
┌─ Client Face ───────────────────────────────────────────┐
│                                                         │
│  tsc -b tsconfig.client.json                            │
│    → 类型检查（消费 Host 面发出的 .d.ts）                │
│                                                         │
│  tsdown --env.DSH_BUILD_FACE=client                     │
│    → Typert 禁用（直接消费已生成的宿主端产物）            │
│    → 入口切换：                                          │
│      dev  → src/client/index.ts（工作区源码）             │
│      prod → lib/types/client/index.js（输出的 JS）        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### 构建顺序约束

**宿主构建必须在客户端类型检查开始前完成**：

```
typecheck  =  build:lib:host  →  tsc -b tsconfig.client.json
```

这不是性能优化——客户端包通过共享叶子引用依赖于宿主端发出的 `.d.ts` 文件。跳过宿主构建将导致过时或缺失的声明，在客户端聚合体中引发类型错误。

---

### 四、clientBundle — 双产物工厂

```typescript
clientBundle(id, libEntry, options?)
```

生成**双产物**：

1. **Node 端库**——由 Cordis 宿主加载器在运行时消费
2. **浏览器端客户端包**——调用 `window.__ModuleLoader__.load({id, factory})`

**Cordis 插件模块加载器**接收该工厂函数，外部依赖通过指向加载器模块表的注入 `require` 进行解析。这意味着：
- **无需 import 映射**
- **无需全局变量**
- **无需打包器运行时**
- 工厂闭包即为完整的插件载荷

#### hostPhase 选项

```typescript
clientBundle(id, libEntry, { hostPhase: 'host' | 'client' })
```

| `hostPhase` | 用途 |
|---|---|
| `'host'` | 需要被宿主反射的包——Node 端库在宿主阶段输出 |
| `'client'`（默认） | 标准客户端包——Node 端库在客户端阶段输出 |

---

### 五、客户端包预设

`packages/client/tsdown.client.ts` 中的共享预设是所有 UI 插件包的核心构建装备，确保统一的输出格式、外部化规则和插件载荷结构。

---

### 六、CSS 管线

CSS 由 `lightningcss` 内联编译，使用虚拟 loader ID 与 tsdown 自身的 `@tsdown/css` 处理分离：

| 虚拟 Loader ID | 用途 |
|---|---|
| `\0dsh-css:` | 标准 CSS 模块 |
| `\0dsh-global-css:` | 全局 CSS |
| `\0dsh-inline-css:` | 内联 CSS |

**输出形式**：
- `.module.css` → 哈希类映射 → 工厂执行时注入带标记的 `<style>` 元素
- `.css?inline` → 导出编译后的文本字符串，供插件拥有的生命周期效应使用

---

### 七、Vite 分块布局

Web 应用的 `vite.config.ts` 强制执行几项结构约束：

**Rollup `manualChunks`** 将输出拆分为两个分块：

| 分块 | 内容 | 更新频率 |
|---|---|---|
| `index` | 工作区代码、React、经 vendor 处理的 cordis 加载器 | 高（随应用代码变动） |
| `vendor` | 体量大且极少变动的 npm 包：KaTeX、Shiki、micromark | 低（仅在依赖升级时） |

**框架包解析**：经 vendor 处理的框架包解析至工作区源码而非已发布的 npm 版本——开发时直接引用 monorepo 源码。

---

### 八、闭包隔离门控

该门控确保两个动态插件包**绝不会**在携带运行时身份的工作区包上共享闭包——**模块表是唯一的共享机制**。这防止了两个独立加载的插件实例意外共享状态。

---

### 九、独立守卫

`rejectStandaloneServe()` 插件在直接调用 `vite dev` 或 `vite preview` 时抛出错误：

```
✘ 外壳需要由宿主进程注入的 window.__DSH_BOOT__
✘ 裸 Vite 服务提供的是不可用页面
```

正确入口点：

| 环境 | 命令 |
|---|---|
| 生产 | `pnpm dsh web` |
| 开发 | `pnpm run dev:web` |

---

### 十、构建环境变量

| 变量 | 用途 | 设定方式 |
|---|---|---|
| `DSH_BUILD_FACE` | `host` 或 `client`——控制 tsdown 输出面 | 构建管线自动设置 |
| `DSH_BUILD_CLIENT_PROFILE` | 选择官方配置档案 | Profile 参数或环境变量 |
| `DSH_CLIENT_TITLE` | Web 外壳中的 HTML `<title>` | 环境变量；官方档案默认 `"DeepSeek Harness"` |

`clientBuildEnvironmentDefines()` 为 Vite/tsdown 生成 `define` 映射，在构建时将环境变量内联为常量。

---

### 十一、完整构建图

```
┌─ Monorepo ─────────────────────────────────────────────────────┐
│                                                                 │
│  ┌─ Shared Leaves ──────────────────────────────────────────┐  │
│  │  session / llm / tools / agent / scope / ...             │  │
│  │  单一 tsconfig，双聚合体通过项目引用链引用               │  │
│  └──────────────────────────────────────────────────────────┘  │
│        │                         │                              │
│        ▼                         ▼                              │
│  ┌─ Host Aggregate ──┐   ┌─ Client Aggregate ──────────────┐  │
│  │ tsconfig.host.json │   │ tsconfig.client.json            │  │
│  │                    │   │                                  │  │
│  │ 1. tsc → .d.ts     │──▶│ 2. tsc（消费 Host .d.ts）       │  │
│  │ 2. tsdown + Typert │   │ 3. tsdown（Typert 禁用）        │  │
│  │    → RPC 描述符    │   │    → 浏览器包                   │  │
│  │    → Node 端库     │   │    → CSS 内联                   │  │
│  └────────────────────┘   └──────────────────────────────────┘  │
│        │                         │                              │
│        ▼                         ▼                              │
│  ┌─ Runtime ────────────────────────────────────────────────┐  │
│  │  Host: Node.js 进程（Agent 运行时）                      │  │
│  │    ↕ 传输无关 API 网关（RPC）                            │  │
│  │  Client: 浏览器（React UI + 模块加载器 + HMR）           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

若要继续深入双面架构的上下游：

[添加工具与适配器](19-adding-tools-and-adapters) — 工具/适配器在双面架构中如何注册与表现
[包与配置档案](17-bundles-and-profiles) — Bundle/Profile/Preset 如何组合 Host 与 Client 包为可运行应用