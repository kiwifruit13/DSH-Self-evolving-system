/** P0-2: 领域失败 vs 基础设施故障分离
 *
 * 本文件是 **契约门面**：所有 API 全部由 `@kiwifruit/dsh-self-evolving-contract`
 * 派生，唯一真源见 `plugins/dsh-self-evolving-contract/contract.json`。
 *
 * 约束：领域失败用规范值，基础设施故障用 throw
 *
 * 错误码约定（与 contract.json 的 domainErrors / infraError 一一对应）：
 * - NOT_FOUND             → 领域：路由表节点不存在
 * - OVERLAP_REJECTED      → 领域：重叠率超过阈值
 * - INVALID_INPUT         → 领域：无效输入
 * - PARENT_NOT_FOUND      → 领域：create_node 目标父节点不存在（BUG-36）
 * - CHILD_ALREADY_EXISTS  → 领域：create_node 子节点已存在（BUG-36）
 * - MAX_DEPTH_EXCEEDED    → 领域：create_node 超过最大深度（BUG-36）
 * - SPLIT_FAILED          → 领域：routing_split 执行失败（BUG-36）
 * - INFRA                 → 基础设施：子进程异常/超时/崩溃
 *
 * 此前 TS 侧白名单缺失 4 个码（PARENT_NOT_FOUND 等），领域失败被误判为
 * 基础设施故障并 throw，破坏调用方的领域处理分支。现错误码集合由契约
 * 定义，`test/contract.spec.ts` 双向锁定一致性。
 *
 * ## 已知缺陷（结构性，非契约问题）
 *
 * `parseErrorCode` 是**字符串嗅探**——Python 侧把 code 拼进 message 文本，
 * TS 侧按冒号切回来。任何含冒号且前缀恰好匹配的消息都会被误判。
 * 根治方向是把 code 放进 JSON-RPC error 的 `data` 字段（结构化），需同时
 * 改 serve.py 的 _handle 与本函数，见 contract-todo.md。
 */

export {
  /** 全部错误码联合类型（领域 + 基础设施） */
  type ErrorCode,
  /** 领域错误码子集 */
  type DomainErrorCode,
  /** 基础设施错误码子集（仅 1 个值：'INFRA'） */
  type InfraErrorCode,
  /** 领域错误码集合 */
  DOMAIN_ERROR_CODES,
  /** 基础设施错误码常量 */
  INFRA_ERROR_CODE,
  /** 全部错误码集合（= 领域 + INFRA） */
  ALL_ERROR_CODES,
  /** 领域失败信封（ok:false + code + error） */
  type RpcErrorEnvelope,
  /** 类型守卫 */
  isDomainError,
  /** 从 JSON-RPC 错误信息 "CODE: message" 中提取领域错误码 */
  parseErrorCode,
  /** 构造领域失败信封 */
  rpcError,
  /** 构造带 code 的 Error（用于基础设施 throw） */
  toError,
} from '@kiwifruit/dsh-self-evolving-contract'
