# 受控自进化 AI Agent 框架

> 一个基于 CQRS 读写分离、由子代理自主进化、由人类锁定根分类骨架的 AI Agent 错误自愈框架。

```
经验蒸馏 → 四维排序 → 地图演化 → Skill孵化 → 前台查询 → 未知反馈 → 后台再蒸馏
```

---

## 一、设计哲学

### CQRS 读写分离

| 代理 | 角色 | 操作 | 响应时间 |
|------|------|------|---------|
| **主代理（MainAgent）** | 前台只读 | 查路由表、执行 Skill、生成举证 | 秒级 |
| **子代理（SubAgent）** | 后台写 | 蒸馏日志、维护路由表、孵化 Skill | 异步 |

两者通过 **PendingQueue（反馈暂存队列）** 异步通信。主代理代码路径中**禁止**出现任何写操作。

### 受控自主

子代理拥有高度自治权，但**根分类骨架由人类锁定**：

```
network | data_parsing | llm_inference | resource_exhaustion | permission
```

分类树可以下钻、分裂、合并，但根节点不可被算法改写。

---

## 二、核心数据资产

| 资产 | 模块 | 说明 |
|------|------|------|
| 规避洞察路由表 | `routing_table.py` | 树状错误分类树，每节点带 LocalMindMap（边界/逻辑/维护日志） |
| 专类 Skills 库 | `skill_compiler.py` | 子代理动态孵化的可执行工作流 DAG（支持 tool/domain/workflow/memory 四种模板模式） |
| 多维标签系统 | `tag_system.py` + `tag_query.py` | 三类前缀（`状态_`/`代价_`/`场景_`），支持 AND/OR/NOT 复合查询 |
| 反馈暂存队列 | `pending_queue.py` | 未分类举证的暂存区，连接主代理与子代理 |
| **质量评分引擎** | `quality_scorer.py` | D1 知识增量评分（E/A/R 三分类），标记冗余节点并驱动剪枝 |
| **子代理池** | `sub_agent_pool.py` | Agent-Builder 专用子代理工厂，按根分类专业化 |

### 质量层数据流

```
路由表节点 ──→ NodeQualityScorer.score() ──→ NodeQualityScore
                     │                            │
                     │ δ ≥ 0.5 (expert)          │ δ < 0.1 (redundant)
                     ▼                            ▼
              保留并编译 Skill              标记 quality_gated
                                           加入剪枝候选
```

### Skill 模板模式

| 根分类 | 模式 | 步骤 |
|-------|------|------|
| `network` | Tool | 参数校验 → 带重试执行 → 结果验证 |
| `data_parsing` | Domain | 格式检测 → 解析负载 → 输出校验 |
| `llm_inference` | Workflow | 预处理输入 → 运行推理 → 后处理输出 |
| `permission` | Memory | 策略查询 → 权限评估 → 决策记录 |
| `resource_exhaustion` | Tool | 同 network |

SpecializedSkill 携带 `pattern`（模式）、`tools`（运行时工具集）、`context_keys`（上下文依赖键）三个运行时字段。

详细架构参见 `docs/架构-Skill质量层.md`。

---

## 三、核心运转闭环

```
┌──────────────────────────────────────────────────────────────────────┐
│                        子代理（后台写）                               │
│                                                                      │
│  distill()  →  consume_pending()  →  maintain()  →  compile_skills() │
│  蒸馏日志      消费暂存               分裂/剪枝      Skill 孵化       │
│    ↑                                                  │              │
│    │                        PendingQueue               │              │
│    │                  ┌────────────────────┐          │              │
│    │                  │   反馈暂存队列       │          │              │
│    └──────────────────┴────────────────────┘          │              │
│                                                        │              │
├────────────────────────────────────────────────────────┼──────────────┤
│                        主代理（前台只读）                  ↓              │
│                                                        路由表          │
│  lookup_exact()  →  execute_skill()  →  report_unknown()             │
│  精确查询         执行 Skill          生成举证入队                     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 四、快速开始

### 环境要求

- Python >=3.10（pyproject `requires-python` 锁定）
- SQLite（stdlib，运行时**零第三方依赖**）

> 开发/测试依赖（pytest、ruff、mypy）见 `pyproject.toml` 的 `[project.optional-dependencies].dev`。
> 覆盖率与 Gherkin/Bdd 为可选增强（未默认安装）。

### 安装（一键 · dsh 插件生态）

从 npm 已发布包一键安装：

```bash
dsh plugin --profile web add @kiwifruit/dsh-self-evolving-agent@0.3.0
```

> `--profile` 指定目标 profile（如 `web`）。本包声明 `dsh.bundle.patch`，安装后自动加入 `dsh.profile.bundles`（bundle 层，**重启 `dsh web` 生效**）。

**从源码 / 本地安装**（开发态，替代一键）：

```bash
# 在插件 checkout 内直接安装当前目录：
dsh plugin --profile web add .
# 或指定源：github:kiwifruit13/DSH-Self-evolving-system / 本地链接路径
```

安装后验证：
- python 子进程 `serve.py` 存在（命令行 `dbPath` 正确）；
- web 启动日志无 FAILED，`registerTools` 跑完即 9 个工具注册成功。

> 详细规范（bundle.patch / peerDependencies / cordis.patch.yml）见"十、Cordis 插件适配"。

### 纯 Python 库方式（可选 · 脱离 dsh 插件生态）

如不需 dsh 插件，仅直接把 Python 核心作库 import，运行测试：

```bash
cd 受控自进化-AI-Agent-框架
python -m pytest tests/
```

### 基础用法（纯库 API）

```python
from src.main_agent import MainAgent
from src.sub_agent import SubAgent
from src.storage import Storage
from src.pending_queue import PendingQueue

# 初始化
db = Storage("agents.db")
db.init()
queue = PendingQueue(db)
agent = MainAgent(db, queue)

# 主代理：精确查询
result = agent.lookup_exact("network.http_429")

# 主代理：标签模糊查询
results = agent.lookup_fuzzy(required_tags={"状态_稳定", "代价_低消耗"})

# 主代理：未知错误举证
agent.report_unknown("GraphQL: Field 'user' not found", context={"field": "user"})

# 子代理：消费暂存队列（离线规划）
from src.offline_planner import OfflinePlanner
planner = OfflinePlanner(db, queue)
report = planner.plan(batch_size=10)
print(f"处理: {report.total_processed}, 接受: {report.accepted}, 拒绝: {report.rejected}")
```

---

## 五、API 速览

### 主代理（只读）

| 方法 | 说明 |
|------|------|
| `lookup_exact(category_id)` | 按分类 ID 精确查询 |
| `lookup_fuzzy(required_tags, limit=5)` | 按标签 AND 条件模糊查询 |
| `execute_skill(category_id, context)` | 执行指定分类的 Skill 工作流 |
| `report_unknown(error_stack, context, attempts)` | 未知错误举证入队 |

### 子代理（只写）

| 方法 | 说明 |
|------|------|
| `distill(session_log)` | 从 DSH Session 日志蒸馏错误修复经验 |
| `consume_pending()` | 消费暂存队列，自动分类 + Skill 孵化 |
| `maintain(quality_delta_min=0.1)` | 路由表维护（分裂 + 剪枝 + D1 质量评分门禁） |
| `compile_skills(top_k=5, quality_delta_min=0.1)` | 为 Top K 分类编译专类 Skill（低质量节点跳过） |

### 子代理池（Agent-Builder 专业化）

| 方法 | 说明 |
|------|------|
| `create_specialized(root_category)` | 创建指定根分类的专用子代理 |
| `auto_balance(threshold=50)` | 自动为节点数超过阈值的根分类创建专用子代理 |
| `maintain()` | 依次调用通用 + 所有专用子代理执行维护 |
| `compile_skills()` | 依次调用通用 + 所有专用子代理编译 Skill |
| `pool_summary()` | 生成子代理池概要统计 |

### 质量评分

| 方法 | 说明 |
|------|------|
| `score(entry)` | 对单节点执行 D1 知识增量评分 |
| `is_low_quality(score, delta_min)` | 判断节点是否为低质量 |

知识增量公式：`delta = E / (E + A + R)`（Expert / Activation / Redundant 三分类）

### 路由表

| 方法 | 说明 |
|------|------|
| `insert(entry)` | 插入（已存在则报错） |
| `update(entry)` | 更新（幂等 upsert） |
| `create_node(entry, validate_overlap=True)` | 统一创建入口（互斥 + 重叠校验） |
| `split(parent, child_name, reason, ...)` | 分裂子节点（含重叠门禁 + 深度限制） |
| `prune_lowest(threshold, bottom_pct, execute=True)` | 剪枝低分节点（可自动合并） |
| `rank(root_category=None)` | 四维排序 |
| `query(root_category=None, tags=None)` | 查询 |
| `query_by_expression(expr, root_category=None)` | AND/OR/NOT 复合标签查询 |

### 重叠校验

| 方法 | 说明 |
|------|------|
| `check(candidate_id, signature, boundary)` | 检查新节点与现有节点重叠率 |

重叠率公式：`0.55 × 签名相似度(Levenshtein) + 0.45 × 边界重叠度(子集检测+Jaccard)`

不同根分类节点互不阻挡（根分类硬性过滤）。

### 排序计算器

| 方法 | 说明 |
|------|------|
| `compute_final_score(stats, days_since_last_seen=0)` | 计算单节点得分 |
| `rank(entries)` | 排序 |
| `top_k(entries, k)` | Top K |

得分公式：

```
final_score = (freq_norm × 0.25 + impact_norm × 0.35 + trend_norm × 0.20 + cost_norm × 0.20) × decay
decay = 2^(-days_since_last_seen / 7)   # 指数衰减，半衰期 7 天
```

---

## 六、标签系统

### 三类前缀（强制校验）

| 前缀 | 示例 | 含义 |
|------|------|------|
| `状态_` | `状态_稳定`, `状态_实验性` | 节点成熟度 |
| `代价_` | `代价_低消耗`, `代价_高延迟`, `代价_中消耗` | 修复成本 |
| `场景_` | `场景_第三方依赖`, `场景_内部微服务` | 适用场景 |

### 遗传与变异

子节点从父节点遗传标签，支持覆盖和移除：

```python
child_tags = inherit_tags(parent.tags, overrides={Tag("状态_实验性")}, removals={Tag("场景_第三方依赖")})
```

### 复合查询

```python
from src.tag_query import TagQueryBuilder

query = (
    TagQueryBuilder()
    .group()
    .must(Tag("状态_稳定"))
    .must(Tag("代价_低消耗"))
    .end_group()
    .or_()
    .group()
    .must(Tag("状态_实验性"))
    .must_not(Tag("场景_本地计算"))
    .end_group()
    .build()
)
results = routing_table.query_by_expression(query)
```

---

## 七、目录结构

```
├── src/                        # 核心源码
│   ├── models.py               # 数据模型（LocalMindMap / Tag / NodeQualityScore / SpecializedSkill）
│   ├── storage.py              # SQLite 存储层
│   ├── routing_table.py        # 路由表操作层
│   ├── scoring.py              # 四维排序计算器
│   ├── overlap_checker.py      # 重叠率校验器
│   ├── offline_planner.py      # 子代理离线规划器
│   ├── skill_compiler.py       # Skill 编译器（4 种模板模式 + 工具集推断）
│   ├── quality_scorer.py       # D1 知识增量评分器（新增 Phase 10）
│   ├── sub_agent_pool.py       # Agent-Builder 子代理工厂（新增 Phase 13）
│   ├── pending_queue.py        # 反馈暂存队列
│   ├── tag_system.py           # 标签系统（遗传/变异/查询）
│   ├── tag_query.py            # 标签复合查询（AND/OR/NOT）
│   ├── main_agent.py           # 主代理（前台只读）
│   └── sub_agent.py            # 子代理（后台写 + 质量门禁）
├── tests/                      # 测试（356 个用例，全绿）
├── docs/                        # 规范白名单（持久契约）
│   ├── 架构-Skill质量层.md      # Phase 10-13 质量层完整架构
│   ├── 工具介绍.md              # Skill 元技能白皮书（Skill-Creator/Judge/Builder/Agent-Builder）
│   └── error-codes.md           # 错误码登记表（唯一真相源）
├── scripts/
│   └── gen_api_docs.py         # API 文档自动生成
├── 待完善/
│   ├── 设计待完善.md            # 31 个已知问题及修复方案
│   ├── cordis-adapter.md       # Cordis 插件适配（过程档案，已归档）
│   ├── cordis-adapter-compliance-plan.md   # 适配合规计划（过程档案）
│   └── cordis-adapter-iteration-plan.md    # 适配迭代计划（过程档案）
├── skill-todo.md               # Phase 10-13 质量层实施进度
├── api_reference.md            # 自动生成 API 参考文档
├── todo.md                     # 实施进度追踪
├── AGENTS_01.md                # 项目级工程落地指南
├── 总览.md                     # 框架设计蓝图
├── Gherkin.md                  # 验收标准（Feature 场景）
└── pyproject.toml              # 项目配置（pytest/ruff/mypy）
```

---

## 八、质量门禁

| 检查 | 工具 | 状态 |
|------|------|------|
| 单元测试 | `pytest`（356 用例） | ✅ 全绿 |
| 静态检查 | `ruff` | ✅ 0 error |
| 类型检查 | `mypy --strict` | ✅ 0 error |
| API 文档 | `scripts/gen_api_docs.py` | ✅ 自动生成 |
| 质量层架构 | `docs/架构-Skill质量层.md` | ✅ Phase 10-13 |

```bash
# 运行测试
python -m pytest tests/

# 静态检查
python -m ruff check src/ tests/

# 类型检查
python -m mypy src/ --strict

# 生成 API 文档
python scripts/gen_api_docs.py
```

---

## 九、已知待完善（9 个 P0）

详见 `待完善/设计待完善.md`。已完成 8/9 项：

- ✅ `split()` 绕过重叠校验 → `create_node()` 统一入口
- ✅ `insert()` = `update()` → 互斥 INSERT 语义
- ✅ 子节点继承父节点 stats → 从零积累
- ✅ `prune()` 空壳 → `merge_into_parent()` 实际合并
- ✅ `candidate_signature` 未使用 → 接入真实签名
- ✅ 根分类二元权重陷阱 → 根分类硬性过滤
- ✅ Jaccard 长度敏感 → 子集检测 + 停用词过滤
- ✅ 创建路径不统一 → `create_node()` 统一入口
- ⬜ 趋势维度无数据源 → 待接入 `last_seen` 时间戳（Phase 6）

---

## 十、Cordis 插件适配

详见 `待完善/cordis-adapter.md`（过程档案，已从 docs 归档）。

Python 核心通过 JSON-RPC over stdin/stdout 暴露为 Cordis 原生插件，被 `dsh plugin add` 加载。

```
Cordis Plugin (TS)  ←stdin/stdout→  Python 核心 (Python)
  ├─ index.ts           (apply + 生命周期)
  ├─ python-server.ts   (子进程管理 + JSON-RPC)
  ├─ tools/index.ts     (9 个 DSH 工具)
  └─ cordis.patch.yml   (Bundle 装配)
```

暴露的 9 个工具：`lookup_exact` / `lookup_fuzzy` / `report_unknown` / `planner_plan` / `routing_query` / `routing_rank` / `routing_split` / `routing_prune` / `agent_stats`

### 插件安装（dsh 插件生态，符合 dsh 插件规范）

**推荐方式：通过 dsh 插件生态安装**（`dsh plugin` 是 dsh 规范的插件管理入口）：

```bash
# 1. 构建插件（TS → ESM）
cd plugins/dsh-self-evolving-agent
pnpm build

# 2. 以 dsh 生态方式装入 web profile
#    在本机 harness 工程根（如 D:\Git\gitee\deepseek-harness-master）执行：
dsh plugin --profile web add "E:/Deepseek/DSH-Self-evolving-system/plugins/dsh-self-evolving-agent"
```

该命令会按 dsh 插件规范自动完成三件事：
1. 在当前 profile 写入 `link:` 依赖（本地路径链接，不发布 registry）；
2. 把插件加入 `dsh.profile.bundles`（作为活跃 bundle 加载）；
3. 应用 `cordis.patch.yml` 的 bundle patch（注册插件 entry 与 `config` 默认值）。

**符合 dsh 插件规范的要点**：
- `package.json` 声明 `dsh.bundle.patch: ./cordis.patch.yml`，使包被识别为 **bundle 层**；
- `cordis.patch.yml` 的 `insert` 在 plugin 作用域注册 entry（`id`/`name` 为 scoped 名）；
- 运行时依赖用 `peerDependencies`（cordis / dsh-tools / schemastery），避免与宿主版本冲突。

**安装后验证（两个信号）**：
- **子进程**：`Get-CimInstance Win32_Process -Filter "Name='python.exe'"` 应能看到 `serve.py` 进程、命令行 `dbPath` 正确；
- **工具列表**：web 启动日志无 FAILED/error，`registerTools` 全量跑完即 9 个工具注册成功。

> 仅当希望脱离 dsh 插件生态、作为纯 Python 库直接 import 时，才走根目录的"快速开始"方式（无害二者选一）。

### 插件配置（Config 项）

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `dbPath` | string（必填） | `%USERPROFILE%\.dsh\profiles\web\self-evolving-agents.db` | SQLite 库路径；可用环境变量 `SELF_EVOLVING_DB` 覆盖 |
| `pythonBin` | string | `python` | Python 可执行文件 |
| `serveScript` | string | 插件目录 `scripts/serve.py` | 可由 `SELF_EVOLVING_SERVE_SCRIPT` 覆盖 |
| `reconnectIntervalMs` | number | `5000` | Python 子进程重连间隔 |
| `readonly` | boolean | `false` | 只读模式：拒绝所有写方法 |
| `token` | string | 空 | 写操作鉴权：设置后写方法需携带 `auth` 参数 |

### 服务端 CLI（`scripts/serve.py`）

| 参数 | 说明 |
|------|------|
| `<db_path>` | SQLite 库路径（必填） |
| `--listen <port>` | TCP 行协议端口（**默认绑定 127.0.0.1**，不经 `--token` 不对外暴露） |
| `--readonly` | 只读：拒绝写方法 |
| `--token <str>` | 写方法鉴权 token（配合调用方 `params.auth`） |

> 读写方法分组：读（stats/lookup_*/routing_query/routing_rank/health）始终放行；写（init/report_unknown/planner_plan/routing_split/routing_prune）受 `readonly`/`token` 约束。

---

## 十一、设计参考

- **总览.md** — 框架完整设计蓝图
- **Gherkin.md** — 3 个 Feature 验收标准
- **AGENTS_01.md** — 项目级工程落地指南
- **待完善/设计待完善.md** — 31 个已知问题及修复方案
- **api_reference.md** — 自动生成 API 参考文档
- **docs/架构-Skill质量层.md** — Phase 10-13 质量层架构（D1 评分 / Skill 模板模式 / 子代理池）
- **docs/工具介绍.md** — Skill 元技能白皮书（Skill-Creator / Skill-Judge / Skill-Builder / Agent-Builder）
- **docs/error-codes.md** — 错误码登记表（唯一真相源）
- **skill-todo.md** — Phase 10-13 实施进度追踪

---

> 受控自进化 — 子代理高度自治，根分类骨架由人类锁定。