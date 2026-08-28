import type { Context } from '@deepseek-ai/cordis'
import { fileURLToPath } from 'node:url'
import Schema from '@deepseek-ai/schemastery'

import { PythonServer } from './python-server.js'
import { registerTools } from './tools/index.js'

export const name = 'dsh-self-evolving-agent'
export const inject = ['tools', 'systemPrompt']

export interface Config {
  dbPath: string
  pythonBin: string
  serveScript?: string
  reconnectIntervalMs: number
  /** 只读模式：拒绝所有写方法（透传 serve.py --readonly） */
  readonly?: boolean
  /** 写操作鉴权 token（透传 serve.py --token，写方法需携带 auth） */
  token?: string
}

// L3 — 双锚点 Bundle 解析合规：
// 插件从自身文件系统位置（__dirname）发现 serve.py，
// 不再依赖 SELF_EVOLVING_PROJECT 环境变量。
const PLUGIN_DIR = fileURLToPath(new URL('..', import.meta.url))
const DEFAULT_SERVE_SCRIPT = `${PLUGIN_DIR}/scripts/serve.py`

export const Config = Schema.object({
  dbPath: Schema.string()
    .required()
    .description('SQLite 数据库路径（绝对路径）'),
  pythonBin: Schema.string()
    .default('python')
    .description('Python 可执行文件路径'),
  serveScript: Schema.string()
    .default(DEFAULT_SERVE_SCRIPT)
    .description('serve.py 路径（默认自动从插件目录发现）'),
  reconnectIntervalMs: Schema.number()
    .default(5000)
    .description('子进程重连间隔（毫秒）'),
  readonly: Schema.boolean()
    .default(false)
    .description('只读模式：拒绝所有写方法（routing_split/prune/planner_plan/report_unknown）'),
  token: Schema.string()
    .default('')
    .description('写操作鉴权 token：设置后写方法需携带 auth 参数'),
})

export function apply(ctx: Context, config: Config) {
  const server = new PythonServer({
    pythonBin: config.pythonBin,
    serveScript: config.serveScript ?? DEFAULT_SERVE_SCRIPT,
    dbPath: config.dbPath,
    reconnectIntervalMs: config.reconnectIntervalMs,
    rpcTimeoutMs: 30000,
    readonly: config.readonly,
    token: config.token,
  })

  // 启动 Python 子进程
  ctx.effect(() => {
    server.start()
    return () => server.stop()
  })

  // 注册系统提示词段
  ctx.effect(() => {
    const dispose = ctx.systemPrompt.section({
      name: 'self-evolving-agent',
      order: 100,
      text: () => `[受控自进化 Agent 框架已激活]
数据库: ${config.dbPath}
主代理 → 查路由表 / 执行 Skill / 未知举证
子代理 → 蒸馏日志 / 维护路由表 / 孵化 Skill
根分类锁定: network | data_parsing | llm_inference | resource_exhaustion | permission`,
    })
    return dispose
  })

  // 注册 DSH 工具
  registerTools(ctx, server)
}