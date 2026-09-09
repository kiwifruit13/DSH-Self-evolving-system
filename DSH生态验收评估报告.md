# DSH 插件生态验收评估报告

> 评估对象：`plugins/dsh-self-evolving-agent`（插件/bundle 包）+ `plugins/dsh-self-evolving-contract`（契约定义包）
> 评估依据：`约束/00-总纲/07-验收标准.md`（7 阶段 21 检查点）+ `示例/示例4.md`（六域避坑指南）+ `示例/工具示例.md`（14 步标准流程）
> 评估方式：源码静态审查 + dsh-tools 类型定义实证 + profile 安装现场取证
> 评估日期：2026-09-09

---

## 一、总评

**形态合规，语义未达标。**

- 能装（`dsh plugin add` 已实证成功）
- 能加载（插件未卡 PENDING，`inject` 处理正确）
- 能跑（`self-evolving-agents.db` + `-wal` 证明 Python 子进程真实运行过）
- 但**伴生事件机制是空壳**、**结果卡片全量降级**——两个对外承诺的能力实际未生效

| 维度 | 结果 |
|---|---|
| 7 阶段 21 检查点 | 通过 13 / 部分或不适用 5 / 不通过 3 |
| 生态纪律附加项（不在 21 点内） | P0 缺陷 2 / P1 缺陷 2 / P2 建议 3 |
| 综合判定 | **可发布，但须先修 2 个 P0** |

---

## 二、逐项判定表

### 阶段 1：包声明文件

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| `cordis.yml` 声明入口 + config + JSDoc | ⚠️ 部分 | agent 包**无 cordis.yml**。bundle 形态（`package.json` 有 `dsh.bundle`）按 `工具示例.md` 第九步可省，但该包同时是插件实现体，建议补 |
| config 属性带 JSDoc | ✅ | `src/index.ts` 6 个字段全部 `.description()` |
| `id` 全局唯一 | ✅ | patch 用完整包名 `@kiwifruit/dsh-self-evolving-agent` 作 id |
| `peerDependencies` 精确含 `-rc.N` | ✅ | `@deepseek-ai/dsh-tools: ^0.1.0-rc.8`，实装 0.1.0-rc.8 ✓ |
| `engines` 约束 Node 版本 | ❌ | **两个包均缺失**。验收 1.2 明确要求 `"engines": {"node": ">=22.19"}` |
| `type: module` / `files` 仅产物 | ✅ | agent: `lib/ scripts/ pycore/ cordis.patch.yml`；contract: `lib/ contract.json python/ README.md` |
| `cordis.patch.yml` insert 行 | ✅ | `- insert:` + `id` + `config` 结构正确 |

### 阶段 2：类型与导出

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| 导出 `name` / `inject` / `apply` 原名（禁别名） | ✅ | `src/index.ts:8-10`，无 alias |
| `Config` 为 Schemastery schema（非普通对象） | ✅ | `Schema.object({...})` |
| 只依赖定义包，不导入实现包 | ✅ | 无 `dsh-*-local` 类导入 |
| 副作用全部走 effect API | ✅ | `ctx.effect()` 包裹 server 启动 / 提示词段 / 工具注册 |
| 定义包零运行时依赖 | ✅ | contract 仅 `import type {} from '@deepseek-ai/cordis'`（编译期擦除）+ 内部相对导入 |
| PENDING 陷阱规避 | ✅ | `systemPrompt` 改 `ctx.get()` 探测并注释说明，非硬依赖 |

### 阶段 3：构建流水线

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| `strict: true` + `noImplicitAny` | ✅ | `tsconfig.json` 已开 strict |
| 产物输出到 `lib/` + `.d.ts` | ✅ | `declaration: true`, `outDir: lib`, `rootDir: src` |
| 双面编译（Host + Client） | N/A | 纯 Host 插件，无 UI 客户端面 |
| `gen-config-catalog` | N/A | dsh monorepo 内部脚本，out-of-tree 插件不可运行 |

### 阶段 4：测试

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| Vitest 单元测试 | ✅ | agent 7 例（R1/R2/R3/R4）+ contract 21 例 |
| Python 侧测试 | ✅ | 371 例，含 `test_integration.py` / `test_rpc_server.py` / `test_gherkin_scenarios.py` |
| `isolate` 测试替身 | ❌ | 用手工 mock ctx 对象，**未使用 `ctx.isolate()`** |
| e2e / 集成测试（SDK harness） | ❌ | 无 `DeepSeekHarness` 实启动测试 |
| `fiber.state = ACTIVE` 断言 | ❌ | 无任何纤程状态断言 |
| 覆盖率门禁 | ⚠️ | 无覆盖率阈值配置 |

### 阶段 5：CI 验证

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| `verify-config-catalog --check` | N/A | 同上，monorepo 内部 |
| 预设健康检查 | N/A | 由 dsh 宿主执行 |
| 自研等价守门 | ✅ | `sync-contract.mjs --check`（prepack 强制，字节级比对）+ `check_pycore_sync.py` |

### 阶段 6：本地安装验证

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| `dsh plugin add` 安装 | ✅ | `~/.dsh/profiles/web/package.json` 已含本包，`bundles` 列表第 4 位 |
| 实装版本 | ✅ | `0.2.2` |
| 运行时实证 | ✅ | `self-evolving-agents.db`(4KB) + `-wal`(37KB) + `-shm`(32KB) → 子进程真实读写过 |
| 双锚解析 | ✅ | profile `node_modules/` 存在实装副本 |

### 阶段 7：发布与分发

| 检查点 | 判定 | 证据 / 说明 |
|---|---|---|
| 发布到 npm scope | ✅ | `@kiwifruit/*` |
| `files` 不泄漏源码 | ✅ | `.npmignore` 额外排除 `src/` `__pycache__/` `*.pyc` |
| 版本号格式 | ⚠️ | `0.2.2` 无 `-rc.N`。第三方包可接受，但 dsh 生态建议 `0.N.M-rc.K` |
| 发布文档 | ✅ | README 完整，含安装说明 |

---

## 三、P0 缺陷（阻断级）

### P0-1 · 伴生事件零派发 —— 「受控自主」是空承诺

**证据**：契约包 `src/events.ts` 声明 6 个事件，`events.ts:56-68` 扩充 `Events` 接口；但全 `plugins/` 目录 grep `ctx.emit|ctx.waterfall|ctx.serial|ctx.bail|ctx.parallel` **零命中**——只有类型声明，无任何派发点。

**影响**：README 与契约注释宣称的「人类可在此拦截自动分裂，实现受控自主的锁定语义」完全不成立。写操作 `routing_split` / `routing_prune` / `planner_plan` 无任何策略拦截点，模型可无限分裂路由表。

**性质**：典型的结构性断点——接口声明了、文档承诺了，实现方从不履约。

**修复方向**（3 选 1）：
1. **真派发**：在 `tools/index.ts` 的写方法执行前 `await ctx.waterfall('selfEvolving/split-intent', intent)`，返回 false 即拒绝
2. **降承诺**：若暂不实现，删除 `events.ts` 中未实现的事件声明，并在 README 标注 roadmap
3. **补测试**：加一条守门测试，断言每个声明的事件在源码中存在对应派发点（正则扫描）

---

### P0-2 · `presentResult` 参数契约错误 —— 9 个工具结果卡片全量降级

**证据**：
- `dsh-tools/lib/types/schema.d.ts:230`：`presentResult?(args: InferArgs<S>, result: ToolResult): ToolResultView | undefined`
- `dsh-tools/lib/types/index.d.ts:174-185`：`ToolResult = { content: ContentBlock[]; isError: boolean; meta?: JsonValue }`
- 项目 `tools/index.ts:157`：`presentResult: (_args, value) => presentResultCard('精确查询', {}, value)`

第二参是 `ToolResult`，代码却当作 canonical value 解构 `v.ok` / `v.routing_count` / `v.total_processed`。

**后果**（实测推导）：
- `v.ok === false` 永假 → 失败也渲染「完成」
- `Array.isArray(v)` 永假（`ToolResult` 是对象）→ 数组类结果永不命中
- 三个计数字段恒 `undefined` → 9 个工具的结果卡片**全部退化为「标题 + 完成」**

**为何测试没抓到**：`compliance.spec.ts` 只断言工具能构造、名字集合正确，从未调用 `presentResult`。

**修复方向**：
```ts
// 变更类工具（已有 presentationMeta）：从 result.meta 重建
presentResult: (_args, result) => {
  const meta = result.meta as { category_id?: string; ok?: boolean } | undefined
  return { card: 'generic', title: '路由分裂',
           content: [{ type: 'text', text: meta?.ok ? `分裂成功: ${meta.category_id}` : result.content[0]?.text ?? '' }] }
}
// 只读类工具：从 result.content 提取，或补 output.presentationMeta
```
配套：加一条测试，用真实 `ToolResult` 调用每个工具的 `presentResult`，断言输出随 `isError` / `meta` 变化。

---

## 四、P1 / P2 清单

| 级别 | 项 | 位置 | 修复 |
|---|---|---|---|
| P1 | 缺 `engines` 字段 | 两包 `package.json` | 加 `"engines": { "node": ">=22.19" }` |
| P1 | 未用 `ctx.isolate()` 做替身注入 | `test/compliance.spec.ts` | 改用 `ctx.isolate('tools')` + mock service |
| P2 | 原生 `setTimeout` ×3 | `python-server.ts` ready 超时 / reconnect / rpc timeout | 改 `inject: ['timer']` + `ctx.timeout`；当前实证可运行（out-of-tree 插件未施加 vm 陷阱），但改用后自动享 HMR 清理 |
| P2 | 部分 `parameters` 缺 `description` | `routing_query.root_category/tags`、`routing_rank.root_category`、`routing_split.child_boundary_rules` 等 | 补齐 description（schema 会流入系统提示词） |
| P2 | 建议补 `cordis.yml` | agent 包根 | 虽 bundle 形态可省，补上可让 config schema 被加载器静态发现 |
| P2 | contract 包 `files` 含空 `python/` | `package.json:26` | 目录为空，填充或从 files 移除 |
| P2 | 版本号无 `-rc` 后缀 | agent `0.2.2` | 生态建议 `0.2.2-rc.1`（第三方非强制） |

---

## 五、修复优先级建议

```
顺序 1  P0-2 presentResult       —— 改 9 处 + 补测试，半天，风险低，收益立即可见
顺序 2  P1   engines             —— 改 2 行
顺序 3  P0-1 伴生事件            —— 需设计决策（真派发 / 降承诺），先定方向再动手
顺序 4  P1   isolate 测试        —— 与顺序 3 的守门测试合并做
顺序 5  P2   其余                —— 批量收尾
```

---

## 六、不适用项说明（N/A 判定依据）

| 项 | 为什么 N/A |
|---|---|
| `gen-config-catalog` / `verify-config-catalog` | dsh monorepo 内部脚本，第三方 out-of-tree 插件无法运行。项目用 `sync-contract.mjs --check` 建立了等价守门 |
| 预设健康检查 | 由 dsh 宿主在启动时执行，非插件作者职责 |
| 双面编译（Host + Client） | 纯 Host 插件，无 UI 客户端面 |
| `dsh.bundle` 声明 | agent 包已有，contract 包是 library 不需要 |
