/** 传输层常量与报文结构 —— 单一真源见 ../contract.json 的 transport
 *
 * 这些值此前在 TS 与 Python 两侧各写一遍：改动一侧不报错、测试不红，
 * 只在握手或鉴权时静默失败。现在两侧都从契约派生。
 */
/** Python 进程就绪的握手信号（stdio 首行） */
export declare const READY_SIGNAL = "__ready__";
export declare const JSONRPC_VERSION = "2.0";
/** 写操作的鉴权参数名。TS 侧自动注入，Python 侧 `_authorize` 读取 */
export declare const AUTH_PARAM = "auth";
/** 单行请求字节上限（1 MiB），防止无界 readline 耗尽内存 */
export declare const MAX_LINE_BYTES: number;
/** JSON-RPC 错误码。与 serve.py `_handle` 的返回码一一对应 */
export declare const RPC_ERROR_CODES: {
    readonly parseError: -32700;
    readonly invalidRequest: -32600;
    readonly methodNotFound: -32601;
    readonly internal: -32000;
    readonly domain: -32001;
};
export type RpcErrorCode = (typeof RPC_ERROR_CODES)[keyof typeof RPC_ERROR_CODES];
export interface RpcRequest {
    jsonrpc: typeof JSONRPC_VERSION;
    id: number;
    method: string;
    params: Record<string, unknown>;
}
export interface RpcResponse {
    jsonrpc: typeof JSONRPC_VERSION;
    id: number;
    result?: unknown;
    error?: {
        code: number;
        message: string;
    };
}
