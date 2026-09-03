/** P0-2: 领域失败 vs 基础设施故障分离

 约束：领域失败用规范值，基础设施故障用 throw

 错误码约定：
 - NOT_FOUND             → 领域：路由表节点不存在
 - OVERLAP_REJECTED      → 领域：重叠率超过阈值
 - INVALID_INPUT         → 领域：无效输入
 - PARENT_NOT_FOUND      → 领域：create_node 目标父节点不存在（BUG-36）
 - CHILD_ALREADY_EXISTS  → 领域：create_node 子节点已存在（BUG-36）
 - MAX_DEPTH_EXCEEDED    → 领域：create_node 超过最大深度（BUG-36）
 - SPLIT_FAILED          → 领域：routing_split 执行失败（BUG-36）
 - INFRA                 → 基础设施：子进程异常/超时/崩溃

（PARENT_NOT_FOUND 等 4 个码与 scripts/serve.py 中 DomainError 的发射点
  一一对应：create_node 3 处 + routing_split 1 处。此前 TS 侧白名单缺失，
  这些领域失败会被误判为基础设施故障并 throw，破坏调用方的领域处理分支。）
*/

export type ErrorCode =
  | 'NOT_FOUND'
  | 'OVERLAP_REJECTED'
  | 'INVALID_INPUT'
  | 'PARENT_NOT_FOUND'
  | 'CHILD_ALREADY_EXISTS'
  | 'MAX_DEPTH_EXCEEDED'
  | 'SPLIT_FAILED'
  | 'INFRA'

export const DOMAIN_ERROR_CODES: ReadonlySet<ErrorCode> = new Set([
  'NOT_FOUND',
  'OVERLAP_REJECTED',
  'INVALID_INPUT',
  'PARENT_NOT_FOUND',
  'CHILD_ALREADY_EXISTS',
  'MAX_DEPTH_EXCEEDED',
  'SPLIT_FAILED',
])

export interface RpcErrorEnvelope {
  ok: false
  error: string
  code: ErrorCode
}

export function isDomainError(code: string): code is ErrorCode {
  return DOMAIN_ERROR_CODES.has(code as ErrorCode)
}

/** 从 JSON-RPC 错误信息 "CODE: message" 中提取 code */
export function parseErrorCode(message: string): ErrorCode | null {
  const colonIdx = message.indexOf(':')
  if (colonIdx === -1) return null
  const code = message.slice(0, colonIdx).trim()
  if (DOMAIN_ERROR_CODES.has(code as ErrorCode)) {
    return code as ErrorCode
  }
  return null
}

export function rpcError(code: ErrorCode, message: string): RpcErrorEnvelope {
  return { ok: false, error: message, code }
}

export function toError(code: ErrorCode, message: string): Error {
  return Object.assign(new Error(message), { code })
}