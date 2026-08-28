/** P0-2: 领域失败 vs 基础设施故障分离

 约束：领域失败用规范值，基础设施故障用 throw

 错误码约定：
 - NOT_FOUND          → 领域：路由表节点不存在
 - OVERLAP_REJECTED   → 领域：重叠率超过阈值
 - INVALID_INPUT      → 领域：无效输入
 - INFRA              → 基础设施：子进程异常/超时/崩溃
*/

export type ErrorCode =
  | 'NOT_FOUND'
  | 'OVERLAP_REJECTED'
  | 'INVALID_INPUT'
  | 'INFRA'

export const DOMAIN_ERROR_CODES: ReadonlySet<ErrorCode> = new Set([
  'NOT_FOUND',
  'OVERLAP_REJECTED',
  'INVALID_INPUT',
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