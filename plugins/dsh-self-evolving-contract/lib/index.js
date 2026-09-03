/** `@kiwifruit/dsh-self-evolving-contract`
 *
 * 跨进程（TypeScript ↔ Python）契约的**定义方**。
 * 单一真源在 `contract.json`；本目录是其 TS 类型层。
 *
 * 定位声明（重要）：
 *   依据 `接缝设计模式.md` 规则 10「至少两个提供者才值得做成接缝」，
 *   当前只有 Python 一个后端，因此本包**不是 seam，是 contract**。
 *   它的合法性来自「契约曾散落在 6 处手工同步」，而非「后端可替换」。
 *   待第二个后端出现时，补一个抽象 Service 子类即可升级为真接缝。
 */
// events.ts 含 declare module 扩充，必须被加载
import './events.js';
export { RPC_METHODS, WRITE_METHODS, READ_METHODS, TOOL_TO_METHOD, TOOLED_METHODS, TOOL_NAMES, isWriteMethod, methodToTool, } from './methods.js';
export { DOMAIN_ERROR_CODES, ALL_ERROR_CODES, INFRA_ERROR_CODE, isDomainError, parseErrorCode, rpcError, toError, } from './errors.js';
export { READY_SIGNAL, JSONRPC_VERSION, AUTH_PARAM, MAX_LINE_BYTES, RPC_ERROR_CODES, } from './transport.js';
/** 服务键名。接缝形态确定后，运行时绑定必须与本常量一致（规则 3） */
export const SERVICE_KEY = 'selfEvolving';
/** 契约版本。破坏性变更必须递增，两侧据此校验兼容性 */
export const CONTRACT_VERSION = '1.0.0';
