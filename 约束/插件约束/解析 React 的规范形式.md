客户端插件解析 React 的规范形式，核心是一套 **惰性 CJS 模块表 → 声明式槽注册 → uSES 绑定 → 逐条目缓存渲染** 的流水线。下面分层拆解。

---

## 一、模块到达：惰性工厂注册

客户端插件不直接执行模块体，而是通过 **闭包工厂** 注册到模块表：

```
window.__ModuleLoader__.load({id, factory})
```

- **执行脚本**仅注册 `factory`，不产生任何副作用（包括 CSS 注入）。
- **具象化**（`factory(require) → exports`）发生在首次 `import/require` 时，并缓存于 `loadCache`；递归具象化保证加载顺序无需外部编排。
- `require` 是注入的同步函数，从模块表中解析外部依赖——这是 Cordis 插件模块加载器的核心契约。

参见 [manifest.ts](packages/client/modules/src/client/manifest.ts#L155-L175) 中 `ClientBundleRegistration` 的定义，以及 [tsdown.client.ts](packages/client/tsdown.client.ts#L1-L31) 中对双产物构建的说明。

## 二、React 规范形式：声明式槽系统

插件不以命令式方式挂载 React 组件，而是通过 **声明式槽（Slot）** 注册，由运行时槽引擎选举和渲染。规范形式分为三层：

### 1. 槽注册（宿主侧，React 无关）

插件在 `apply()` 中调用 `ctx.slots.register()`，声明：
- 槽键（`key`）——渲染占位符的名称
- 组件——实际 React 组件
- `children`——子槽声明（`kind: 'single' | 'list' | 'chain'`）
- `inject`——标准工具注入工厂
- `store`——状态存储声明

这一层完全不含 React 导入，是 **框架无关的纯契约**。参见 [renderer.ts](packages/client/ui-slots/src/renderer.ts#L1-L30) 中 `SlotRendererHost` 的定义。

### 2. 槽渲染（客户端侧，React 消费）

[`ui-renderer`](packages/client/ui-renderer/src/client/index.ts) 插件在客户端侧执行：

```typescript
export function apply(ctx: Context): void {
  ctx.slots.install(createSlotRenderer())  // 安装 React 槽渲染器
  ctx.reflect.provide('uiRenderer', {
    mount: (container) => {
      const root = mountApp(container, buildRenderApp({ ctx }))
      return () => { root.unmount() }
    },
  })
}
```

关键步骤：
1. **安装渲染器**：`createSlotRenderer()` 将 `SlotRendererHost`（运行时 API）桥接到 React 渲染逻辑
2. **组装应用**：[app.tsx](packages/client/ui-renderer/src/client/app.tsx#L30-L41) 中 `buildRenderApp` 生成以 `root` 槽为顶点的整棵布局树
3. **挂载**：支持 SSR 水合（`hydrateRoot`）和纯客户端渲染（`createRoot`）两种模式

### 3. 逐条目绑定与缓存

[scoped-slots.tsx](packages/client/ui-renderer/src/client/scoped-slots.tsx#L44-L70) 实现了核心的规范形式：

- **`renderSlotCache`**（WeakMap）：每个 `StoredEntry` 获得身份稳定的 `renderSlot` 绑定，跨渲染不重建闭包
- **条目存活检查**：已 dispose 的注册调用 `renderSlot` 抛出 `StaleAuthorizationError`
- **子槽声明校验**：未声明的槽键抛出 `SlotOwnershipError`；`kind` 不匹配时提示使用 `renderSlotChain`
- **注入规范化**：`bindInjectHooks` 将 `hooks` 中的 `HostObservable` 源转换为 `use<Name>` 钩子，缓存于 WeakMap

## 三、状态绑定：uSES 桥接

[bind.ts](packages/client/ui-renderer/src/client/bind.ts#L1-L25) 是整个客户端栈中 **唯一的钩子构造器**：

```typescript
export function bindSnapshotSelector<T>(w: HostObservable<T>): SnapshotSelectorHook<T> {
  const subscribe = (fn) => w.subscribe(fn)
  const getSnapshot = () => w.getSnapshot()
  return function useSelector<S>(sel, eq?) {
    return useSyncExternalStoreWithSelector(subscribe, getSnapshot, undefined, sel, eq)
  }
}
```

规范形式的关键：**宿主侧永远产出裸 Observable（`getSnapshot`/`subscribe`），客户端侧统一通过 `bindSnapshotSelector` 桥接为 React 的 `useSyncExternalStore`**。这意味着：
- 引擎和宿主侧零 React 依赖
- 绑定仅在 React 侧发生一次
- `subscribe`/`getSnapshot` 闭包在首次绑定时捕获并永不变更，组件不会跨渲染重订阅

## 四、构建侧规范：双产物 + 纯度门

[tsdown.client.ts](packages/client/tsdown.client.ts#L55-L80) 的 `clientBundle()` 预设为每个 UI 插件包生成：

| 产物 | 运行时消费者 | 说明 |
|------|------------|------|
| Node 端库 | Cordis 宿主加载器 | 含 `apply()`、服务声明、类型 |
| 浏览器客户端包 | `window.__ModuleLoader__.load()` | 含 React 组件、CSS 注入 |

**纯度门**（`INLINE_SAFE` 正则）决定哪些 `@deepseek-ai/*` 依赖可内联（无跨插件运行时身份），其余必须作为模块表外部依赖——违反则在构建时报错。

---

总结：客户端插件解析 React 的规范形式 = **惰性工厂注册 → 声明式槽注册（React 无关） → 槽渲染器安装（React 消费） → uSES 桥接（唯一钩子构造点） → 逐条目缓存绑定**。宿主侧永远不知道 React 的存在，客户端侧通过唯一的 `bindSnapshotSelector` 桥接点将裸 Observable 转为 React 钩子——这就是"规范形式"的精确含义。

[宿主与客户端构建系统](22-host-and-client-build-system)
[插件生命周期与副作用](8-plugin-lifecycle-and-effects)
[类型化事件与分发模式](10-typed-events-and-dispatch-modes)


好的，以下是关于桥接点的完整展开。

---

## 桥接点的定义

**桥接点** 是整个客户端栈中**唯一**将宿主侧的裸 Observable 转换为 React 钩子的构造函数。它就是 [bind.ts](packages/client/ui-renderer/src/client/bind.ts#L1-L25) 中的 `bindSnapshotSelector`：

```typescript
export function bindSnapshotSelector<T>(w: HostObservable<T>): SnapshotSelectorHook<T> {
  const subscribe = (fn: () => void) => w.subscribe(fn)
  const getSnapshot = () => w.getSnapshot()
  return function useSelector<S>(sel: (s: T) => S, eq?: (a: S, b: S) => boolean): S {
    return useSyncExternalStoreWithSelector(subscribe, getSnapshot, undefined, sel, eq)
  }
}
```

只有这一个函数调用了 `useSyncExternalStoreWithSelector`。整个渲染器中没有第二个。

---

## 关键信息一：宿主侧永远产出裸 Observable

宿主侧（引擎、服务、宿主插件）只认识这个接口：

```typescript
interface HostObservable<T> {
  getSnapshot(): T
  subscribe(fn: () => void): () => void
}
```

这是**框架无关的纯契约**——零 React 导入。所有标准工具源（`sessions.list`、`workspaces.list`、`sessions.provideInfo`、store 实例、projection face）都是 `HostObservable`。宿主侧不构造钩子，不调用 `useState`，不依赖任何 React API。

参见 [renderer.ts](packages/client/ui-slots/src/renderer.ts#L31-L34) 中 `HostObservable` 的定义，以及 [store.ts](packages/client/ui-slots/src/store.ts#L21-L28) 中 `StoreInstance` 的契约——`getSnapshot`/`subscribe` 是引擎产品，React 钩子由渲染侧绑定。

---

## 关键信息二：桥接是一次性的，闭包永不变

`bindSnapshotSelector` 在首次调用时捕获 `subscribe` 和 `getSnapshot` 闭包，返回的 `useSelector` 函数内部**永远使用同一对闭包引用**。

这意味着：

| 场景 | 行为 |
|------|------|
| 组件重渲染 | `useSyncExternalStoreWithSelector` 对比 `subscribe` 引用 → 相同 → **不重订阅** |
| 源内部更新 | 源调用已注册的 `fn` → React 调度重渲染 → `getSnapshot` 返回新快照 |
| 插件卸载 | `StoredEntry` 从 ledger 移除 → 调用 `renderSlot` 时 `isLive(entry)` 为 false → 抛 `StaleAuthorizationError` |

这是 uSES 契约的精确利用：`subscribe` 引用稳定 = 零重订阅 churn。

---

## 关键信息三：所有钩子都经过缓存层 `observableHook`

[session-provider.tsx](packages/client/ui-renderer/src/client/session-provider.tsx#L55-L67) 中的 `observableHook` 是桥接点的缓存门面：

```typescript
export function observableHook<T>(source: HostObservable<T>): SnapshotSelectorHook<T> {
  let hook = hookCache.get(source)
  if (hook === undefined) {
    hook = bindSnapshotSelector(source)
    hookCache.set(source, hook)
  }
  return hook as SnapshotSelectorHook<T>
}
const hookCache = new WeakMap<object, unknown>()
```

**每个源只桥接一次**，缓存在 `WeakMap<object, unknown>` 中，键为源对象身份。后续所有对同一源的 `observableHook` 调用直接返回已缓存的钩子。

为什么用 WeakMap：源是宿主拥有的单例（如 `host.sessions.list`），生命周期由宿主管理；源被回收时缓存自动释放，不会泄漏已卸载插件的数据。

---

## 关键信息四：四类标准工具的桥接路径

### 1. 全局源（`useSessions`、`useWorkspaces`）

[scoped-slots.tsx](packages/client/ui-renderer/src/client/scoped-slots.tsx) 中 `standardProps` 函数：

```typescript
cache = {
  root: {
    useSessions: observableHook(host.sessions.list),
    useWorkspaces: observableHook(host.workspaces.list),
  },
  // ...
}
```

直接桥接，缓存于 `standardPropsCache`（WeakMap，键为 `host`），全局只建一次。

### 2. 会话源（`useSession`、其他 `hooks` 条目）

```typescript
for (const [name, source] of Object.entries(info.hooks)) {
  const hookName = `use${name[0]?.toUpperCase() ?? ''}${name.slice(1)}`
  standard[hookName] = observableHook(source)  // 或 maybeObservableHook(source)
}
```

- **严格会话**（`scope === 'session'`）：`observableHook(source)`，源必定存在
- **可选会话**（`scope === 'session-maybe'`）：`maybeObservableHook(source)`，源可能为 `undefined`

### 3. 可选会话源的桥接（`maybeObservableHook`）

[session-provider.tsx](packages/client/ui-renderer/src/client/session-provider.tsx#L70-L82)：

```typescript
const absentSource: HostObservable<undefined> = {
  getSnapshot: () => undefined,
  subscribe: () => () => {},
}

export function maybeObservableHook<T>(source: HostObservable<T> | undefined): MaybeSnapshotSelectorHook<T> {
  if (source !== undefined) return observableHook(source)
  return useAbsentSnapshot
}

function useAbsentSnapshot<S>(_selector, _equal?): S | undefined {
  observableHook(absentSource)(() => undefined)  // 保持 uSES 调用计数恒定
  return undefined
}
```

关键设计：**当源缺席时，仍调用一次 uSES（绑定到 `absentSource`），保证钩子调用顺序不变**。React 的 hooks 规则要求同一组件每次渲染的钩子调用数量一致，`maybeObservableHook` 通过"虚拟订阅"满足此约束。

### 4. 投影源（`useProjection`）

[session-provider.tsx](packages/client/ui-renderer/src/client/session-provider.tsx#L84-L113)：

```typescript
export function projectionHook(info: SessionMaybeProvideInfo): (...) => unknown {
  let hook = projectionHookCache.get(info)
  if (hook === undefined) {
    hook = (key, selector, eq) => {
      const useValue = observableHook(info.projections?.faceOf(key) ?? absentSource)
      return useValue(selector ?? (value => value), eq)
    }
    projectionHookCache.set(info, hook)
  }
  return hook
}
```

投影是**键址**的——每个 `key` 对应一个独立的 `HostObservable`，通过 `info.projections.faceOf(key)` 解析。同一 `info` 下整个 `projectionHook` 函数只创建一次（缓存于 WeakMap），但内部每次调用不同 `key` 会走 `observableHook` 获取不同源的钩子。

---

## 关键信息五：存储源（`useStore`）的桥接

[scoped-slots.tsx](packages/client/ui-renderer/src/client/scoped-slots.tsx) 中 `standardKit` 函数：

```typescript
const store = host.storeOf(entry, info?.sessionId)
if (store !== undefined) {
  kit['useStore'] = observableHook(store)  // store 实例本身就是 HostObservable
  kit['actions'] = store.actions
}
```

`StoreInstance` 同时实现 `HostObservable`（`getSnapshot`/`subscribe`）和 `actions`（烘焙后的回调）。`useStore` 通过同一个 `observableHook` 缓存路径桥接，实例身份（WeakMap 键）保证每个 store 只绑一次。

---

## 关键信息六：Locale 源的特殊订阅

Locale 不走 `bindSnapshotSelector`，而是直接用 `useSyncExternalStore` 订阅**修订版本号**，再从版本号派生 `t` 函数：

[scoped-slots.tsx](packages/client/ui-renderer/src/client/scoped-slots.tsx) 中 `useLocaleRevision`：

```typescript
function useLocaleRevision(face: LocaleFace | undefined): number {
  const subscription = face !== undefined ? localeSubscription(face) : undefined
  return useSyncExternalStore(
    subscription?.subscribe ?? noopSubscribe,
    subscription?.getRevision ?? zeroRevision,
  )
}
```

- **订阅**缓存在 `localeSubscriptionCache`（WeakMap，键为 `face`）——避免每个 outlet 各建闭包导致 uSES 重订阅
- **`t` 函数**缓存在 `localeSeatCache`，以 `(face, namespace, revision)` 为键——revision 变化时产出新函数引用，`React.memo` 组件通过浅比较感知变化而重渲染
- face 未安装时回退到 `noopSubscribe`/`zeroRevision`——**钩子数量恒定，不随 locale 安装状态变化**

---

## 关键信息七：Session 上下文的桥接

[session-provider.tsx](packages/client/ui-renderer/src/client/session-provider.tsx#L137-L161) 中 `SessionProvider`：

```typescript
export function SessionProvider({ empty, children }: SessionProviderProps) {
  const host = useHost()
  const info = observableHook(host.sessions.provideInfo)(s => s)  // ← 桥接！
  const id = info.sessionId
  if (id === undefined) return <>{empty?.() ?? null}</>
  return (
    <BindingContext.Provider value={info} key={id}>
      {children(id)}
    </BindingContext.Provider>
  )
}
```

`host.sessions.provideInfo` 是一个 `HostObservable<SessionMaybeProvideInfo>`——它同时承载了会话选择变更和 provider 名单变更。通过 `observableHook` 桥接后，一个 uSES 订阅就覆盖了两种变更场景。`key={id}` 保证会话切换时子树完整重挂载。

---

## 关键信息八：错误边界与桥接安全

桥接点产出的钩子在 `SlotErrorBoundary` 内执行。关键规则：

- **`SlotAssemblyError`**（组装错误：缺 provider、缺 locale）：**重新抛出**，不捕获——组装错误必须 fail-loud
- **条目错误**（组件崩溃、inject 工厂异常）：**捕获并报告**，通过 `host.reportEntryError` 通知 ledger
  - 遮蔽类（`single`/`keyed`/`list`）：条目退位（abdicate），outlet 渲染下一个幸存者
  - 链类（`chain`）：仅报告不退位——选举结果静态固定

---

## 总结：桥接点的全貌

| 层次 | 角色 | React 依赖 |
|------|------|-----------|
| 宿主侧 | 产出 `HostObservable`（`getSnapshot`/`subscribe`） | 无 |
| `bindSnapshotSelector` | **唯一桥接函数**：`HostObservable → useSyncExternalStore` | 唯一调用点 |
| `observableHook` | 缓存门面：同一源只桥接一次 | 无（返回钩子函数） |
| `maybeObservableHook` | 可选源：缺席时虚拟订阅保持钩子顺序 | 无 |
| `projectionHook` | 键址投影：每 key 独立源，统一缓存 | 无 |
| 组件侧 | 调用 `useXxx(sel, eq?)` 选择快照切片 | 是（uSES 消费者） |

**核心不变量**：宿主侧零 React 依赖 + 客户端侧唯一桥接点 + 每源只桥接一次 + 钩子调用顺序恒定。违反任何一条都会在运行时产生订阅泄漏、重渲染风暴或 hooks 规则违规。

[宿主与客户端构建系统](22-host-and-client-build-system)
[插件生命周期与副作用](8-plugin-lifecycle-and-effects)
[服务与依赖注入](9-services-and-dependency-injection)