// 模块扩充（declare module）要求目标模块已在编译上下文中被解析。
// 本包运行时不依赖 cordis，此处仅为空类型导入以触发加载。
import type {} from '@deepseek-ai/cordis'

/** 伴生事件词汇表 —— 定义包拥有它
 *
 * 依据 `接缝设计模式.md` §三「伴生事件 — 策略与能力分离」与规则 7
 * 「伴生事件声明在定义包中」。
 *
 * 事件门模式（Event-Gate Pattern）：
 *   调用方发起变更 → 派发 *-intent 事件（waterfall）
 *     → 策略插件检查后决定放行或否决
 *       → 放行：实际执行
 *       → 否决：返回拒绝，执行不发生
 *
 * 这使得「人类锁定根分类」这类受控自主策略可以作为**伴生插件**挂载，
 * 无需修改提供者或消费者代码，也不必 import 任何一方。
 */

/** 意图门的决策结果
 *
 * - `true`（含 `next()` 的返回值）→ 放行，实际执行变更
 * - `string` → 否决，字符串作为否决原因，调用方返回 `POLICY_VETOED` 规范值
 *
 * 否决语义来自 waterfall 的核心不变量：**不调用 `next()` 即否决整条链**。
 * 用 `false` 表达否决是错的——cordis 的 `isBailed` 把 `false` 判为「不终止」，
 * 且它无法携带否决原因。
 */
export type IntentDecision = boolean | string

/** 分裂子节点前的意图事件（waterfall）
 *
 * 对应 RPC 方法 `routing_split`。
 * 人类可在此拦截自动分裂，实现「受控自主」的锁定语义。
 */
export interface SplitIntent {
  parentCategoryId: string
  childName: string
  reason: string
}

/** 剪枝低分节点前的意图事件（waterfall） */
export interface PruneIntent {
  threshold: number
  bottomPct: number
  execute: boolean
}

/** 创建路由表节点前的意图事件（waterfall）
 *
 * 对应离线规划器内部的 create_node 路径 —— 这是「人类锁定根分类」的主战场：
 * 规划器想新建根分类时，策略插件可在此否决。
 */
export interface NodeCreateIntent {
  categoryId: string
  parentCategoryId: string | null
  reason: string
}

/** 离线规划完成后的观测事件（emit，不可修改） */
export interface PlannedObservation {
  totalProcessed: number
  accepted: number
  rejected: number
}

declare module '@deepseek-ai/cordis' {
  interface Events {
    /** waterfall：放行必须 `return next()`；不调 `next()` 直接返回字符串即否决
     *
     * 派发方把 innermost `next` 设为「默认放行」，因此无策略插件挂载时自动放行。
     */
    'selfEvolving/split-intent'(
      intent: SplitIntent,
      next: () => Promise<IntentDecision>,
    ): Promise<IntentDecision>
    'selfEvolving/prune-intent'(
      intent: PruneIntent,
      next: () => Promise<IntentDecision>,
    ): Promise<IntentDecision>
    'selfEvolving/node-create-intent'(
      intent: NodeCreateIntent,
      next: () => Promise<IntentDecision>,
    ): Promise<IntentDecision>

    /** emit：同步观察已完成的操作，不可修改 */
    'selfEvolving/node-created'(categoryId: string): void
    'selfEvolving/planned'(observation: PlannedObservation): void
    'selfEvolving/unknown-reported'(enqueued: boolean): void
  }
}
