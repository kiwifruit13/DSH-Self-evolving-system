# @kiwifruit/dsh-self-evolving-agent

DSH（DeepSeek Harness）插件 —— 受控自进化 AI Agent，以 Cordis 原生插件形式把「规避洞察路由表」的读写分离闭环暴露给 DSH。

- 基于 CQRS：主代理（读）+ 子代理（写）通过反馈暂存队列解耦，异步自进化
- 人类锁定根分类骨架，子代理自治地蒸馏日志、分裂剪枝、孵化 Skill
- 质量层：D1 知识增量评分驱动 Skill 编译门禁与剪枝

## 安装

按 dsh 插件生态方式装入 profile（项目根下 src 为 Python 核心，插件为本包）：

```bash
cd plugins/dsh-self-evolving-agent
pnpm build                # TS → ESM（lib/）

# 在 harness 工程根执行
dsh plugin --profile web add <本插件仓库或本地路径>
```

> 运行时依赖用 `peerDependencies`（cordis / dsh-tools / schemastery），由宿主提供，避免版本冲突。

## 配置

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `dbPath` | string（必填） | `%USERPROFILE%\.dsh\profiles\web\self-evolving-agents.db` | SQLite 库路径；可用 `SELF_EVOLVING_DB` 覆盖 |
| `pythonBin` | string | `python` | Python 可执行文件 |
| `serveScript` | string | 插件 `scripts/serve.py` | 可由 `SELF_EVOLVING_SERVE_SCRIPT` 覆盖 |
| `reconnectIntervalMs` | number | `5000` | Python 子进程重连间隔 |
| `readonly` | boolean | `false` | 只读模式：拒绝所有写方法 |
| `token` | string | 空 | 写操作鉴权：设置后写方法需携带 `auth` 参数 |

## 暴露工具（9 个）

`lookup_exact` · `lookup_fuzzy` · `report_unknown` · `planner_plan` · `routing_query` · `routing_rank` · `routing_split` · `routing_prune` · `agent_stats`

## 安全提示

- `.env` 等含密钥的私人文件**切勿**提交到 git 或随此包发布；请走环境变量注入。
- 服务端 CLI 支持 `--readonly` / `--token`；`--listen` 默认绑定 `127.0.0.1`。
- 详细错误码见根项目 `docs/error-codes.md`。

## License

见项目 LICENSE（未内置时以部署方约定为准）。