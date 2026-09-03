/** 领域错误码 —— 单一真源见 ../contract.json 的 domainErrors / infraError
 *
 * 领域失败（节点不存在、重叠超限、输入非法……）用**规范值**返回；
 * 基础设施故障（子进程崩溃、RPC 超时）用 **throw**。这条分离是契约的一部分：
 * 领域失败是调用方可预期的业务分支，基础设施故障不是。
 */

/** 领域错误码：由 Python 侧主动抛出，调用方应当处理 */
export type DomainErrorCode =
  | 'NOT_FOUND'
  | 'OVERLAP_REJECTED'
  | 'INVALID_INPUT'
  | 'PARENT_NOT_FOUND'
  | 'CHILD_ALREADY_EXISTS'
  | 'MAX_DEPTH_EXCEEDED'
  | 'SPLIT_FAILED'

/** 基础设施错误码：仅由 TS 侧在 RPC 传输失败时合成，Python 侧不产生 */
export type InfraErrorCode = 'INFRA'

export type ErrorCode = DomainErrorCode | InfraErrorCode

/** 全部领域错误码 —— 与 contract.json 的 domainErrors 键一一对应
 *
 * BUG-36 根因即此处白名单缺失 4 个码（PARENT_NOT_FOUND 等）：
 * 缺失的领域失败会被 parseErrorCode 判为「非领域」，进而被当作基础设施
 * 故障 throw，破坏调用方按 code 分支处理的语义。
 */
export const DOMAIN_ERROR_CODES: ReadonlySet<DomainErrorCode> =
  new Set<DomainErrorCode>([
    'NOT_FOUND',
    'OVERLAP_REJECTED',
    'INVALID_INPUT',
    'PARENT_NOT_FOUND',
    'CHILD_ALREADY_EXISTS',
    'MAX_DEPTH_EXCEEDED',
    'SPLIT_FAILED',
  ])

export const INFRA_ERROR_CODE: InfraErrorCode = 'INFRA'

export const ALL_ERROR_CODES: ReadonlySet<ErrorCode> = new Set<ErrorCode>([
  ...DOMAIN_ERROR_CODES,
  INFRA_ERROR_CODE,
])

export function isDomainError(code: string): code is DomainErrorCode {
  return DOMAIN_ERROR_CODES.has(code as DomainErrorCode)
}

/** 领域失败的规范信封 */
export interface RpcErrorEnvelope {
  ok: false
  error: string
  code: ErrorCode
}

/** 从 JSON-RPC 错误信息 `"CODE: message"` 中提取领域错误码
 *
 * ⚠️ 这是**字符串嗅探**，不是结构化契约 —— Python 侧把 code 拼进 message
 * 文本，TS 侧再按冒号切回来。任何含冒号且前缀恰好匹配的消息都会被误判。
 *
 * 根治方向是把 code 放进 JSON-RPC error 的 `data` 字段（结构化），
 * 需同时改 serve.py 的 _handle 与本函数，属独立任务，见 contract-todo.md。
 */
export function parseErrorCode(message: string): DomainErrorCode | null {
  const colonIdx = message.indexOf(':')
  if (colonIdx === -1) return null
  const code = message.slice(0, colonIdx).trim()
  return isDomainError(code) ? code : null
}

export function rpcError(code: ErrorCode, message: string): RpcErrorEnvelope {
  return { ok: false, error: message, code }
}

export function toError(code: ErrorCode, message: string): Error {
  return Object.assign(new Error(message), { code })
}
