# `@kiwifruit/dsh-self-evolving-contract`

跨进程（TypeScript ↔ Python）契约的**单一真源**。

---

## 一、定位：这是 contract，不是 seam

依据 `约束/001接口/接缝设计模式.md` 规则 10：

> **至少两个提供者才值得做成接缝，单实现用普通 Service 即可。**

当前只有 Python 一个后端。**所以本包不是接缝，是契约包。**

| | 说明 |
|---|---|
| 合法性来源 | 契约曾散落在 **6 处手工同步**，而非「后端可替换」 |
| 不是什么 | 不是运行时服务，不含抽象 Service 类（ADR-4） |
| 升级路径 | 第二个后端（TS 原生 / HTTP 远程）出现时，补一个抽象 Service 子类即可平滑升级为真接缝，消费方零改动 |
| 保留的部分 | 伴生事件词汇表 —— 纯声明、零成本，是「人类锁定根分类」的天然挂载点 |

---

## 二、为什么存在

改造前，9 类跨进程契约**全部靠注释里的「保持一致」维系，0 类有守卫**：

| # | 契约项 | 事故 |
|---|--------|------|
| 1 | RPC 方法名（3 处） | — |
| 2 | 读写分类 | **BUG-35**：漏注入 auth → 配 token 后写操作 100% 被拒 |
| 3 | 领域错误码 | **BUG-36**：缺 4 个码 → 领域失败被当基础设施 throw |
| 4 | 错误码桥接（字符串嗅探 `"CODE: msg"`） | 消息含冒号即误判 |
| 5 | 中文文案 → 错误码 | 改一个字全错，无测试可发现 |
| 6 | 参数校验边界（`batch_size [1,1000]`） | 双份，serve.py 注释自认重复 |
| 7 | 工具名 → 方法名（`agent_stats` → `stats`） | 隐性映射，无处声明 |
| 8 | 传输常量（`__ready__` / `auth`） | 两侧各写一遍 |
| 9 | 返回结构 | 9 个 `output.schema` 与 `_serialize` 各说各话 |

**实测成本对比**：新增一个写方法

| | 改动处 | 漏改后果 |
|---|---|---|
| 改造前 | **12 处**（插件内 9 + 根副本 3），其中 7 处是纯契约同步 | 编译不报、测试全绿、**运行时才炸** |
| 改造后 | **3 处**，且漏改 `contract.json` 编译期即报错 | 测试立刻红 |

---

## 三、契约内容

真源文件：**`contract.json`**（语言中立，TS / Python 均可读）

```
contract.json
├── contractVersion   "1.0.0"      破坏性变更必须递增
├── serviceKey        "selfEvolving"  接缝形态确定后的运行时键名
├── transport         握手信号 / jsonrpc 版本 / auth 参数名 / 单行上限 / JSON-RPC 错误码
├── methods           11 个 RPC 方法（kind: read|write, tool: 对外工具名, params）
├── domainErrors      7 个领域错误码
└── infraError        "INFRA"      仅由 TS 侧合成，Python 侧不产生
```

### 领域失败 vs 基础设施故障（契约的一部分）

- **领域失败**（节点不存在、重叠超限、输入非法）→ 用**规范值**返回 `{ ok: false, code, error }`
- **基础设施故障**（子进程崩溃、RPC 超时）→ 用 **throw**

领域失败是调用方可预期的业务分支，基础设施故障不是。两者混同会破坏调用方按 `code` 分支处理的语义（BUG-36 即此）。

---

## 四、用法

### TypeScript 侧（已完全契约化）

```ts
// 方法名 —— 写错编译期即报错
import { TOOL_TO_METHOD, type RpcMethod } from '@kiwifruit/dsh-self-evolving-contract'

await server.call(TOOL_TO_METHOD.agent_stats, {}, signal)  // → 'stats'

// 读写分类 —— 无需维护本地白名单
import { isWriteMethod } from '@kiwifruit/dsh-self-evolving-contract'
const needAuth = isWriteMethod(method as RpcMethod)

// 错误码 —— 集合与解析均由契约定义
import { parseErrorCode, rpcError, toError, DOMAIN_ERROR_CODES } from '@kiwifruit/dsh-self-evolving-contract'
```

`dsh-self-evolving-agent/src/tools/error-map.ts` 是本包的**门面**（纯 re-export），
原有调用方无需改动即可享受契约化。

### Python 侧（⚠️ 当前状态）

**`scripts/serve.py` 目前仍是字面量，不从 `contract.json` 读取。**

漂移不是靠"源码派生"防止的，而是靠 `test/contract.spec.ts` 的守门：
它用正则从 `serve.py` 源码中抽取 `_ALLOWED_METHODS` / `_WRITE_METHODS` /
`_READ_METHODS` / `DomainError("XXX")` 发射点，与 `contract.json` 断言相等。

也就是说 —— **改一侧漏另一侧，测试立刻红**（已实测验证，见下）。
这是"守门"而非"消除字面量"：正确性已封堵，源码重复作为可读性债保留。

> 若要进一步消除 Python 侧字面量（源码级派生），需解决 contract.json 在
> 子进程中的路径定位与 `prepare-pycore.mjs` 打包链路。详见 `contract-todo.md`
> § Phase 3.6 决策点。

---

## 五、如何新增一个 RPC 方法

按此顺序改，**每一步漏了都会被测试抓到**：

### TS 侧

1. `contract.json` → `methods` 加一项（`kind` / `tool` / `params`）
2. `src/methods.ts` → `RpcMethod` 联合类型加一项
3. `src/methods.ts` → `RPC_METHODS` 数组加一项
4. `src/methods.ts` → `WRITE_METHODS` **或** `READ_METHODS` 加一项
5. `src/methods.ts` → `TOOL_TO_METHOD` 加一项（若对外暴露为工具）
6. `dsh-self-evolving-agent/src/tools/index.ts` → `defineTool` + 加入 `ctx.effect` 的 tools 数组

### Python 侧

7. `plugins/dsh-self-evolving-agent/scripts/serve.py` → `_ALLOWED_METHODS` 加一项
8. 同上 → `_WRITE_METHODS` **或** `_READ_METHODS` 加一项
9. 同上 → handler 加分支（领域错误码必须是 `contract.json` 中声明过的）
10. `scripts/serve.py`（根版）→ 同步 7 / 8 / 9 三处

**第 2~5 步漏任一步** → `test/contract.spec.ts` ① 防线红（TS ↔ contract.json）
**第 7~9 步漏任一步** → ② 防线红（contract.json ↔ serve.py）
**第 10 步漏** → ③ 防线红（插件版 ↔ 根版 serve.py）
**第 6 步与第 5 步不一致** → `dsh-self-evolving-agent/test/compliance.spec.ts` R4 红

---

## 六、守门：三条防线

`test/contract.spec.ts`（23 个测试）

| 防线 | 锁定内容 |
|---|---|
| ① TS 类型层 ↔ `contract.json` | 方法名集合、读写分类、错误码集合、工具名映射、传输常量 |
| ② `contract.json` ↔ 插件版 serve.py | `_ALLOWED/_WRITE/_READ_METHODS`、`DomainError` 发射点（双向：无未声明码，也无死码）、`MAX_LINE_BYTES`、握手信号 |
| ③ 插件版 ↔ 根版 serve.py | 上述全部，防开发副本与生产副本漂移 |

外加 `dsh-self-evolving-agent/test/compliance.spec.ts` 的 **R4**：
断言 `registerTools` 注册的工具名集合 **严格等于** 契约 `TOOL_NAMES`。

### 守门强度实测（故意破坏）

| 破坏动作 | 结果 |
|---|---|
| `contract.json` 的 `health` → `health_check` | **4 个测试立刻红**（TS 侧 2 + Python 侧 2） |
| `tools/index.ts` 的 `agent_stats` → `agent_stat` | **2 个 R4 测试立刻红** |

---

## 七、运行

```bash
# 契约包
cd plugins/dsh-self-evolving-contract
npx tsc -p tsconfig.json --noEmit   # 类型检查
npx vitest run                      # 23 个一致性测试

# 插件（消费方）
cd plugins/dsh-self-evolving-agent
npx tsc --noEmit
npx vitest run                      # 7 个测试（含 R4）
```

---

## 八、本包解决不了的（别指望它）

| 问题 | 归属 |
|---|---|
| `pycore` 双副本漂移 | 构建问题，用 `python scripts/check_pycore_sync.py --fix` |
| `python-server.ts` 的 7 个进程生命周期 BUG（BUG-25/26/27/28/29/46/47） | 传输层实现，需重写 |
| Python 核心内部 `ValueError` 中文文案耦合 | 需改 `routing_table.py` 抛类型化异常，要动 371 个测试 |
| `ctx.subagents` 与自进化自有子代理的语义冲突 | 本包只做命名隔离（`selfEvolving`），两套实现并存的事实不变 |
| 错误码桥接靠字符串嗅探 | 需把 code 放进 JSON-RPC error 的 `data` 字段，属结构化改造，独立任务 |
