`ctx.subprocess` 是子进程能力接缝的 Cordis 服务入口。以下是其精确接口。

---

## 服务三元组

| 角色 | 包 | 职责 |
|------|-----|------|
| **服务定义** | `dsh-subprocess` | 声明 `ctx.subprocess` 键、`Subprocess` 抽象类、类型词汇表 |
| **服务提供者** | `dsh-subprocess-local` | 本地 `node:child_process` 实现 |
| **消费者** | `tool-bash`、`tool-pwsh` 等 | 通过 `ctx.subprocess` 消费，不导入具体实现 |

---

## `Subprocess` 抽象接口

核心方法：

| 方法 | 签名 | 用途 |
|------|------|------|
| `spawn()` | `(spec: SubprocessSpawnSpec) => SubprocessHandle` | 启动子进程，返回句柄 |

### `SubprocessSpawnSpec` — 生成规格（完全显式）

它**刻意不提供默认值**——每个处置、限制和目录都显式到达 `SubprocessSpawnSpec` 上，由调用者自身的配置（Shell 执行器的配置）决定它们。

| 字段 | 类型 | 用途 |
|------|------|------|
| `argv` | `string[]` | 可执行文件和参数；`argv[0]` 是程序 |
| `cwd` | `string` | 工作目录 |
| `env` | `Record<string, string>` | 环境变量（全量覆盖，非增量合并） |
| `stdin` | `'pipe' \| 'inherit' \| null` | 标准输入管道模式 |
| `stdout` | `'pipe' \| 'inherit' \| null` | 标准输出管道模式 |
| `stderr` | `'pipe' \| 'inherit' \| null` | 标准错误管道模式 |
| `signal` | `AbortSignal` | 中止信号 |
| `uid` | `number \| undefined` | POSIX 用户 ID |
| `gid` | `number \| undefined` | POSIX 组 ID |
| `detached` | `boolean` | 是否分离（脱离父进程组） |
| `windowsHide` | `boolean` | Windows 上隐藏子控制台窗口 |
| `resourceLimits` | `ResourceLimits \| undefined` | POSIX 资源限制（RLIMIT_*） |

> ⚠️ **文档漂移标注（2026-08-26）**：本表描述的扁平结构（`stdin/stdout/stderr: 'pipe'|'inherit'|null`）+ 句柄暴露 `exitCode`/`kill` 为**旧契约**。现有实现（`dsh-subprocess-local`）实际使用**嵌套 `stdio: {stdin:{data}, stdout:{maxBytes}, stderr:{maxBytes}}` + `graceMs`**，句柄经 `handle.done`/`handle.collected` 读取输出，见 `src/index.js` 与根目录 `坑与经验沉淀.md`。**以实际安装的 API 为准**；本表作为契约演进参考保留，变更时须回填。

### `SubprocessHandle` — 进程句柄

| 属性/方法 | 类型 | 用途 |
|-----------|------|------|
| `pid` | `number \| undefined` | 子进程 PID |
| `stdin` | `Writable \| null` | 标准输入流 |
| `stdout` | `Readable \| null` | 标准输出流 |
| `stderr` | `Readable \| null` | 标准错误流 |
| `exitCode` | `Promise<number \| null>` | 退出码（null 表示信号终止） |
| `signalCode` | `Promise<string \| null>` | 终止信号名 |
| `kill(signal?)` | `(signal?: string) => boolean` | 发送信号终止进程 |

#### 终止语义

- **POSIX**：向分离的进程组发送信号；当进程组消失时回退到直接子进程
- **Windows**：使用 `taskkill /T` 终止进程树

> 这确保了辅助进程不会在未察觉的情况下比句柄存活得更久。

---

## 与 `ctx.shell` 的关系

`ctx.subprocess` 是**底层原语**，`ctx.shell` 是**高层编排**：

```
ctx.shell (ShellExecutor)
    │
    │  resolve() — 填充实现拥有的默认值和上限
    │  生成 ShellExecSpec（完全指定）
    │
    ▼
ctx.subprocess.spawn(SubprocessSpawnSpec)
    │
    ▼
  SubprocessHandle
```

拆分设计保持了面向模型/插件的 API 简洁，同时赋予实现控制特定平台边界的能力。

---

## 沙箱交互

`ctx.subprocess` 本身**不施加沙箱约束**——沙箱由 `ctx.sandbox` 在更高层强制执行：

```
工具调用 → ctx.sandbox.confine(argv, policy) → ConfinedArgv
    │
    ▼
ctx.subprocess.spawn(confinedArgv) → SubprocessHandle
```

沙箱接缝在构造上**故障闭塞**：`ctx.sandbox.confine()` 要么返回带有强制执行 argv 的 `ConfinedArgv`，要么抛出 `SANDBOX_UNAVAILABLE`。

---

## 完整接缝架构

```
┌─────────────────────────────────────────────────────┐
│                   消费者工具层                       │
│   tool-bash / tool-pwsh / tool-lsp / tool-terminal  │
└──────────┬──────────────────────────┬───────────────┘
           │                          │
           ▼                          ▼
┌──────────────┐           ┌──────────────────┐
│  ctx.shell   │           │  ctx.sandbox     │
│  ShellExec   │──resolve──│  .confine()      │
└──────┬───────┘           └────────┬─────────┘
       │                            │
       ▼                            ▼
┌──────────────────────────────────────────┐
│           ctx.subprocess                  │
│           .spawn(SpawnSpec)              │
│           → SubprocessHandle              │
└──────────────────────────────────────────┘
       │
       ├── dsh-subprocess-local (node:child_process)
       └── [可替换的远程/沙箱实现]
```

---

[文件系统与子进程接缝](14-filesystem-and-subprocess-seams)
[沙箱与审批策略](16-sandbox-and-approval-policy)