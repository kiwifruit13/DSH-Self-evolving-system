import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type ToolCallView, type ToolResultView } from '@deepseek-ai/dsh-tools'

import type { PythonServer } from '../python-server.js'
import {
  TOOL_TO_METHOD,
  parseErrorCode,
  rpcError,
  toError,
  type RpcMethod,
} from '@kiwifruit/dsh-self-evolving-contract'

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

function presentResultCard(title: string, _args: ToolArgs, value: unknown): ToolResultView {
  const v = value as { ok?: boolean; error?: string } | { routing_count?: number; pending_count?: number; total_processed?: number } | unknown[]
  if ((v as { ok: boolean })?.ok === false) {
    return { card: 'generic', title, content: [{ type: 'text', text: `失败: ${(v as { error: string }).error}` }] }
  }
  if (Array.isArray(v)) {
    return { card: 'generic', title, content: [{ type: 'text', text: `${v.length} 个条目` }] }
  }
  const rv = v as { routing_count?: number; pending_count?: number; total_processed?: number }
  const summary = rv.routing_count != null
    ? `路由表 ${rv.routing_count} 个`
    : rv.total_processed != null
      ? `处理 ${rv.total_processed} 个`
      : '完成'
  return { card: 'generic', title, content: [{ type: 'text', text: summary }] }
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
    presentResult: (_args, value) => presentResultCard('精确查询', {}, value),
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
    },
    // Step 73
    presentCall: (args) => presentCallCard('模糊查询', args),
    presentResult: (_args, value) => presentResultCard('模糊查询', {}, value),
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
    presentResult: (_args, value) => presentResultCard('举证入队', {}, value),
    execute: async (args, exec) =>
      safeCallWithGuard(server, TOOL_TO_METHOD.report_unknown, args, exec, (_a) => {
        if (!(_a.error_stack as string)) throw new Error('error_stack 不可为空')
      }),
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
    presentResult: (_args, value) => presentResultCard('离线规划', {}, value),
    execute: async (args, exec) =>
      safeCallWithGuard(server, TOOL_TO_METHOD.planner_plan, args, exec, guardPlannerPlan),
  })

  // ═══════════════════════════════════════════════════════
  // 5. routing_query
  // ═══════════════════════════════════════════════════════

  const routingQuery = defineTool({
    name: 'routing_query',
    description: '查询路由表条目，支持根分类和标签过滤。',
    parameters: {
      root_category: { type: 'string' },
      tags: { type: 'array', items: { type: 'string' } },
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
    },
    // Step 73
    presentCall: (args) => presentCallCard('路由查询', args),
    presentResult: (_args, value) => presentResultCard('路由查询', {}, value),
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
      root_category: { type: 'string' },
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
    },
    // Step 73
    presentCall: (args) => presentCallCard('路由排序', args),
    presentResult: (_args, value) => presentResultCard('路由排序', {}, value),
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
      reason: { type: 'string', default: 'split' },
      child_boundary_rules: { type: 'string' },
      child_logic_signature: { type: 'string' },
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
    presentResult: (_args, value) => presentResultCard('路由分裂', {}, value),
    execute: async (args, exec) =>
      safeCallWithGuard(server, TOOL_TO_METHOD.routing_split, args, exec, guardRoutingSplit),
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
    presentResult: (_args, value) => presentResultCard('路由剪枝', {}, value),
    execute: async (args, exec) =>
      safeCallWithGuard(server, TOOL_TO_METHOD.routing_prune, args, exec, guardRoutingPrune),
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
    },
    // Step 73
    presentCall: (_args) => presentCallCard('统计信息', {}),
    presentResult: (_args, value) => presentResultCard('统计信息', {}, value),
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