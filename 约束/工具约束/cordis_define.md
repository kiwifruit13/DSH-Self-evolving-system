# Cordis 插件定义（`cordis_define`）关键信息全解

Cordis 中"定义一个插件"涉及三个层面：**插件元数据声明**、**纤程生命周期管理**、**工具定义辅助函数 `defineTool`**。以下逐一展开，直指源码。

---

## 一、插件形态与 `Plugin.Base` 元数据

`ctx.plugin()` 接受三种形态（[registry.ts#L91-L133](vendor/cordis/src/registry.ts#L91-L133)），它们共享 `Plugin.Base<T>`：

```ts
// registry.ts#L100-L111
interface Base<T = any> {
  name?: string                        // 显示名（日志/诊断用）
  Config?: StandardSchemaV1<any, T>    // 配置 schema（Standard Schema 接口）
  inject?: Inject                      // 依赖声明
  provide?: string | string[]          // 提供的服务名
  intercept?: Dict<boolean>            // 声明消费的拦截配置键
}
```

三种形态：

| 形态 | 签名 | 何时用 |
|------|------|--------|
| **函数** `Plugin.Function` | `(ctx: Context, config: T) => any` | 绝大多数插件：注册副作用 |
| **类** `Plugin.Constructor` | `new (ctx: Context, config: T) => any` | 需要在 `ctx` 上暴露命名服务 |
| **对象** `Plugin.Object` | `{ apply(ctx: Context, config: T): any }` | 需要与 `apply` 并存元数据 |

---

## 二、`ctx.plugin()` 核心流程

来自 [registry.ts#L316-L336](vendor/cordis/src/registry.ts#L316-L336)：

```ts
plugin(plugin: Plugin, config?: any): Fiber & PromiseLike<Fiber> {
  // 1. 解析插件形态 → 可执行回调
  const callback = this.resolve(plugin)
  if (!callback) throw new Error('invalid plugin...')

  // 2. 创建或复用 Runtime 记录（以 callback 为键）
  let runtime = this._internal.get(callback)
  if (!runtime) {
    runtime = { name: plugin.name, callback, fibers: new DisposableList(), Config: plugin.Config }
    this._internal.set(callback, runtime)
  }

  // 3. 构造 Fiber（附带 inject 映射、config、runtime）
  const fiber = new Fiber(this.ctx, config, Inject.resolve(plugin.inject), runtime, ...)

  // 4. 返回 thenable 纤程（await 等待加载完成）
  return wrapped
}
```

**四个步骤**：解析形态 → 创建/复用 Runtime → 构造 Fiber → 返回 thenable。

---

## 三、纤程状态机

来自 [fiber.ts#L147-L154](vendor/cordis/src/fiber.ts#L147-L154)：

```
PENDING ──(inject 满足 + Config 校验通过)──→ LOADING ──(apply 返回)──→ ACTIVE
  │                                            │
  │  inject 缺失                               │  apply 抛异常
  ↓                                            ↓
 停留 PENDING                                 FAILED

ACTIVE ──(dispose / 父纤程卸载)──→ UNLOADING ──(清理完成)──→ DISPOSED
```

| 状态 | 含义 | 插件体是否已运行 |
|------|------|-----------------|
| `PENDING` | 等待 inject 依赖可用 | 否 |
| `LOADING` | Config 已校验，`apply` 正在执行 | 正在执行 |
| `ACTIVE` | `apply` 正常返回，插件提供能力 | 是 |
| `FAILED` | Config 校验或 `apply` 失败 | 否/中断 |
| `UNLOADING` | 副作用清理中 | — |
| `DISPOSED` | 已移除，不可重启 | — |

---

## 四、Config 校验：Standard Schema

来自 [fiber.ts#L50-L62](vendor/cordis/src/fiber.ts#L50-L62)：

```ts
function resolveConfig(runtime: Plugin.Runtime, config: any) {
  if (!runtime.Config) return config                    // 无 schema → 原样透传
  const result = runtime.Config['~standard'].validate(config)  // Standard Schema 协议
  if (result.issues) throw new ValidationError(result.issues)  // 失败 → FAILED
  return result.value                                   // 成功 → 校验后值（含默认值）
}
```

关键点：
- Cordis 本身**不绑定**任何特定 schema 库，只依赖 [Standard Schema](https://standardschema.dev/) 接口（`~standard`）
- 本仓库使用 [Schemastery](https://github.com/shigma/schemastery) 作为实现
- 校验在 `PENDING → LOADING` 之前执行；失败抛 `ValidationError`，纤程进入 `FAILED`，`apply` **永不执行**
- `apply` 接收的 `config` **已校验且含默认值**

---

## 五、`inject` 依赖声明

来自 [registry.ts#L18-L19](vendor/cordis/src/registry.ts#L18-L19)：

```ts
type Inject<M = Dict> = (keyof M)[] | { [K in keyof M]?: M[K] }
```

两种形式，经 `Inject.resolve()` 归一化为 `{ 服务名 → 拦截配置 | null }`：

```ts
// 数组形式：仅声明依赖
export const inject = ['tools', 'llm']

// 对象形式：依赖 + 拦截配置
export const inject = { llm: { model: 'deepseek-v4-flash' }, tools: null }
```

**加载顺序由依赖图拓扑排序决定，与 `cordis.yml` 声明顺序无关。**

`ctx.inject()` 简写（[registry.ts#L300-L302](vendor/cordis/src/registry.ts#L300-L302)）：

```ts
inject(inject: Inject, callback: Plugin.Function<void>) {
  return this.plugin({ inject, apply: callback, name: callback.name })
}
```

适合短生命周期/响应式逻辑——依赖变更时自动重新运行回调，天然支持热重载。

---

## 六、`provide` 与服务暴露

- `provide: string | string[]`：声明此插件向 `ctx` 上暴露的服务名
- 类插件自动将类名注册为服务；`provide` 用于显式覆盖或声明额外服务
- 其他插件通过 `inject` 或 `ctx.<key>` 引用

---

## 七、副作用系统 `ctx.effect()`

来自 [fiber.ts#L83-L93](vendor/cordis/src/fiber.ts#L83-L93)：

```ts
type Effect<T = any> = SyncEffect<T> | AsyncEffect<T>
type SyncEffect<T>  = Disposable<T> | Iterable<Disposable<T>>
type AsyncEffect<T> = Promise<Disposable<T>> | AsyncIterable<Disposable<T>>
```

- 注册副作用时返回**清理函数**；纤程卸载时按**注册逆序**执行所有清理函数
- 事件监听器、服务提供者、子插件、工具注册本身都已是副作用
- 仅对 Cordis 未托管的资源使用 `ctx.effect()`

---

## 八、`defineTool` — 工具定义辅助函数

来自 [schema.ts#L482-L536](packages/core/tools/src/schema.ts#L482-L536)，完整签名：

```ts
interface DefineToolOptions<S extends ParameterSchemaSpec, O extends ValueSchemaSpec> {
  name: string                         // 工具名（全局唯一）
  description: string                  // 给模型的描述
  parameters: S                        // 参数 schema（隐式开放对象根）
  output: {
    schema: O                          // 输出值 schema
    render(args, value): ContentBlock[]// 面向模型的纯渲染
    presentationMeta?(args, value): JsonValue  // 可重放 UI 元数据
  }
  timeoutMs?: number                   // 协作超时（ms）
  isConcurrencySafe?(args): boolean    // 并发安全分类器
  execute(args, exec): Promise<InferValue<O>>  // 工具主体
  finalizeContent?(exec, result): ContentBlock[] | undefined  // 统一内容变换
  presentCall?(args): ToolCallView | undefined    // 调用中 UI 呈现
  presentResult?(args, result): ToolResultView | undefined  // 完成后 UI 呈现
}
```

### 参数 Schema DSL 子集

| 类型 | 允许约束 | 不支持 |
|------|---------|--------|
| `string`/`number`/`integer`/`boolean`/`null` | `enum`, `const` | `pattern`, `format`, `minLength`, `maxLength`, `minimum`, `maximum` |
| `array` | 可选 `items` | `minItems`, `maxItems` |
| `object` | **必须** `additionalProperties: boolean`，可选 `properties` | — |
| `json` | 无约束 | — |
| `oneOf` | 至少两分支，不能与 `type` 同声明 | `anyOf`, `allOf` |

所有节点共享注解：`description`, `title`, `default`, `examples`（仅注解，不参与校验）。

### 校验行为

| 阶段 | 失败 | 抛出 | 行为 |
|------|------|------|------|
| 定义时 | schema 含不支持关键字 | `JsonSchemaError` | 工具无法注册 |
| 调用时 | 模型参数违反 schema | `ToolArgsError`（`INVALID_ARGS`） | `execute` 不执行，错误进入模型上下文 |
| 呈现时 | 重放旧参数不兼容当前 schema | **不抛错** | 返回 `undefined`，退化为通用卡片 |

### UI 呈现卡片体系

**调用中** `ToolCallView`（[presentation.ts#L46](packages/core/tools/src/presentation.ts#L46)）：

| 卡片 | 用途 |
|------|------|
| `generic` | 默认：标题 + 图标 + 输入 |
| `terminal` | Shell 命令卡片 |
| `diff` | 文件修改内联 diff |

**完成后** `ToolResultView`（[presentation.ts#L140](packages/core/tools/src/presentation.ts#L140)）：

| 卡片 | 用途 |
|------|------|
| `generic` | 默认结果 |
| `terminal` | 终端输出 + 退出码 |
| `diff` | 已应用的文件变更 |
| `search` | 搜索结果（按文件分组 or 路径列表） |
| `read` | 行号 + 语法高亮代码视图 |
| `web` | 网页搜索/抓取 + 引用源 |

---

## 九、上下文作用域操作

来自 [context.ts#L99-L145](vendor/cordis/src/context.ts#L99-L145)：

| 方法 | 作用 |
|------|------|
| `ctx.extend(meta)` | 创建子上下文，添加额外元数据 |
| `ctx.isolate(name, label?)` | 为服务 `name` 创建隔离作用域 |
| `ctx.intercept(name, config)` | 为服务 `name` 注入拦截配置 |

这些方法**不修改父上下文**，返回子上下文用于局部覆盖。

---

## 十、`Plugin.Transform` 配置变换

来自 [registry.ts#L113-L118](vendor/cordis/src/registry.ts#L113-L118)：

```ts
interface Transform<S, T> {
  schema?: true
  Config: (config: S) => T  // 用户配置 S → 运行时配置 T
}
```

`ctx.plugin()` 先识别 Transform，对配置做变换后再传给实际插件。用于将用户友好的配置形态转换为内部运行时形态。

---

## 十一、完整插件定义示例

```ts
import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'

// ── 元数据 ──
export const name = 'greet-tool'
export const inject = ['tools']        // 依赖 tools 服务

// ── 配置 Schema ──
export interface Config {
  greeting: string
  targets: string[]
}
export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  targets: Schema.array(Schema.string()),
})

// ── 插件体 ──
export function apply(ctx: Context, config: Config) {
  // config 已校验，已填默认值
  ctx.tools.register(defineTool({
    name: 'greet',
    description: '向指定目标发送问候',
    parameters: {
      target: { type: 'string', description: '目标名称', required: true },
      lang:   { type: 'string', enum: ['en', 'zh'], description: '语言' },
    },
    output: {
      schema: { type: 'string' },
      render(_args, value) { return [{ type: 'text', text: value as string }] },
    },
    async execute(args) {
      return `${config.greeting}, ${args.target}!`
    },
  }))
}
```

---

**下一步建议：**

- [插件生命周期与副作用](8-plugin-lifecycle-and-effects) — 纤程状态机与 `ctx.effect()` 清理机制
- [服务与依赖注入](9-services-and-dependency-injection) — `inject` 解析、服务可用性谓词、`ctx.inject()` 简写
- [添加工具与适配器](19-adding-tools-and-adapters) — `defineTool` 呈现方式与更丰富的 schema