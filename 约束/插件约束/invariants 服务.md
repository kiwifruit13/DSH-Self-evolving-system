`invariant.ts` 中的 `apply` 是每个包向 Cordis 不变式系统注册自身运行时断言的统一入口函数。

### 核心结构

每个 `invariant.ts` 都遵循相同的模式，导出三个成员：

| 导出 | 作用 |
|---|---|
| `name` | Cordis companion 插件名（如 `'agent-invariant'`） |
| `inject` | 声明依赖的服务（始终包含 `'invariants'`） |
| **`apply`** | **主入口** — 接收 Cordis `Context`，调用 `ctx.invariants.register(PACKAGE_NAME, install)`，返回一个 `Promise<() => void>`（即卸载/销毁函数） |

### `apply` 的签名与语义

```ts
export const apply = (ctx: Context): Promise<() => void> =>
  Promise.resolve(ctx.invariants.register(PACKAGE_NAME, install))
```

1. **输入**：`ctx: Context` — Cordis 上下文，携带 `invariants` 服务。
2. **核心调用**：`ctx.invariants.register(PACKAGE_NAME, install)` — 将当前包的 `install` 回调注册到不变式服务中，绑定到包名（如 `@deepseek-ai/dsh-agent`）。
3. **返回**：`Promise<() => void>` — 注册成功后返回一个 **disposer**（清理函数），调用它可撤销该不变式注册。

### `install` 回调（`InvariantInstaller`）

`install` 是真正执行断言逻辑的地方，签名大致为 `(ctx, fail) => void`：

- `ctx`：子 fiber 上的 Cordis 上下文
- `fail`：不变式失败回调，传入描述字符串即触发断言失败

各包的 `install` 逻辑各不相同：

- **agent** — 检测 `agent/status` 的重复状态切换（无操作转移）[invariant.ts](packages/core/agent/src/invariant.ts#L16-L22)
- **scope** — 检查作用域过滤事件是否携带正确的 scope carrier [invariant.ts](packages/core/scope/src/invariant.ts#L18-L36)
- **tools** — 校验工具执行流水线的单调性（pre → execute → post）、结果快照冻结、code-dispatch 的层级关系 [invariant.ts](packages/core/tools/src/invariant.ts#L35-L119)

### 一句话总结

`apply` 是 **"将包的运行时不变式断言注册到全局不变式服务"** 的标准化入口，所有 `invariant.ts` 都遵循同一签名 `(ctx) => Promise<disposer>`，通过 `ctx.invariants.register` 将包名与断言安装器绑定，在 Cordis 插件系统中被自动发现和加载。

---

想更深入理解不变式在整体架构中的角色，可以继续阅读：

[插件生命周期与副作用](8-plugin-lifecycle-and-effects)
[服务与依赖注入](9-services-and-dependency-injection)
[Agent 轮次与步骤生命周期](12-agent-turn-and-step-lifecycle)


`invariants` 服务是一个**可配置的包级运行时不变式注册表**，由 `@deepseek-ai/dsh-invariants` 包提供。它是整个 Harness 运行时断言体系的核心基础设施。

---

## 1. 本质：一个 Cordis Service

`InvariantRegistry` 继承自 `@deepseek-ai/cordis` 的 `Service`，挂载到上下文的 `ctx.invariants` 上：

```ts
export class InvariantRegistry extends Service { ... }

declare module '@deepseek-ai/cordis' {
  interface Context {
    invariants: InvariantRegistry  // 全局可访问
  }
}
```

参见 [index.ts](packages/runtime-diagnostics/invariants/src/index.ts#L122-L126)

---

## 2. 核心职责：`register()` 方法

这是整个服务的灵魂。签名：

```ts
register(packageName: string, installer: InvariantInstaller): () => void
```

它做的事，按顺序：

| 步骤 | 说明 |
|---|---|
| **校验包名** | 非空、无前后空白、无内嵌空格 |
| **唯一性预留** | 即使包被 filter 过滤掉，也占住坑位，防止重复注册 |
| **过滤判断** | 调用 `selected(packageName)` 决定是否真正安装 |
| **创建子 fiber** | 通过 `ctx.effect()` → `ctx.plugin()` 启动一个独立的子 fiber 来运行 `installer` |
| **绑定 `fail` 回调** | 将 `InvariantInstaller` 的 `fail` 参数绑定为 `(msg) => throw new InvariantError(packageName, msg)` |
| **返回 disposer** | 调用即销毁子 fiber + 释放包名占位，支持 HMR 重注册 |

参见 [index.ts](packages/runtime-diagnostics/invariants/src/index.ts#L148-L199)

---

## 3. `InvariantInstaller` — 安装器接口

```ts
export interface InvariantInstaller {
  (ctx: Context, fail: InvariantFailure): void | Promise<void>
  readonly inject?: Inject   // 声明子 fiber 需要的额外服务依赖
}
```

- **`ctx`**：子 fiber 拥有的上下文，独立于注册表主 fiber
- **`fail`**：`(message: string) => never` — 一旦调用就抛出 `InvariantError`，**永不返回**
- **`inject`**：可选的服务依赖声明，子 fiber 启动前确保这些服务已就绪

参见 [index.ts](packages/runtime-diagnostics/invariants/src/index.ts#L48-L58)

---

## 4. `InvariantError` — 失败归因

```ts
export class InvariantError extends Error {
  readonly code = 'INVARIANT'        // 机器可读的错误码
  readonly packageName: string       // 违反不变式的包名

  constructor(packageName: string, message: string) {
    super(`invariant violated by "${packageName}": ${message}`)
  }
}
```

错误信息自动携带包名前缀，一眼定位是哪个包的哪个契约被违反。

参见 [index.ts](packages/runtime-diagnostics/invariants/src/index.ts#L72-L86)

---

## 5. 配置与过滤

`Config` 接口提供三层控制：

```ts
interface Config {
  enabled?: boolean                   // 全局开关，默认 true
  package_allowlist?: string[]        // 正则白名单，空 = 全部放行
  package_blocklist?: string[]        // 正则黑名单，优先级高于白名单
}
```

过滤逻辑（`selected()` 方法）：

```
1. enabled === false → 全部跳过
2. 白名单非空 && 包名不匹配任何白名单正则 → 跳过
3. 包名匹配任一黑名单正则 → 跳过
4. 其余 → 放行
```

关键细节：**正则是大小写敏感的、无锚点的 JavaScript 正则源**。`package_allowlist: ['session']` 会匹配 `@deepseek-ai/dsh-session-extra`；要精确匹配需写 `^@deepseek-ai/dsh-session$`。

参见 [index.ts](packages/runtime-diagnostics/invariants/src/index.ts#L130-L145)

---

## 6. 生命周期保障

测试揭示了几个精妙的设计：

| 场景 | 行为 |
|---|---|
| **禁用但注册** | 包名仍被占位（不可重复注册），但 `installer` 不执行 |
| **安装失败** | 原子回滚：已注册的监听器被移除、包名占位被释放，允许重试 |
| **异步安装失败** | 同样原子回滚，`await` 之后的 `fail()` 也会导致回滚 |
| **disposer 调用** | 销毁子 fiber → 释放包名 → 允许同一包名重新注册（HMR） |
| **异步销毁中** | 占位不释放，直到异步销毁完成才允许重注册 |
| **服务 fiber 死亡** | 再调用 `register()` 抛出 inactive 错误 |

参见 [service.spec.ts](packages/runtime-diagnostics/invariants/tests/service.spec.ts)

---

## 7. 整体协作流

```
  各包的 invariant.ts               InvariantRegistry                 Cordis
  ──────────────────              ─────────────────               ──────────
  apply(ctx) ──────────────────→ ctx.invariants.register()
                                     │
                                     ├─ 校验 + 占位
                                     ├─ selected() 过滤
                                     ├─ ctx.effect() → ctx.plugin(installer)
                                     │        │
                                     │        └─ installer(childCtx, fail)
                                     │              │
                                     │              ├─ childCtx.on('event', ...)
                                     │              └─ fail('msg') → throw InvariantError
                                     │
                                     └─ 返回 disposer
```

每个包只需写一个 `install` 函数来监听事件、断言契约；`InvariantRegistry` 负责所有基础设施——过滤、隔离、归因、回滚、生命周期。

---

想进一步了解不变式如何与 Cordis 插件生命周期交织，或者看看各核心包具体断言了什么：

[插件生命周期与副作用](8-plugin-lifecycle-and-effects)
[服务与依赖注入](9-services-and-dependency-injection)
[工具执行流水线](13-tool-execution-pipeline)