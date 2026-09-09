/** 约束符合度回归测试：R1 / R2 / R3 / R5 / R6 / R7 / R8。 */
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apply, computeDefaultDbPath } from '../src/index.js'
import { Context } from '@deepseek-ai/cordis'
import type { ToolResult } from '@deepseek-ai/dsh-tools'
import { PythonServer } from '../src/python-server.js'
import { registerTools } from '../src/tools/index.js'
import { TOOL_NAMES } from '../src/contract/index.js'

afterEach(() => vi.unstubAllEnvs())

/** 最小可用的 ctx 替身：capture 工具注册、收集 effect、按需返回服务。
 *  autoRun=true 时 effect 立即执行——用于 registerTools（其注册无副作用）；
 *  apply 的测试必须 collect（effect0 会真实 spawn 子进程），不能自动执行。
 *  extra：注入额外方法（waterfall / emit 等），供事件门测试覆写。
 */
function makeCtx(
  tools: unknown[],
  effects: Array<() => unknown>,
  services: Record<string, unknown>,
  autoRun = false,
  extra: Record<string, unknown> = {},
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
    ...extra,
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

describe('R4 — 工具名集合与契约 TOOL_NAMES 严格一致', () => {
  it('registerTools 注册的工具名 == 契约 TOOL_NAMES（顺序无关）', () => {
    const tools: unknown[] = []
    const effects: Array<() => unknown> = []
    const server = new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 })
    const ctx = makeCtx(tools, effects, {}, true)
    registerTools(ctx, server)
    const names = (tools.map((t) => (t as { name?: string }).name) as string[]).sort()
    expect(names).toEqual([...TOOL_NAMES].sort())
  })

  it('TOOL_TO_METHOD 中的工具名也必须出现在 ctx.tools（防 schema 与映射错位）', () => {
    const tools: unknown[] = []
    const effects: Array<() => unknown> = []
    const server = new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 })
    const ctx = makeCtx(tools, effects, {}, true)
    registerTools(ctx, server)
    const names = new Set(tools.map((t) => (t as { name?: string }).name) as string[])
    // 保护 agent_stats → stats 这条隐性映射的另侧：
    // 即使 TOOL_NAMES 改了，TOOL_TO_METHOD.agent_stats 也必须真注册过。
    expect(names.has('agent_stats')).toBe(true)
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

// ═══════════════════════════════════════════════════════════════
// R5 — presentResult 从 ToolResult 重建（BUG-P0-2 回归护栏）
// ═══════════════════════════════════════════════════════════════
// 修复前：presentResult 把第二参当 canonical value 解构 ok/routing_count，
// 9 个工具卡片全部退化为「标题 + 完成」。本组用例传入真实 ToolResult，
// 断言卡片内容随 isError / meta 变化——「能构造」不等于「能用」。

function registerAll(): { tools: Array<Record<string, any>> } {
  const tools: unknown[] = []
  const server = new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 })
  registerTools(makeCtx(tools, [], {}, true), server)
  return { tools: tools as Array<Record<string, any>> }
}

function fakeResult(text: string, meta?: unknown, isError = false): ToolResult {
  return {
    content: [{ type: 'text', text }],
    isError,
    meta,
  } as unknown as ToolResult
}

function cardText(view: { content?: Array<{ type?: string; text?: string }> }): string {
  return (view.content ?? [])
    .map((b) => (b.type === 'text' ? (b.text ?? '') : ''))
    .join('\n')
}

describe('R5 — presentResult 从 ToolResult 重建卡片', () => {
  const { tools } = registerAll()
  const byName = (name: string) => tools.find((t) => t.name === name)!

  it('9 个工具都声明了 presentCall / presentResult', () => {
    for (const name of TOOL_NAMES) {
      const t = byName(name)
      expect(t, `工具 ${name} 未注册`).toBeTruthy()
      expect(typeof t.presentCall, `${name}.presentCall`).toBe('function')
      expect(typeof t.presentResult, `${name}.presentResult`).toBe('function')
    }
  })

  it('失败结果（isError=true）不再伪装成「完成」', () => {
    // args 必须满足 parameters schema（required 字段），否则 defineTool 的
    // 展示软校验会直接返回 undefined（通用回退）——这是官方契约，不是 bug
    const view = byName('lookup_exact').presentResult(
      { category_id: 'network.x' },
      fakeResult('', undefined, true),
    )
    expect(cardText(view)).not.toBe('完成')
  })

  it('变更类工具从持久化 meta 重建（回放可还原）', () => {
    expect(
      cardText(byName('planner_plan').presentResult({}, fakeResult('', { processed: 5, accepted: 3, rejected: 2 }))),
    ).toContain('处理 5')
    expect(
      cardText(byName('routing_split').presentResult(
        { parent_category_id: 'network', child_name: 'dns' },
        fakeResult('', { ok: true, category_id: 'network.dns' }),
      )),
    ).toContain('network.dns')
    expect(
      cardText(byName('routing_prune').presentResult({}, fakeResult('', { plan_count: 6 }))),
    ).toContain('6 个节点待处理')
    expect(
      cardText(byName('report_unknown').presentResult({ error_stack: 'x' }, fakeResult('', { enqueued: true }))),
    ).toContain('入队')
  })

  it('只读类工具从 meta 重建条目计数', () => {
    expect(
      cardText(byName('lookup_fuzzy').presentResult({ tags: ['状态_x'] }, fakeResult('', { count: 3 }))),
    ).toContain('3 个匹配条目')
    expect(
      cardText(byName('routing_query').presentResult({}, fakeResult('', { count: 4 }))),
    ).toContain('4 个条目')
    expect(
      cardText(byName('routing_rank').presentResult({}, fakeResult('', { count: 7 }))),
    ).toContain('7 个条目')
    expect(
      cardText(byName('agent_stats').presentResult({}, fakeResult('', { routing_count: 9, pending_count: 2 }))),
    ).toContain('路由表 9 个')
  })

  it('meta 缺失时退回模型可见 content 文本', () => {
    const view = byName('lookup_exact').presentResult(
      { category_id: 'network.x' },
      fakeResult('[精确匹配] network.http_429'),
    )
    expect(cardText(view)).toContain('network.http_429')
  })
})

// ═══════════════════════════════════════════════════════════════
// R6 — 伴生事件守门：声明了就必须派发（结构断链防线）
// ═══════════════════════════════════════════════════════════════
// 修复前：契约声明 6 个事件、全仓库零派发点，README 的「受控自主」无从生效。
// 本用例把「事件必须有派发点」固化为回归护栏。

describe('R6 — 契约事件全部有派发点', () => {
  const src = readFileSync(new URL('../src/tools/index.ts', import.meta.url), 'utf8')

  it('3 个意图门以 waterfall 派发', () => {
    for (const event of [
      'selfEvolving/split-intent',
      'selfEvolving/prune-intent',
      'selfEvolving/node-create-intent',
    ]) {
      expect(
        src,
        `意图门 ${event} 未以 ctx.waterfall 派发`,
      ).toMatch(new RegExp(`ctx\\.waterfall\\(\\s*'${event}'`))
    }
  })

  it('3 个观测事件以 emit 广播', () => {
    for (const event of [
      'selfEvolving/node-created',
      'selfEvolving/planned',
      'selfEvolving/unknown-reported',
    ]) {
      expect(
        src,
        `观测事件 ${event} 未以 ctx.emit 广播`,
      ).toMatch(new RegExp(`ctx\\.emit\\(\\s*'${event}'`))
    }
  })
})

// ═══════════════════════════════════════════════════════════════
// R7 — 策略否决链路：waterfall 决策 → POLICY_VETOED 规范值
// ═══════════════════════════════════════════════════════════════
// waterfall 语义：不调 next() 即否决。否决是**领域结果**（不 throw），
// 返回契约新增的 POLICY_VETOED 规范值；放行（调 next()）则继续执行 RPC。

describe('R7 — 意图门否决与放行', () => {
  /** 工具捕获数组必须传给 makeCtx（register 回调闭包绑定的是 makeCtx 的参数） */
  function splitTool(extra: Record<string, unknown>): Record<string, any> {
    const tools: unknown[] = []
    registerTools(
      makeCtx(tools, [], {}, true, extra),
      new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 }),
    )
    return (tools as Array<Record<string, any>>).find((t) => t.name === 'routing_split')!
  }

  it('策略插件不调 next() → 返回 POLICY_VETOED 且不发起 RPC', async () => {
    const split = splitTool({
      // 模拟已否决：waterfall 直接返回原因字符串，innermost next 未被调用
      waterfall: async () => '否决原因：根分类由人类锁定',
      emit: () => {},
    })
    const result = await split.execute({ parent_category_id: 'network', child_name: 'dns' }, {})
    expect(result).toEqual({
      ok: false,
      error: '否决原因：根分类由人类锁定',
      code: 'POLICY_VETOED',
    })
  })

  it('策略插件调 next() 放行 → 继续执行（无 veto 值）', async () => {
    // server 未 start：放行后进入 RPC 应抛基础设施错误，而非返回 POLICY_VETOED
    const split = splitTool({
      waterfall: async (_e: unknown, _i: unknown, next: () => Promise<unknown>) => next(),
      emit: () => {},
    })
    await expect(
      split.execute({ parent_category_id: 'network', child_name: 'dns' }, {}),
    ).rejects.toThrow(/PythonServer not started/)
  })
})

// ═══════════════════════════════════════════════════════════════
// R8 — isolate 测试替身：通过真实 cordis Context 验证「换实现不换消费者」
// ═══════════════════════════════════════════════════════════════
// 修复前：测试用手工 mock 对象，从未验证 isolate 子上下文解析语义。

describe('R8 — ctx.isolate() 注入测试替身', () => {
  it('在 isolate 子上下文注册 mock tools 服务，registerTools 零改动解析到 mock', () => {
    const root = new Context()
    const child = root.isolate('tools')
    const registered: unknown[] = []
    ;(child as unknown as Record<string, unknown>).tools = {
      register: (t: unknown) => {
        registered.push(t)
        return () => {}
      },
    }
    registerTools(child, new PythonServer({ ...DEFAULT_CFG, rpcTimeoutMs: 1 }))
    expect(registered).toHaveLength(9)
    const names = (registered as Array<{ name?: string }>).map((t) => t.name)
    expect(new Set(names).size).toBe(9)
    expect([...names].sort()).toEqual([...TOOL_NAMES].sort())
  })
})