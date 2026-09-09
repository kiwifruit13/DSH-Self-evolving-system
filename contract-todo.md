# 契约包落地 Todo：`@kiwifruit/dsh-self-evolving-contract`

> 来源：跨进程（TS ↔ Python）契约漂移问题勘查（2026-09-03）
> 约束依据：`约束/001接口/服务定义、提供者、消费者.md`、
> 　　　　　`约束/001接口/接缝设计模式.md` §十 检查清单
> 执行原则：每完成一个 Step 标记 ✅ 并跑一次回归；行为零变化优先于结构漂亮

---

## 一、要解决的问题

9 类跨进程契约，**0 类有守卫**，全部靠注释里的"保持一致"维系：

| # | 契约项 | 重复处 | 已发生的事故 |
|---|--------|--------|--------------|
| 1 | RPC 方法名 | `serve.py _ALLOWED_METHODS`(11) ↔ `tools/index.ts` 9 字面量 ↔ `WRITE_METHODS`(5) | — |
| 2 | 读写分类 | `_WRITE_METHODS`(5)/`_READ_METHODS`(6) ↔ TS `WRITE_METHODS`(5) | **BUG-35**：漏注入 auth → 配 token 后写操作 100% 被拒 |
| 3 | 领域错误码 | serve.py 7 发射点 ↔ `error-map.ts`(8) | **BUG-36**：缺 4 码 → 领域失败被当基础设施 throw |
| 4 | 错误码桥接 | 字符串嗅探 `"CODE: msg"` | 冒号即误判 |
| 5 | 中文文案→错误码 | `if "不存在" in exc_str` | 改文案即全错，无测试可发现 |
| 6 | 参数校验 | `batch_size [1,1000]` 双份 | serve.py 注释自认重复 |
| 7 | 工具名→方法名 | `agent_stats` → `stats` | 隐性映射，无处声明 |
| 8 | 传输常量 | `__ready__` / `auth` 两侧各写一遍 | — |
| 9 | 返回结构 | 9 个 output.schema ↔ `_serialize` 输出 | BUG-48 的 `never` 底部类型 |

**实测收益**：新增 1 个写方法当前需改 **12 处**（插件内 9 + 根副本 3），其中 7 处纯契约同步；
契约包后降到 **3 处**，且漏了契约编译期即报错。

---

## 二、⚠️ 定位声明（先讲清楚，避免自欺）

`接缝设计模式.md` 规则 10：**"至少两个提供者才值得做成接缝，单实现用普通 Service 即可。"**

当前只有 Python 一个后端。**所以本包不是 seam，是 contract（契约包）。**

- 它的合法性来自 **"契约被 6 处重复消费"**，而非 "后端可替换"
- 仍按定义包形态实现（规则 1/2/3/7：抽象 Service + `declare module` + 伴生事件），
  因为规范明说这是**架构级约束，不是可选的设计风格**
- 规则 10 的合规状态：**不满足，且不应强凑第二个提供者**来凑数
- 将来若加第二个后端（TS 原生 / HTTP 远程），本包**自动升级为真接缝**，
  届时只需新增 provider 包改一行组合配置，消费方零改动

---

## 三、架构决策（ADR）

### ADR-1：契约单一真源用 `contract.json`（语言中立）

- **决策**：方法名 / 读写分类 / 错误码 / 传输常量，全部落在 `contract.json`
- **理由**：插件 `tsconfig.json` 已开 `resolveJsonModule`；Python 侧 `json.load` 直接读。
  两侧读同一文件 → **零生成步骤、零镜像漂移**
- **否决的备选**：
  - TS 为源 + 脚本生成 Python → 要写生成器，复杂度高于收益
  - TS / Python 双写 + 一致性测试 → 退回"两处同步"，只是加了守门，没根治

### ADR-2：类型敏感部分用 `satisfies` 反向锁定

JSON 提供数据，TS 手写的联合类型用 `satisfies` 断言**必须与 JSON 键集合完全一致**。
数据是单一真源，类型是编译期护栏——JSON 加一个方法而 TS 联合类型没加，编译报错。

### ADR-3：本次不拆 provider / consumer 包

- 拆三包需新建 2 个 npm 包 + 协调版本，当前只有 1 个提供者，收益不抵成本
- 但**包内遵守规则 5/6**：tools 通过 `ctx.selfEvolving` 查找服务，用 `inject` 声明依赖，
  不直接 import 提供者实现
- 拆包留到 Phase 5（可选），届时零成本

---

## 四、执行计划

### Phase 1 — 契约包骨架与数据真源

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 1.1 | 建包目录与 `package.json` / `tsconfig.json` | 可编译的空包 | ✅ |
| 1.2 | 写 `contract.json`：11 方法名 + 读写分类 + 7 领域错误码 + 传输常量 | 单一真源 | ✅ |
| 1.3 | `src/methods.ts` — 方法名联合类型 + 读写集合 + 工具名映射 | 类型层 | ✅ |
| 1.4 | `src/errors.ts` — 错误码联合类型 + 领域集合 + 错误信封 | 类型层 | ✅ |
| 1.5 | `src/transport.ts` — `__ready__` / `auth` / jsonrpc 常量 | 类型层 | ✅ |
| 1.6 | ~~`python/contract.py`~~ → serve.py 直接 `json.load` contract.json（见 3.4） | Python 侧真源 | ✅ 免落（少一层间接） |
| ✅ | `npm run typecheck` + `npm test` 通过 | 门禁 | ✅ 23 测试全绿 |

> **守卫有效性已实测**：故意把 `contract.json` 的 `health` 改成 `health_check`，
> 4 个测试立刻变红（TS 侧 2 个 + Python 侧 2 个）；恢复后 23 个全绿。
> 漂移从「运行时才炸」变成「测试立刻红」。

### Phase 2 — 定义包形态（抽象 Service + 事件词汇表）

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 2.1 | `src/events.ts` — 伴生事件词汇表（split/prune/node-create intent） | 规则 7 | ✅ |
| 2.2 | `src/index.ts` — `declare module` 扩充 `Events` | 规则 1 | ✅ |
| 2.3 | ~~抽象 `SelfEvolvingService` + 扩充 `Context`~~ | 规则 2/3 | ⏸ **推迟**（见 ADR-4） |
| ✅ | `npm run typecheck` 通过 | 门禁 | ✅ |

> **ADR-4（新增，修订原计划）**：抽象 Service 类是**接缝的运行时载体**。
> 规则 10 明说「单实现用普通 Service 即可」——现在只有一个 Python 后端，
> 做抽象 Service 就是**为接缝而接缝**，徒增一层无替换对象的间接层。
> 伴生事件词汇表不同：它是纯声明、零成本，且是「人类锁定根分类」的天然挂载点，
> 因此保留。待第二个后端出现时，补一个抽象 Service 子类即可平滑升级为真接缝。

### Phase 3 — Python 侧源码级派生（行为零变化）

| # | 任务 | 消除的契约类 | 状态 |
|---|------|--------------|------|
| 3.1 | `error-map.ts` → 从 contract 导入错误码 | #3 | ✅ |
| 3.2 | `python-server.ts` → 导入 `WRITE_METHODS` + `RpcRequest/Response` | #2 #8 | ✅ |
| 3.3 | `tools/index.ts` → 导入方法名常量（含 `agent_stats→stats` 映射） | #1 #7 | ✅ |
| 3.4 | 插件版 `serve.py` → 从 contract.json **派生**全部契约值（17 处字面量清零） | #1 #2 #3 #4 #8 | ✅ |
| 3.5 | `prepare-pycore.mjs` → contract.json 打进 `pycore/` bundle | 打包一致性 | ✅ |
| 3.6 | 删除根版 `scripts/serve.py` 与 `check_serve_sync.py` | **根除 BUG-51 类** | ✅ |
| ✅ | 9 个工具注册测试仍绿 + 编译通过 | 门禁 | ✅ |

> **ADR-5（3.4/3.5 决策复盘）**：原计划「3.4 插件版 + 3.5 根版各改一份」，并一度
> 判断「不值得做」，依据是「双副本协调成本高、serve.py 定位契约包路径脆弱」。
> **两条依据均被实测证伪**：
>
> 1. 忽略行尾符后，两份 serve.py 真实差异仅 96 行（docstring + 引导层 + 1 行注释），
>    功能代码 100% 等价 —— 我此前记的「根版少两个错误分支」是 BUG-51 修复前的
>    过期状态，被误当作论据。
> 2. 路径定位本就不难：contract.json 打进 `pycore/`，而 serve.py 的 `PROJECT_ROOT`
>    在生产态即指向 pycore，故 `PROJECT_ROOT / "contract.json"` 与安装方式
>    （workspace / npm / pnpm 软链）完全无关。
>
> 因此改为：**插件版做源码级派生 + 删除根版**。`DSH_DEV=1` 已覆盖原开发副本用途
> （`_discover_project_root()` 可回溯宿主 `src/`）。
>
> **失败策略**：契约缺失即 fail-fast（`RuntimeError`，退出码 1），不静默降级 ——
> 契约是鉴权与限长加固的判定依据，降级会让加固在无人察觉时失效。风险由防线 ③
> 前移到测试期，并已实测：报错清晰列出两个查找路径与修复指引。
>
> **连带修复**：两个测试文件 `import scripts.serve`（无 `.py` 后缀，被首轮 grep
> 漏检）随根版删除而断链，已改由 `tests/plugin_serve.py` 统一加载生产副本 ——
> 这反而消除了「测的是开发副本、跑的是生产副本」的错配隐患。

### Phase 4 — 接入 `ctx.selfEvolving` 接缝（规则 5/6）

> ⏸ **整体推迟**，理由同 ADR-4：单提供者下接入接缝属超前设计。
> provider/consumer 拆包与 `ctx.selfEvolving` 注册，等第二个后端出现时一并做，
> 届时消费方零改动（这正是先锁定契约的收益）。
> 当前阶段包内仍遵守「不 import 提供者实现」——tools 只从契约包导入常量。

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 4.1 | provider：`SelfEvolvingPythonProvider extends SelfEvolvingService` | 提供者实现 | ⏸ 推迟 |
| 4.2 | `index.ts` apply 中注册 `ctx.selfEvolving` | 运行时绑定 | ⏸ 推迟 |
| 4.3 | tools 改走 `ctx.selfEvolving.<method>()` + `inject` 声明 | 规则 5/6 | ⏸ 推迟 |

### Phase 5 — 守卫与文档

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 5.1 | 契约一致性测试：TS ↔ contract.json ↔ serve.py | **永久免疫 #1#2#3#7** | ✅ 21 测试全绿 |
| 5.2 | ~~扩展 `check_serve_sync.py` 覆盖面~~ → 随双副本删除而**退役** | 虚警修复 | ✅ 已退役 |
| 5.3 | 契约包 README：定位声明 + 用法 + 如何加新方法 | 文档 | ✅ |
| 5.4 | 修复 `pycore` DRIFT（`src/CLAUDE.md` 未同步） | 构建一致性 | ✅ 16 文件一致 |
| 5.5 | 新增防线 ③：contract.json 真源 ↔ `pycore/` bundle 快照 | 防发布包内契约过期 | ✅ |
| 5.6 | 修复 `check_pycore_sync.py` 与 `prepare-pycore.mjs` 的打包红线冲突 | **泄密风险** | ✅ |
| ✅ | 全量回归：typecheck + 371 Python + 7 插件 + 21 契约 全绿 | 门禁 | ✅ |

> **验收结果（2026-09-03 23:2x）**
>
> | 检查 | 结果 |
> |---|---|
> | `pytest tests/` | **371 passed** |
> | agent `tsc --noEmit` | 通过 |
> | agent `vitest` | **7 passed** |
> | contract `tsc --noEmit` | 通过 |
> | contract `vitest` | **21 passed** |
> | `check_pycore_sync.py` | **[OK] 16 个文件一致** |
> | serve.py 冒烟（生产态 + `DSH_DEV=1` 开发态） | 均正常，契约加载成功 |
> | 契约缺失 fail-fast | 退出码 1，报错列出两个查找路径与修复指引 |
> | `ruff check` serve.py / test_hidden_bug_regressions.py | **All checks passed** |
>
> **三条防线（重编号）**
>
> | 防线 | 内容 | 抓住什么 |
> |---|---|---|
> | ① | TS 类型层 ↔ contract.json | TS 侧漏改 |
> | ② | contract.json ↔ serve.py **源码级派生**（断言派生关系存在 + 零字面量残留） | 有人绕过契约硬编码 |
> | ③ | contract.json 真源 ↔ `pycore/` bundle 快照 | 漏跑 `npm run prepack` |
>
> **5.6 的发现过程（值得一记）**：跑 `npm run prepack` 后 `check_pycore_sync.py`
> 突然报 DRIFT（缺 `CLAUDE.md`）。追下去发现两个工具标准打架：
> `prepare-pycore.mjs` 立了「只收 .py」的红线——根 `src/` 混有内部约束文件，
> 带进 npm 包即泄密；而 `check_pycore_sync.py` 全量比对且 `copytree` 全量复制，
> 认为缺 CLAUDE.md 就算漂移。
>
> 后果是双向的：① 上一轮为消 DRIFT 跑的 `--fix` 实质**把 CLAUDE.md 复制进了
> pycore**，引入了真实的泄密风险；② 此后每次 `npm run prepack` 都会误报 DRIFT，
> 门禁沦为噪音。已改为两侧都只认 `.py`（`SYNC_SUFFIX`），验证：`--fix` 不再带
> CLAUDE.md 进 pycore，prepack 后 check 仍为 `[OK] 15 个文件一致`。
>
> 教训：**同一份产物被两个工具维护时，先对齐它们的「什么算内容」标准**，
> 否则「修复」本身可能就是新的缺陷来源。

---

## 五、明确不做（避免范围蔓延）

- ❌ 拆 provider / consumer 为独立 npm 包（留在 Phase 5 之后，等第二个后端）
- ❌ 改 Python 核心的 `ValueError` 中文文案耦合（#5）— 需改 `routing_table.py` 抛类型化
  异常，要动 356 个测试，是独立任务
- ❌ 重构 `python-server.ts` 进程生命周期（BUG-25/26/27/28/29/46/47）— 属传输层，
  契约包无能为力，需换实现
- ❌ 解决 `ctx.subagents` 语义冲突 — 契约包只做命名隔离（`selfEvolving`），
  两套实现并存的事实不变

---

## 六、验收标准

- [x] `contract.json` 是方法名 / 错误码 / 传输常量的**唯一**定义处（grep 无第二处硬编码）
- [x] 新增一个 RPC 方法只需改 3 处，漏改 contract 编译期报错
- [x] 契约一致性测试覆盖全部 11 个方法 + 7 个错误码
- [x] 371 个 Python 测试 + vitest 全绿，行为零变化
- [x] ~~`check_serve_sync.py` 覆盖面扩展~~ → 双副本已消除，门禁退役

### 遗留（非阻塞，未擅自处理）

- `ruff check .` 尚有 12 个**既有**告警：`tests/test_serve_security.py`（6）、
  `generate-api-docs.py`（6）。已用 `git stash` 对比确认**非本次改动引入**，
  涉及删除未使用 import 与改写 f-string，会动到测试文件语义，留给你决定是否清理。
- `plugins/dsh-self-evolving-agent/lib/` 是构建产物，其 `error-map.d.ts` 注释仍
  提及旧路径，下次 `npm run build` 会自行刷新。
