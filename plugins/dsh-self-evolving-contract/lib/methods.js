/** RPC 方法名 / 读写分类 / 工具映射 —— 单一真源见 ../contract.json
 *
 * 本文件是 TS 侧的**类型层**。键集合与 `contract.json` 的一致性由
 * `test/contract.spec.ts` 双向锁定（改一侧而漏另一侧，测试必红）。
 *
 * 之所以不从 JSON 直接 import 推导类型：JSON 推导出的字面量类型宽泛，
 * 会让 `server.call('typo', ...)` 这类误用逃过编译期检查。手写联合类型
 * 换取最强的类型安全，用测试兜住同步。
 */
export const RPC_METHODS = [
    'init',
    'stats',
    'lookup_exact',
    'lookup_fuzzy',
    'report_unknown',
    'planner_plan',
    'routing_query',
    'routing_rank',
    'routing_split',
    'routing_prune',
    'health',
];
/** 写方法：受 readonly 与 token 鉴权约束（对标 HTTP POST/PUT/DELETE）
 *
 * 与 contract.json 中 kind==="write" 的集合一致。
 * BUG-35 根因即此处与 serve.py 的 _WRITE_METHODS 失配：漏一个方法，
 * 该方法就永远不带 auth，配置 token 后 100% 被服务端拒绝。
 */
export const WRITE_METHODS = new Set([
    'init',
    'report_unknown',
    'planner_plan',
    'routing_split',
    'routing_prune',
]);
/** 读方法：仅查询/观测，无副作用，始终放行（对标 HTTP GET/HEAD） */
export const READ_METHODS = new Set([
    'stats',
    'lookup_exact',
    'lookup_fuzzy',
    'routing_query',
    'routing_rank',
    'health',
]);
/** 工具名 → RPC 方法名
 *
 * 这是全项目**唯一允许**工具名与方法名不一致的地方。此前该映射是隐性的
 * （tools/index.ts 里 `name: 'agent_stats'` 却调用 `'stats'`），无任何声明，
 * 无法推导也无法校验。现在显式落到契约里。
 */
export const TOOL_TO_METHOD = {
    lookup_exact: 'lookup_exact',
    lookup_fuzzy: 'lookup_fuzzy',
    report_unknown: 'report_unknown',
    planner_plan: 'planner_plan',
    routing_query: 'routing_query',
    routing_rank: 'routing_rank',
    routing_split: 'routing_split',
    routing_prune: 'routing_prune',
    agent_stats: 'stats',
};
/** 对外注册为 DSH 工具的方法（init / health 仅内部使用，不暴露给模型） */
export const TOOLED_METHODS = RPC_METHODS.filter((m) => Object.values(TOOL_TO_METHOD).includes(m));
/** 工具名列表，顺序与 TOOL_TO_METHOD 的键顺序一致 */
export const TOOL_NAMES = Object.keys(TOOL_TO_METHOD);
export function isWriteMethod(method) {
    return WRITE_METHODS.has(method);
}
/** 按方法名查工具名（反向映射，用于错误提示与测试断言） */
export function methodToTool(method) {
    for (const [tool, m] of Object.entries(TOOL_TO_METHOD)) {
        if (m === method)
            return tool;
    }
    return null;
}
