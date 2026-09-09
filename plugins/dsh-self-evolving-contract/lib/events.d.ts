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
/** 分裂子节点前的意图事件（waterfall，返回 false 即否决）
 *
 * 对应 RPC 方法 `routing_split`。
 * 人类可在此拦截自动分裂，实现「受控自主」的锁定语义。
 */
export interface SplitIntent {
    parentCategoryId: string;
    childName: string;
    reason: string;
}
/** 剪枝低分节点前的意图事件（waterfall） */
export interface PruneIntent {
    threshold: number;
    bottomPct: number;
    execute: boolean;
}
/** 创建路由表节点前的意图事件（waterfall）
 *
 * 对应离线规划器内部的 create_node 路径 —— 这是「人类锁定根分类」的主战场：
 * 规划器想新建根分类时，策略插件可在此否决。
 */
export interface NodeCreateIntent {
    categoryId: string;
    parentCategoryId: string | null;
    reason: string;
}
/** 离线规划完成后的观测事件（emit，不可修改） */
export interface PlannedObservation {
    totalProcessed: number;
    accepted: number;
    rejected: number;
}
declare module '@deepseek-ai/cordis' {
    interface Events {
        /** waterfall：第一个返回值的监听器赢得写决策 */
        'selfEvolving/split-intent'(intent: SplitIntent): boolean;
        'selfEvolving/prune-intent'(intent: PruneIntent): boolean;
        'selfEvolving/node-create-intent'(intent: NodeCreateIntent): boolean;
        /** emit：同步观察已完成的操作，不可修改 */
        'selfEvolving/node-created'(categoryId: string): void;
        'selfEvolving/planned'(observation: PlannedObservation): void;
        'selfEvolving/unknown-reported'(enqueued: boolean): void;
    }
}
