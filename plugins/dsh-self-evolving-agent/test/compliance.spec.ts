/** 约束符合度回归测试：R1 / R2 / R3。 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apply, computeDefaultDbPath } from '../src/index.js'
import type { Context } from '@deepseek-ai/cordis'
import { PythonServer } from '../src/python-server.js'
import { registerTools } from '../src/tools/index.js'

afterEach(() => vi.unstubAllEnvs())

/** 最小可用的 ctx 替身：capture 工具注册、收集 effect、按需返回服务。
 *  autoRun=true 时 effect 立即执行——用于 registerTools（其注册无副作用）；
 *  apply 的测试必须 collect（effect0 会真实 spawn 子进程），不能自动执行。
 */
function makeCtx(
  tools: unknown[],
  effects: Array<() => unknown>,
  services: Record<string, unknown>,
  autoRun = false,
): Context {
  return {
    tools: {
      register: (t: unknown) => {
        tools.push(t)
        return () => {}
      },
    },
    effect: <T>(fn: () => T) => {
      if (autoRun) fn()
      effects.push(fn as () => unknown)
      return () => {}
    },
    get: (key: string) => services[key],
  } as unknown as Context
}

const DEFAULT_CFG = {
  dbPath: '/tmp/test.db',
  pythonBin: 'python',
  serveScript: '/svc.py',
  reconnectIntervalMs: 1,
  readonly: false,
  token: '',
}

describe('R1 — output.schema 由 dsh-tools 权威校验（自动构造不抛错）', () => {
  it('registerTools 能构造并注册全部 9 个工具（含 type:"json" 无损节点）', () => {
    const tools: unknown[] = []
    const effects: Array<() => unknown> = []
    const server = new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 })

    // 一旦任何 schema 含 dsh-tools 不支持的类型/关键字，defineTool 会在构造阶段抛错——
    // 这是 R1 的回归护栏。早期约束扫描误判 'json' 非法，经查权威 schema 定义
    // （dsh-tools schema.d.ts JsonValueSchemaSpec）确认其为受支持的作者侧无损 JSON 节点。
    const ctx = makeCtx(tools, effects, {}, true)
    expect(() => registerTools(ctx, server)).not.toThrow()

    expect(tools.length).toBe(9)
    const names = tools.map((t) => (t as { name?: string }).name)
    expect(names).toContain('lookup_exact')
    expect(names).toContain('report_unknown')
    // 9 个工具名唯一
    expect(new Set(names).size).toBe(names.length)
  })
})

describe('R2 — systemPrompt 由硬依赖改为可选服务', () => {
  it('缺 systemPrompt 时 apply 不抛异常、不访问缺失服务', () => {
    const tools: unknown[] = []
    const effects: Array<() => unknown> = []
    expect(() => apply(makeCtx(tools, effects, {}), DEFAULT_CFG)).not.toThrow()
  })

  it('存在 systemPrompt 时正常注册提示词段', () => {
    const effects: Array<() => unknown> = []
    const section = vi.fn(() => () => {})
    const ctx = makeCtx([], effects, { systemPrompt: { section } } as unknown as Record<string, unknown>)
    apply(ctx, DEFAULT_CFG)
    // apply 内 effect 注册顺序：① server 启动 ② systemPrompt 段 ③ 工具注册。
    // 只触发提示词段这个 effect（调用 systemPrompt.section），不去触发真实的子进程启动 effect。
    const sectionEffect = effects[1]
    expect(sectionEffect).toBeDefined()
    expect(() => sectionEffect!()).not.toThrow()
    expect(section).toHaveBeenCalledTimes(1)
  })
})

describe('R3 — dbPath 默认值下沉到 Config', () => {
  it('SELF_EVOLVING_DB 环境变量优先', () => {
    vi.stubEnv('SELF_EVOLVING_DB', '/custom/self.db')
    expect(computeDefaultDbPath()).toBe('/custom/self.db')
  })

  it('无环境变量时回退用户目录并规范化反斜杠', () => {
    vi.stubEnv('SELF_EVOLVING_DB', '')
    // USERPROFILE 置空串：应回退到 HOME（|| 语义，而非 ?? 把空串当有效 home）
    vi.stubEnv('USERPROFILE', '')
    vi.stubEnv('HOME', 'C:\\Users\\me')
    const p = computeDefaultDbPath()
    expect(p).toMatch(/^C:\/Users\/me\/\.dsh\/profiles\/web\/self-evolving-agents\.db$/)
  })
})