**Schemastery** 是一个类型驱动的 Schema 校验库，在本项目中用于插件配置声明、校验和 UI 渲染。它同时满足两件事：给 TypeScript 编译器提供类型，给 Cordis 运行时提供校验器。

---

## 核心用法速查

### 1. 基本类型断言

```ts
import Schema from 'schemastery'  // 本项目: import Schema from '@deepseek-ai/schemastery'

Schema.string()       // 字符串
Schema.number()       // 数字
Schema.boolean()      // 布尔
Schema.any()          // 任意类型
Schema.never()        // 不可接受任何值
Schema.const(10)      // 常量，只接受 10
```

### 2. 复合类型

```ts
// 数组 —— 内部元素必须是 number
Schema.array(Schema.number())           // 默认值 []

// 字典 —— 值必须是 string
Schema.dict(Schema.string())            // 默认值 {}

// 元组 —— 固定长度，每项类型不同
Schema.tuple([Schema.number(), Schema.string()])

// 对象 —— 最常用，声明配置结构
Schema.object({
  greeting: Schema.string().default('Hello'),
  port: Schema.number().default(8080),
})
```

### 3. 高级类型

```ts
// 联合类型 —— 值为多种类型之一
Schema.union([Schema.number(), Schema.string()])

// 枚举快捷写法
Schema.union(['red', 'blue', 'green'])  // 等价于 Schema.union([Schema.const('red'), ...])

// 交叉类型 —— 值须同时满足多个 schema
Schema.intersect([
  Schema.object({ a: Schema.string().required() }),
  Schema.object({ b: Schema.number().default(0) }),
])

// 变换 —— 校验后对值做转换
Schema.transform(Schema.number(), n => n + 1)   // 输入 10 → 输出 11
```

### 4. 修饰符链

```ts
Schema.string().required()        // 必填，不可为 undefined
Schema.string().default('foo')    // 缺省时使用 'foo'
Schema.string().description('昵称') // UI 显示的描述文字
// required 和 default 互斥，不可同时使用
```

### 5. 校验与简化

```ts
const Config = Schema.object({
  foo: Schema.string().default(''),
  bar: Schema.number().default(0),
})

// 校验 — 不合法会抛 TypeError
Config({ foo: 'hi', bar: 1 })       // { foo: 'hi', bar: 1 }

// 简化 — 移除与默认值相同的字段，持久化时节省空间
Config.simplify({ foo: '', bar: 1 }) // { bar: 1 }

// autofix 模式 — 移除无效属性而非报错
Config({ foo: 'hi', bar: 'bad' }, { autofix: true })
```

---

## 在本项目中的用法

本项目导出 `Config` 时，**TypeScript 接口和运行时 Schema 同名**，Cordis 自动用 Schema 校验后再传给 `apply`：

```ts
import type { Context } from '@deepseek-ai/cordis'
import Schema from '@deepseek-ai/schemastery'

export const name = 'config-demo'

export interface Config {
  greeting: string
  targets: string[]
}

export const Config = Schema.object({
  greeting: Schema.string().default('Hello'),
  targets: Schema.array(Schema.string()).default([]),
})

export function apply(ctx: Context, config: Config) {
  for (const target of config.targets) {
    ctx.logger.info(`${config.greeting}, ${target}!`)
  }
}
```

配置目录系统也依赖 Schemastery：注册设置时绑定 Schema，分层合并后做校验，脱敏时按 Schema 结构遍历机密字段。

详见 [Cordis 插件教程](3-cordis-plugin-tutorial) 和 [配置目录](18-configuration-catalog)。

---

## 扩展自定义类型

```ts
Schema.extend('trimmed', (data, schema, options) => {
  if (typeof data !== 'string') {
    throw new Schema.ValidationError(`expected string but got ${data}`, options)
  }
  return [data.trim()]
})
```

---

想进一步了解？去这几页看看：

[Cordis 插件教程](3-cordis-plugin-tutorial)
[配置目录](18-configuration-catalog)
[添加工具与适配器](19-adding-tools-and-adapters)