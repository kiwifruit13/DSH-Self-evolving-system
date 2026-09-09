import type { Context } from '@deepseek-ai/cordis'
import {
  defineTool,
  type ToolCallView,
  type ToolResult,
  type ToolResultView,
} from '@deepseek-ai/dsh-tools'

import type { PythonServer } from '../python-server.js'
import {
  TOOL_TO_METHOD,
  parseErrorCode,
  rpcError,
  toError,
  type IntentDecision,
  type NodeCreateIntent,
  type PruneIntent,
  type RpcMethod,
  type SplitIntent,
} from '../contract/index.js'

type ToolArgs = Record<string, unknown>
type ToolExec = { signal?: AbortSignal }

/** 安全执行 RPC 调用：领域错误返回规范值，基础设施错误 throw
 *
 * method 参数类型为 RpcMethod（契约定义的联合类型），写错方法名在编译期即报错。
 */
async function safeCall(
  server: PythonServer,
  method: RpcMethod,
  args: ToolArgs,
  exec: ToolExec,
): Promise<never> {
  try {
    const result = await server.call(method, args, exec.signal)
    // BUG-48 修复： falsy 检查会误伤合法的假值结果（0 / false / ""），
    // 把它们吞成 null。此处只对"无结果"做精确判断。
    if (result === null || result === undefined) return null as never
    // 运行时会按 output.schema 规范化该返回值；此处以底部类型收拢，交给定义处推断
    return result as never
  } catch (err) {
    const error = err as Error
    const parsedCode = parseErrorCode(error.message || '')
    if (parsedCode) {
      // 领域失败：从 "CODE: message" 提取 code 和 message
      const colonIdx = error.message.indexOf(':')
      const message = colonIdx >= 0 ? error.message.slice(colonIdx + 1).trim() : error.message
      return rpcError(parsedCode, message) as never
    }
    // 基础设施故障：throw（RPC 超时、子进程崩溃等）
    throw toError('INFRA', error.message || 'Unknown infrastructure error')
  }
}

// ═══════════════════════════════════════════════════════════════
// Step 73: presentCall + presentResult（表现层增强）
// ═══════════════════════════════════════════════════════════════
// 所有工具添加 presentCall（调度时待定卡片）和 presentResult（完成卡片）

function presentCallCard(title: string, _args: ToolArgs): ToolCallView {
  return { card: 'generic', title }
}

/** 从 ToolResult 中提取模型可见文本
 *
 * 刻意不 `import { ContentBlock } from '@deepseek-ai/dsh-llm'`——那是 dsh-tools
 * 的内部依赖，引入它会让本插件依赖一个未在 peerDependencies 声明的包。
 */
function textOf(result: ToolResult): string {
  const parts: string[] = []
  for (const block of result.content) {
    const b = block as { type?: string; text?: string }
    if (b.type === 'text' && typeof b.text === 'string') parts.push(b.text)
  }
  return parts.join('\n')
}

/** 结果卡片 —— 三级兜底：持久化 meta → 模型可见 content → 错误标记
 *
 * ⚠️ 契约修正（BUG-P0-2）：`presentResult` 的第二参是 **ToolResult**
 * `{ content, isError, meta? }`，**不是** `execute` 返回的 canonical value。
 * 此前按 canonical value 解构 `ok` / `routing_count` / `total_processed`，
 * 导致 9 个工具的卡片全部退化为「标题 + 完成」。
 *
 * 首选 meta 是因为它由 `output.presentationMeta` 投影并随 `tool/result`
 * 持久化，会话回放时可无损还原（官方文档要求卡片从 meta 重建，不依赖文本）。
 */
function presentResultCard(
  title: string,
  result: ToolResult,
  fromMeta?: (meta: Record<string, unknown>) => string | null,
): ToolResultView {
  if (result.isError) {
    return { card: 'generic', title, content: [{ type: 'text', text: textOf(result) || '执行失败' }] }
  }
  if (fromMeta && result.meta && typeof result.meta === 'object') {
    const text = fromMeta(result.meta as Record<string, unknown>)
    if (text) return { card: 'generic', title, content: [{ type: 'text', text }] }
  }
  const text = textOf(result)
  return { card: 'generic', title, content: [{ type: 'text', text: text || '完成' }] }
}

/** 统一把 meta 当作记录处理，避免各工具重复做类型窄化 */
function metaPick(
  pick: (meta: Record<string, unknown>) => string | null,
): (meta: Record<string, unknown>) => string | null {
  return pick
}

// ═══════════════════════════════════════════════════════════════
// Step 74: guards（危险操作前置参数校验）
// ═══════════════════════════════════════════════════════════════

function guardRoutingSplit(args: ToolArgs): void {
  const parent = args.parent_category_id as string
  const child = args.child_name as string
  if (!parent || !child) throw new Error('parent_category_id 和 child_name 不可为空')
  if (parent.includes('..')) throw new Error('parent_category_id 包含非法 ".." 段')
  // BUG-30 修复：校验 child_name 中的非法字符（点号、点序列等）
  if (child.includes('.')) throw new Error('child_name 不可包含点号 "."')
  if (child.includes('..')) throw new Error('child_name 不可包含 ".."')
  // child_name 应为纯标识符（字母、数字、下划线、短横线）
  if (!/^[a-zA-Z0-9_-]+$/.test(child)) {
    throw new Error('child_name 仅允许字母、数字、下划线和短横线')
  }
}

function guardRoutingPrune(args: ToolArgs): void {
  const t = (args.threshold as number) ?? 0.1
  const bp = (args.bottom_pct as number) ?? 0.1
  if (t < 0 || t > 1) throw new Error('threshold 必须在 [0, 1] 范围内')
  if (bp < 0 || bp > 1) throw new Error('bottom_pct 必须在 [0, 1] 范围内')
}

function guardPlannerPlan(args: ToolArgs): void {
  const bs = (args.batch_size as number) ?? 10
  if (bs < 1 || bs > 1000) throw new Error('batch_size 必须在 [1, 1000] 范围内')
}

async function safeCallWithGuard(
  server: PythonServer,
  method: RpcMethod,
  args: ToolArgs,
  exec: ToolExec,
  guard: (a: ToolArgs) => void,
): Promise<never> {
  guard(args)
  return safeCall(server, method, args, exec)
}

// ═══════════════════════════════════════════════════════════════
// Step 119: 伴生事件门 —— 补齐「声明了却从不派发」的断裂链路
// ═══════════════════════════════════════════════════════════════
// 契约声明了 6 个 selfEvolving/* 事件，此前全仓库零派发点，导致 README
// 宣称的「人类锁定根分类 / 受控自主」无从生效。此处补齐三个写操作的意图门
// 与三个观测广播。
//
// 模式选择依据：官方 `fs/edit-intent` 先例即 waterfall 决策门——
//   放行 = `return next()`；否决 = 不调 next() 直接返回原因字符串。
//   `false` 不能表达否决：cordis 的 isBailed 把 false 判为「不终止链」。

/** 把 waterfall 的决策归一化为「否决原因 | null（放行）」 */
function verdict(decision: IntentDecision): string | null {
  if (decision === true) return null
  return typeof decision === 'string' ? decision : '被伴生策略插件否决'
}

/** 分裂意图门：无策略插件挂载时 innermost next 放行 */
async function gateSplit(ctx: Context, intent: SplitIntent): Promise<string | null> {
  const decision = await ctx.waterfall(
    'selfEvolving/split-intent',
    intent,
    async () => true,
  )
  return verdict(decision)
}

/** 剪枝意图门 */
async function gatePrune(ctx: Context, intent: PruneIntent): Promise<string | null> {
  const decision = await ctx.waterfall(
    'selfEvolving/prune-intent',
    intent,
    async () => true,
  )
  return verdict(decision)
}

/** 建节点意图门 —— 「人类锁定根分类」的主战场 */
async function gateNodeCreate(
  ctx: Context,
  intent: NodeCreateIntent,
): Promise<string | null> {
  const decision = await ctx.waterfall(
    'selfEvolving/node-create-intent',
    intent,
    async () => true,
  )
  return verdict(decision)
}

/** 执行 RPC 并在成功后广播观测事件（emit，不可修改）
 *
 * `safeCall` 以 `Promise<never>` 收拢类型，此处用 `unknown` 承接运行时真实值，
 * 交给 onResult 后再以 `as never` 交回定义处推断。
 */
async function safeCallAndEmit(
  server: PythonServer,
  method: RpcMethod,
  args: ToolArgs,
  exec: ToolExec,
  guard: ((a: ToolArgs) => void) | null,
  onResult: (value: unknown) => void,
): Promise<never> {
  const value: unknown = guard
    ? await safeCallWithGuard(server, method, args, exec, guard)
    : await safeCall(server, method, args, exec)
  onResult(value)
  return value as never
}

export function registerTools(ctx: Context, server: PythonServer): void {
  // ═══════════════════════════════════════════════════════
  // 1. lookup_exact
  // ═══════════════════════════════════════════════════════

  const lookupExact = defineTool({
    name: 'lookup_exact',
    description:
      '按 category_id 精确查询路由表节点和关联 Skill。' +
      '返回路由表条目、已编译的 Skill（如有）、匹配类型。',
    parameters: {
      category_id: {
        type: 'string',
        required: true,
        description: '完整的路由表节点 ID，如 "network.http_429"',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean' },
          error: { type: 'string' },
          code: { type: 'string' },
          category_id: { type: 'string' },
          match_type: { type: 'string', enum: ['exact', 'fuzzy', 'none'] },
          note: { type: 'string' },
          entry: { type: 'json' },
          skill: { type: 'json' },
        },
      },
      render: (_args, value) => {
        if ((value as { ok: boolean }).ok === false) {
          return [{ type: 'text', text: `查询失败: ${(value as { error: string }).error}` }]
        }
        const v = value as { category_id?: string; match_type?: string; note?: string; entry?: unknown }
        const status = v.match_type === 'exact' ? '精确匹配' : '无匹配'
        return [{ type: 'text', text: `[${status}] ${v.category_id || '无'}${v.note ? '\n' + v.note : ''}` }]
      },
    },
    // Step 73: 表现层投影
    presentCall: (args) => presentCallCard('精确查询', args),
    presentResult: (_args, result) => presentResultCard('精确查询', result),
    execute: async (args, exec) =>
      safeCall(server, TOOL_TO_METHOD.lookup_exact, args, exec),
  })

  // ═══════════════════════════════════════════════════════
  // 2. lookup_fuzzy
  // ═══════════════════════════════════════════════════════

  const lookupFuzzy = defineTool({
    name: 'lookup_fuzzy',
    description:
      '通过标签组合进行模糊查询（AND 语义）。' +
      '标签必须带前缀：状态_/代价_/场景_。' +
      '按排序得分降序返回 Top K。',
    parameters: {
      tags: {
        type: 'array',
        required: true,
        description: '必须匹配的所有标签',
        items: { type: 'string' },
      },
      root_category: { type: 'string', description: '可选的根分类过滤' },
      limit: { type: 'integer', description: '最大返回数量', default: 5 },
    },
    output: {
      schema: {
        type: 'array',
        items: { type: 'object', additionalProperties: true },
      },
      render: (_args, value) => {
        const items = value as unknown[]
        return [{ type: 'text', text: `模糊查询返回 ${items.length} 个匹配条目` }]
      },
      // 规则 7：投影持久化卡片数据，供 presentResult 从 meta 无损重建
      presentationMeta: (_args, value) => ({
        type: 'agent:lookup-fuzzy',
        count: Array.isArray(value) ? value.length : 0,
      }),
    },
    // Step 73
    presentCall: (args) => presentCallCard('模糊查询', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '模糊查询',
        result,
        metaPick((m) => (typeof m.count === 'number' ? `${m.count} 个匹配条目` : null)),
      ),
    execute: async (args, exec) =>
      safeCall(server, TOOL_TO_METHOD.lookup_fuzzy, args, exec),
  })

  // ═══════════════════════════════════════════════════════
  // 3. report_unknown
  // ═══════════════════════════════════════════════════════

  const reportUnknown = defineTool({
    name: 'report_unknown',
    description:
      '将未知错误举证包写入反馈暂存队列。' +
      '子代理将在下一轮离线规划中处理。',
    parameters: {
      error_stack: { type: 'string', required: true, description: '完整错误栈' },
      context: { type: 'object', additionalProperties: true, description: '上下文快照' },
      strategies: {
        type: 'array',
        description: '已尝试的失败方案',
        items: { type: 'string' },
      },
      location_guess: { type: 'string', description: '猜测归属根分类' },
      confidence: { type: 'number', description: '置信度 [0, 1]', default: 0 },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean' },
          error: { type: 'string' },
          enqueued: { type: 'boolean' },
        },
      },
      render: (_args, value) => {
        const v = value as { ok: boolean; enqueued?: boolean; error?: string }
        if (v.enqueued) return [{ type: 'text', text: '举证已入队，等待子代理处理' }]
        return [{ type: 'text', text: `入队失败: ${v.error || '未知错误'}` }]
      },
      // P0-7: 规则 7 — 变更类工具必须使用 presentationMeta
      presentationMeta: (_args, value) => ({
        type: 'agent:feedback',
        enqueued: (value as { enqueued?: boolean }).enqueued ?? false,
      }),
    },
    // Step 73
    presentCall: (args) => presentCallCard('举证入队', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '举证入队',
        result,
        metaPick((m) =>
          m.enqueued === true
            ? '举证已入队，等待子代理处理'
            : m.enqueued === false
              ? '入队失败'
              : null,
        ),
      ),
    execute: async (args, exec) =>
      safeCallAndEmit(
        server,
        TOOL_TO_METHOD.report_unknown,
        args,
        exec,
        (_a) => {
          if (!(_a.error_stack as string)) throw new Error('error_stack 不可为空')
        },
        (value) => {
          // emit：观察已完成的操作，不可修改
          ctx.emit('selfEvolving/unknown-reported', (value as { enqueued?: boolean })?.enqueued === true)
        },
      ),
  })

  // ═══════════════════════════════════════════════════════
  // 4. planner_plan
  // ═══════════════════════════════════════════════════════

  const plannerRun = defineTool({
    name: 'planner_plan',
    description:
      '运行离线规划器：消费暂存队列，自动分类 + 重叠率门禁 + Skill 孵化。' +
      '返回规划报告（处理数/接受数/拒绝数/决策详情）。',
    parameters: {
      batch_size: { type: 'integer', description: '单次消费数量', default: 10 },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          total_processed: { type: 'integer' },
          accepted: { type: 'integer' },
          rejected: { type: 'integer' },
          acceptance_rate: { type: 'number' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          total_processed: number
          accepted: number
          rejected: number
        }
        return [{ type: 'text', text: `规划: 处理 ${v.total_processed}, 接受 ${v.accepted}, 拒绝 ${v.rejected}` }]
      },
      // P0-7: 规则 7 — 变更类工具（写入路由表 + 编译 Skill）
      presentationMeta: (_args, value) => ({
        type: 'agent:planner',
        processed: (value as { total_processed?: number }).total_processed ?? 0,
        accepted: (value as { accepted?: number }).accepted ?? 0,
        rejected: (value as { rejected?: number }).rejected ?? 0,
      }),
    },
    // Step 73 + Step 74 (guard)
    presentCall: (args) => presentCallCard('离线规划', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '离线规划',
        result,
        metaPick((m) =>
          typeof m.processed === 'number'
            ? `处理 ${m.processed}，接受 ${m.accepted ?? 0}，拒绝 ${m.rejected ?? 0}`
            : null,
        ),
      ),
    execute: async (args, exec) => {
      // 意图门：规划器想新建根分类时，策略插件可在此否决（人类锁定根分类）
      // planner_plan 是批量消费队列，待建节点在规划器内部才确定，
      // 因此 categoryId 传通配 `*`：策略插件可据此整体否决「新建节点」能力。
      const veto = await gateNodeCreate(ctx, {
        categoryId: '*',
        parentCategoryId: null,
        reason: `planner_plan batch_size=${(args.batch_size as number) ?? 10}`,
      })
      if (veto) return rpcError('POLICY_VETOED', veto) as never
      return safeCallAndEmit(
        server,
        TOOL_TO_METHOD.planner_plan,
        args,
        exec,
        guardPlannerPlan,
        (value) => {
          const v = value as {
            total_processed?: number
            accepted?: number
            rejected?: number
          }
          ctx.emit('selfEvolving/planned', {
            totalProcessed: v?.total_processed ?? 0,
            accepted: v?.accepted ?? 0,
            rejected: v?.rejected ?? 0,
          })
        },
      )
    },
  })

  // ═══════════════════════════════════════════════════════
  // 5. routing_query
  // ═══════════════════════════════════════════════════════

  const routingQuery = defineTool({
    name: 'routing_query',
    description: '查询路由表条目，支持根分类和标签过滤。',
    parameters: {
      root_category: {
        type: 'string',
        description: '仅返回该根分类下的条目（如 network），不传则返回全部分类',
      },
      tags: {
        type: 'array',
        items: { type: 'string' },
        description: '标签过滤（AND 语义），需带前缀：状态_/代价_/场景_',
      },
    },
    output: {
      schema: {
        type: 'array',
        items: { type: 'object', additionalProperties: true },
      },
      render: (_args, value) => {
        const items = value as unknown[]
        return [{ type: 'text', text: `路由表查询返回 ${items.length} 个条目` }]
      },
      presentationMeta: (_args, value) => ({
        type: 'agent:routing-query',
        count: Array.isArray(value) ? value.length : 0,
      }),
    },
    // Step 73
    presentCall: (args) => presentCallCard('路由查询', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '路由查询',
        result,
        metaPick((m) => (typeof m.count === 'number' ? `${m.count} 个条目` : null)),
      ),
    execute: async (args, exec) =>
      safeCall(server, TOOL_TO_METHOD.routing_query, args, exec),
  })

  // ═══════════════════════════════════════════════════════
  // 6. routing_rank
  // ═══════════════════════════════════════════════════════

  const routingRank = defineTool({
    name: 'routing_rank',
    description: '对路由表条目按四维排序（Freq+Impact+Trend+Cost）得分降序排列。',
    parameters: {
      root_category: {
        type: 'string',
        description: '仅排序该根分类下的条目（如 network），不传则对全表排序',
      },
    },
    output: {
      schema: {
        type: 'array',
        items: { type: 'object', additionalProperties: true },
      },
      render: (_args, value) => {
        const items = value as unknown[]
        return [{ type: 'text', text: `排序返回 ${items.length} 个条目（按得分降序）` }]
      },
      presentationMeta: (_args, value) => ({
        type: 'agent:routing-rank',
        count: Array.isArray(value) ? value.length : 0,
      }),
    },
    // Step 73
    presentCall: (args) => presentCallCard('路由排序', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '路由排序',
        result,
        metaPick((m) => (typeof m.count === 'number' ? `${m.count} 个条目（按得分降序）` : null)),
      ),
    execute: async (args, exec) =>
      safeCall(server, TOOL_TO_METHOD.routing_rank, args, exec),
  })

  // ═══════════════════════════════════════════════════════
  // 7. routing_split
  // ═══════════════════════════════════════════════════════

  const routingSplit = defineTool({
    name: 'routing_split',
    description:
      '从父节点分裂出子节点（含重叠门禁 + 深度限制）。' +
      '分裂前会检查与已有节点的重叠率。',
    parameters: {
      parent_category_id: { type: 'string', required: true, description: '父节点 ID' },
      child_name: { type: 'string', required: true, description: '子节点名称片段' },
      reason: {
        type: 'string',
        default: 'split',
        description: '分裂原因，写入子节点维护日志便于回溯',
      },
      child_boundary_rules: {
        type: 'string',
        description: '子节点边界规则描述，用于后续重叠校验（缺省继承父节点）',
      },
      child_logic_signature: {
        type: 'string',
        description: '子节点逻辑签名（特征串），用于重叠校验的相似度计算',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean' },
          error: { type: 'string' },
          code: { type: 'string' },
          category_id: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { ok: boolean; category_id?: string; error?: string }
        if (v.ok && v.category_id) {
          return [{ type: 'text', text: `分裂成功: ${v.category_id}` }]
        }
        return [{ type: 'text', text: `分裂失败: ${v.error || '未知原因'}` }]
      },
      // P0-7: 规则 7 — 变更类工具（创建路由表节点）
      presentationMeta: (_args, value) => ({
        type: 'agent:split',
        category_id: (value as { category_id?: string }).category_id ?? null,
        ok: (value as { ok?: boolean }).ok ?? false,
      }),
    },
    // Step 73 + Step 74 (guard)
    presentCall: (args) => presentCallCard('路由分裂', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '路由分裂',
        result,
        metaPick((m) =>
          m.ok === true && typeof m.category_id === 'string'
            ? `分裂成功: ${m.category_id}`
            : m.ok === false
              ? '分裂失败'
              : null,
        ),
      ),
    execute: async (args, exec) => {
      // 意图门：guard 先做参数级校验，再交由策略插件做语义级决策
      guardRoutingSplit(args)
      const veto = await gateSplit(ctx, {
        parentCategoryId: args.parent_category_id as string,
        childName: args.child_name as string,
        reason: (args.reason as string) ?? 'split',
      })
      if (veto) return rpcError('POLICY_VETOED', veto) as never
      return safeCallAndEmit(
        server,
        TOOL_TO_METHOD.routing_split,
        args,
        exec,
        guardRoutingSplit,
        (value) => {
          const v = value as { ok?: boolean; category_id?: string }
          if (v?.ok && v.category_id) ctx.emit('selfEvolving/node-created', v.category_id)
        },
      )
    },
  })

  // ═══════════════════════════════════════════════════════
  // 8. routing_prune
  // ═══════════════════════════════════════════════════════

  const routingPrune = defineTool({
    name: 'routing_prune',
    description: '剪枝低分节点：识别得分排名末尾的节点，可选自动合并到父节点。',
    parameters: {
      threshold: { type: 'number', default: 0.1 },
      bottom_pct: { type: 'number', default: 0.1 },
      execute: { type: 'boolean', default: true },
    },
    output: {
      schema: {
        type: 'array',
        items: { type: 'object', additionalProperties: true },
      },
      render: (_args, value) => {
        const items = value as unknown[]
        // BUG-31 配套：恢复 MergePlan 后计划分 merge（并入父节点）与
        // delete（无子节点直接删除）两种 action，文案按实际类型统计
        const plans = items as { action?: string }[]
        const mergeCount = plans.filter((p) => p.action === 'merge').length
        const deleteCount = plans.filter((p) => p.action === 'delete').length
        const parts: string[] = []
        if (mergeCount > 0) parts.push(`${mergeCount} 个节点将合并到父节点`)
        if (deleteCount > 0) parts.push(`${deleteCount} 个孤立节点将直接删除`)
        if (parts.length === 0) parts.push(`${items.length} 个节点待处理`)
        return [{ type: 'text', text: `剪枝计划: ${parts.join('，')}` }]
      },
      // P0-7: 规则 7 — 变更类工具（合并/剪枝路由表节点）
      presentationMeta: (_args, value) => ({
        type: 'agent:prune',
        plan_count: (value as unknown[]).length,
      }),
    },
    // Step 73 + Step 74 (guard)
    presentCall: (args) => presentCallCard('路由剪枝', args),
    presentResult: (_args, result) =>
      presentResultCard(
        '路由剪枝',
        result,
        metaPick((m) =>
          typeof m.plan_count === 'number' ? `${m.plan_count} 个节点待处理` : null,
        ),
      ),
    execute: async (args, exec) => {
      guardRoutingPrune(args)
      const veto = await gatePrune(ctx, {
        threshold: (args.threshold as number) ?? 0.1,
        bottomPct: (args.bottom_pct as number) ?? 0.1,
        execute: (args.execute as boolean) ?? true,
      })
      if (veto) return rpcError('POLICY_VETOED', veto) as never
      return safeCallWithGuard(
        server,
        TOOL_TO_METHOD.routing_prune,
        args,
        exec,
        guardRoutingPrune,
      )
    },
  })

  // ═══════════════════════════════════════════════════════
  // 9. agent_stats
  // ═══════════════════════════════════════════════════════

  const agentStats = defineTool({
    name: 'agent_stats',
    description: '返回路由表和暂存队列统计信息。',
    parameters: {},
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          routing_count: { type: 'integer' },
          pending_count: { type: 'integer' },
          categories: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as { routing_count: number; pending_count: number }
        return [{ type: 'text', text: `路由表 ${v.routing_count} 个, 暂存队列 ${v.pending_count} 个` }]
      },
      presentationMeta: (_args, value) => {
        const v = value as { routing_count?: number; pending_count?: number }
        return {
          type: 'agent:stats',
          routing_count: v?.routing_count ?? 0,
          pending_count: v?.pending_count ?? 0,
        }
      },
    },
    // Step 73
    presentCall: (_args) => presentCallCard('统计信息', {}),
    presentResult: (_args, result) =>
      presentResultCard(
        '统计信息',
        result,
        metaPick((m) =>
          typeof m.routing_count === 'number'
            ? `路由表 ${m.routing_count} 个，暂存队列 ${m.pending_count ?? 0} 个`
            : null,
        ),
      ),
    execute: async (_args, exec) => safeCall(server, TOOL_TO_METHOD.agent_stats, {}, exec),
  })

  ctx.effect(() => {
    const tools = [
      lookupExact,
      lookupFuzzy,
      reportUnknown,
      plannerRun,
      routingQuery,
      routingRank,
      routingSplit,
      routingPrune,
      agentStats,
    ]
    const disposers = tools.map((t) => ctx.tools.register(t))
    return () => {
      disposers.forEach((d) => d())
    }
  })
}