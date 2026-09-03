#!/usr/bin/env python3
"""JSON-RPC 服务器（Cordis 插件内嵌版本）。

双锚点 Bundle 合规：此脚本从自身文件系统位置发现项目根目录，
不再依赖外部环境变量（SELF_EVOLVING_PROJECT）。

用法：
    python serve.py /path/to/agents.db
    python serve.py /path/to/agents.db --listen  # TCP 模式
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _discover_project_root() -> Path:
    """定位 Python 核心根目录（含 main_agent.py 的目录）。

    第七批 R2 修复：默认优先使用包内 `pycore`（打包形态的唯一真源），避免
    向上回溯 8 层时误命中宿主工程（如 harness）自带的 `src/main_agent.py`，
    导致加载到版本错配的 Python 核心（详见 `隐匿bug勘查报告.md` 的 R2）。

    仅当显式设置开发标志 `DSH_DEV=1`（或 `SELF_EVOLVING_DEV=1`）时，
    才允许优先回溯宿主 `src/` 用于本地联调（live-edit 即时生效）；
    未设置标志时，绝不加载宿主 src，从根上杜绝版本错配。
    """
    dev_mode = (
        os.environ.get("DSH_DEV", "0") == "1"
        or os.environ.get("SELF_EVOLVING_DEV", "0") == "1"
    )
    script_dir = Path(__file__).resolve().parent

    def _walk_up(predicate: Any) -> Path | None:
        current = script_dir
        for _ in range(8):
            if predicate(current):
                return current
            current = current.parent
        return None

    if dev_mode:
        # 开发态：优先宿主 src/（允许 live-edit 立即生效），回退 pycore
        host_root = _walk_up(
            lambda c: (c / "src" / "main_agent.py").is_file()
        )
        if host_root is not None:
            return host_root

    # 生产态（或开发态回退）：永远优先包内 pycore，安全且唯一真源
    pkg_root = _walk_up(
        lambda c: (c / "pycore" / "src" / "main_agent.py").is_file()
    )
    if pkg_root is not None:
        return pkg_root / "pycore"

    raise RuntimeError(
        "无法发现 Python 核心（缺少 pycore/src/main_agent.py 或宿主 src/main_agent.py）。"
        "请通过 `npm run prepack` 重建 pycore，或在开发时设置 DSH_DEV=1 以回溯宿主 src/。"
    )


PROJECT_ROOT = _discover_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.main_agent as main_agent_mod  # noqa: E402
import src.models as models_mod  # noqa: E402
import src.offline_planner as planner_mod  # noqa: E402
import src.pending_queue as queue_mod  # noqa: E402
import src.storage as storage_mod  # noqa: E402

# ---------------------------------------------------------------------------
# 跨进程契约（TypeScript ↔ Python 单一真源）
#
# contract.json 由 @kiwifruit/dsh-self-evolving-contract 拥有，本文件不硬编码其中
# 任何值 —— 方法白名单、读写分类、领域错误码、传输常量一律从中派生。
#
# 缺失即中止启动（fail-fast）：契约是鉴权与限长加固的判定依据，若静默降级为内置
# 字面量，加固会在无人察觉的情况下失效（BUG-35 / BUG-44 / BUG-51 的教训）。
# ---------------------------------------------------------------------------


def _load_contract() -> dict[str, Any]:
    """定位并解析契约真源 contract.json。

    查找顺序（首个命中即用）：
      1. `PROJECT_ROOT/contract.json` —— 打包形态。prepare-pycore 会把契约复制进
         pycore/，与 Python 核心同生命周期，与安装方式无关。
      2. 兄弟契约包目录 —— 开发与 npm 安装形态：
             <pkg>/dsh-self-evolving-agent/scripts/serve.py
         →   <pkg>/dsh-self-evolving-contract/contract.json
         插件与契约包始终平级，两种形态下同一相对路径成立。
    """
    script_dir = Path(__file__).resolve().parent
    candidates = (
        PROJECT_ROOT / "contract.json",
        script_dir.parent.parent / "dsh-self-evolving-contract" / "contract.json",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with candidate.open(encoding="utf-8") as handle:
                data: Any = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"契约文件无法解析：{candidate}（{exc}）") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"契约格式错误（期望 JSON 对象）：{candidate}")
        return data
    raise RuntimeError(
        "未找到契约文件 contract.json。已依次查找：\n  "
        + "\n  ".join(str(item) for item in candidates)
        + "\n打包形态请执行 `npm run prepack` 重建 pycore；"
        "开发形态请确认契约包 @kiwifruit/dsh-self-evolving-contract 已安装。"
    )


_CONTRACT: dict[str, Any] = _load_contract()
_TRANSPORT: dict[str, Any] = _CONTRACT["transport"]
_RPC_ERROR_CODES: dict[str, int] = _TRANSPORT["rpcErrorCodes"]

# JSON-RPC 错误码（源自契约，禁止在此改写数值）
_E_PARSE_ERROR = int(_RPC_ERROR_CODES["parseError"])
_E_INVALID_REQUEST = int(_RPC_ERROR_CODES["invalidRequest"])
_E_METHOD_NOT_FOUND = int(_RPC_ERROR_CODES["methodNotFound"])
_E_INTERNAL = int(_RPC_ERROR_CODES["internal"])
_E_DOMAIN = int(_RPC_ERROR_CODES["domain"])

# 传输常量（源自契约）
_JSONRPC_VERSION = str(_TRANSPORT["jsonrpcVersion"])
_READY_SIGNAL = str(_TRANSPORT["readySignal"])
_AUTH_PARAM = str(_TRANSPORT["authParam"])

# 领域错误码全集：serve.py 抛出的每个 DomainError.code 都必须落在其中
DOMAIN_ERROR_CODES = frozenset(_CONTRACT["domainErrors"])


class RPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class DomainError(Exception):
    """领域失败 — 返回规范错误而非异常。

    `code` 必须落在契约 `domainErrors` 内，否则构造即中止：拼错或未登记的
    错误码会让 TS 侧 `error-map.ts` 把它误判为基础设施错误并 throw，故障面
    完全错位（BUG-36 的根因正是领域码与 TS 侧白名单不一致）。
    """
    def __init__(self, code: str, message: str) -> None:
        if code not in DOMAIN_ERROR_CODES:
            raise RuntimeError(
                f"未知领域错误码 '{code}'：不在契约 domainErrors "
                f"{sorted(DOMAIN_ERROR_CODES)} 内。请先在 contract.json 登记。"
            )
        self.code = code
        self.message = message
        super().__init__(message)


def _methods_of_kind(kind: str) -> frozenset[str]:
    """从契约提取指定类别的方法名集合（kind 为 "read" 或 "write"）。"""
    return frozenset(
        name
        for name, spec in _CONTRACT["methods"].items()
        if spec.get("kind") == kind
    )


# 允许经 RPC 暴露的方法白名单（禁止访问私有/任意属性）
_ALLOWED_METHODS = frozenset(_CONTRACT["methods"])

# 读方法：仅查询/观测，无副作用，始终放行（RFC 语义对标 GET/head）
_READ_METHODS = _methods_of_kind("read")

# 写方法：变更路由表 / Skill / 暂存队列，需受鉴权与 readonly 约束（对标 POST/PUT/DELETE）
_WRITE_METHODS = _methods_of_kind("write")

# 第七批 R4：单行请求的字节上限。
# 覆盖所有合法用例（最大的是 planner_plan 的批量举证包与 routing_query
# 的过滤条件），同时防止无界读取耗尽内存。
MAX_LINE_BYTES = int(_TRANSPORT["maxLineBytes"])


def _iter_limited_lines(reader: Any, limit: int = MAX_LINE_BYTES) -> Any:
    """按行读取二进制流，单行超过 `limit` 字节时截断。

    产出约定：
    - 正常行 → bytes（含换行符，由调用方 strip）
    - 超长行 → None（哨兵），且该行**剩余字节被排空**，不进入解析

    关键点：超长时不把整行读完再判断，而是用 `readline(limit)` 分片读，
    单次内存占用始终 ≤ limit —— 否则防护形同虚设。
    """
    while True:
        chunk = reader.readline(limit + 2)
        if not chunk:
            return
        # BUG-44 修复：先剥离行尾换行符再测量负载长度。原实现把 `\n`
        # 计入长度判断，导致"恰好 limit 字节负载 + 换行"（len = limit+1）
        # 的合法请求被误判为超长拒绝 —— off-by-one。限长语义针对的是
        # 请求负载本身，不含行分隔符。
        payload = chunk[:-1] if chunk.endswith(b"\n") else chunk
        if len(payload) > limit:
            # 该行超限：若尚未到行尾，持续排空直到行尾/流结束，
            # 单次内存占用始终 ≤ limit —— 否则防护形同虚设。
            if not chunk.endswith(b"\n"):
                while True:
                    rest = reader.readline(limit + 2)
                    if not rest or rest.endswith(b"\n"):
                        break
            yield None
            continue
        yield chunk


def _serialize(obj: Any) -> Any:
    """递归序列化 Python 对象为 JSON 可接受格式。"""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, tuple):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, set):
        # BUG-14 修复：按 str 排序而非直接排序对象（Tag 无 __lt__）
        return [_serialize(item) for item in sorted(obj, key=lambda x: str(x))]
    # dataclass / 命名元组 → to_dict()
    if hasattr(obj, "to_dict"):
        return _serialize(obj.to_dict())
    # 回退：dataclass 或 dataclass-like 对象
    if hasattr(obj, "__dataclass_fields__"):
        return _serialize({
            k: getattr(obj, k)
            for k in obj.__dataclass_fields__
        })
    if hasattr(obj, "__dict__"):
        return _serialize({
            k: v for k, v in obj.__dict__.items()
            if not k.startswith("_")
        })
    return str(obj)


class Server:
    """JSON-RPC 服务器实例，持有 Agent 核心引用。"""

    def __init__(
        self,
        db_path: str,
        readonly: bool = False,
        token: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._readonly = readonly
        self._token = token
        self._storage = storage_mod.Storage(db_path)
        self._queue = queue_mod.PendingQueue(self._storage)
        self._agent = main_agent_mod.MainAgent(self._storage, self._queue)
        self._planner = planner_mod.OfflinePlanner(self._storage, self._queue)

    def init(self, params: dict[str, Any]) -> dict[str, Any]:
        """初始化数据库（建表）。"""
        self._storage.init()
        return {"status": "ok"}

    def stats(self, _params: dict[str, Any]) -> dict[str, Any]:
        """返回路由表和暂存队列统计。"""
        entries = self._storage.query_routing_entries()
        return {
            "routing_count": len(entries),
            "pending_count": self._queue.pending_count,
            # BUG-45 修复：set 无序 → list 顺序随 PYTHONHASHSEED 漂移，
            # 同一数据两次调用可能返回不同顺序，破坏客户端快照对比/缓存。
            # 排序保证输出确定性。
            "categories": sorted(
                {e.category_id.split(".")[0] for e in entries}
            ),
        }

    def lookup_exact(self, params: dict[str, Any]) -> dict[str, Any]:
        category_id = params.get("category_id", "")
        result = self._agent.lookup_exact(category_id)
        if result.match_type == "none":
            raise DomainError("NOT_FOUND", f"节点 '{category_id}' 不存在")
        return _serialize(result)

    def lookup_fuzzy(self, params: dict[str, Any]) -> dict[str, Any]:
        tags_raw = params.get("tags", [])
        tags = {models_mod.Tag(t) for t in tags_raw}
        root_category = params.get("root_category")
        limit = params.get("limit", 5)
        results = self._agent.lookup_fuzzy(tags, root_category, limit)
        return _serialize(results)

    def report_unknown(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = self._agent.report_unknown(
            error_stack=params.get("error_stack", ""),
            context=params.get("context") or {},
            attempted_strategies=params.get("strategies") or [],
            location_guess=params.get("location_guess", ""),
            confidence=params.get("confidence", 0.0),
        )
        return {"enqueued": ok}

    def planner_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        # BUG-45 修复：batch_size 此前未做服务端校验——TS 侧 guard 只约束
        # 自己的客户端，绕过 TS 直接打 TCP/stdio 协议的调用方可以传入
        # 字符串/负数/超大值，最终在 planner 内部以不可控方式触发异常或
        # 资源放大。服务端自行完成类型与范围校验（与 tools/index.ts 的
        # guardPlannerPlan [1, 1000] 保持一致）。
        batch_size = params.get("batch_size", 10)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise DomainError("INVALID_INPUT", "batch_size 必须是整数")
        if batch_size < 1 or batch_size > 1000:
            raise DomainError("INVALID_INPUT", "batch_size 必须在 [1, 1000] 范围内")
        report = self._planner.plan(batch_size=batch_size)
        return _serialize(report)

    def routing_query(self, params: dict[str, Any]) -> dict[str, Any]:
        root_category = params.get("root_category")
        tags_raw = params.get("tags", [])
        tags = {models_mod.Tag(t) for t in tags_raw} if tags_raw else None
        entries = self._storage.query_routing_entries(
            root_category=root_category, tags=tags
        )
        return _serialize(entries)

    def routing_rank(self, params: dict[str, Any]) -> dict[str, Any]:
        root_category = params.get("root_category")
        rt = self._agent._rt  # type: ignore[union-attr]
        ranked = rt.rank(root_category=root_category)
        return _serialize(ranked)

    def routing_split(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        rt = self._agent._rt  # type: ignore
        try:
            child = rt.split(
                parent_category_id=params["parent_category_id"],
                child_name=params["child_name"],
                reason=params.get("reason", "split"),
                actor=params.get("actor", "rpc"),
                child_boundary_rules=params.get("child_boundary_rules"),
                child_logic_signature=params.get("child_logic_signature"),
            )
        except ValueError as exc:
            # BUG-15 修复：按具体错误类型映射不同错误码
            exc_str = str(exc)
            if "不存在" in exc_str:
                raise DomainError("PARENT_NOT_FOUND", exc_str) from exc
            elif "已存在" in exc_str:
                raise DomainError("CHILD_ALREADY_EXISTS", exc_str) from exc
            elif "深度" in exc_str:
                raise DomainError("MAX_DEPTH_EXCEEDED", exc_str) from exc
            elif "重叠" in exc_str:
                raise DomainError("OVERLAP_REJECTED", exc_str) from exc
            else:
                raise DomainError("SPLIT_FAILED", exc_str) from exc
        return _serialize(child)

    def routing_prune(self, params: dict[str, Any]) -> dict[str, Any]:
        rt = self._agent._rt  # type: ignore
        plans = rt.prune_lowest(
            threshold=params.get("threshold", 0.1),
            bottom_pct=params.get("bottom_pct", 0.1),
            execute=params.get("execute", True),
        )
        return _serialize(plans)

    def health(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "db_path": self._db_path}

    def _authorize(
        self, method: str, params: dict[str, Any]
    ) -> str | None:
        """写操作鉴权门卫。

        返回 None 表示放行，否则返回被拒绝的信息。
        - readonly 模式：所有写方法一律拒绝
        - 配置了 token：写方法需携带 `auth` 参数且等于 token
        - 读方法不受以上约束（对标只读/观测）
        """
        if method not in _WRITE_METHODS:
            return None
        if self._readonly:
            return "Write operation forbidden: server is read-only"
        if self._token is not None:
            supplied = params.get(_AUTH_PARAM)
            if supplied != self._token:
                return "Write operation forbidden: missing/invalid auth token"
        return None

    def _handle(
        self, method: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        params = params or {}
        if method not in _ALLOWED_METHODS:
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "error": {
                    "code": _E_METHOD_NOT_FOUND,
                    "message": f"Method '{method}' not found",
                },
            }
        denied = self._authorize(method, params)
        if denied is not None:
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "error": {
                    "code": _E_INVALID_REQUEST,
                    "message": denied,
                },
            }
        handler = getattr(self, method, None)
        if handler is None:
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "error": {
                    "code": _E_METHOD_NOT_FOUND,
                    "message": f"Method '{method}' not found",
                },
            }
        try:
            result = handler(params)
            return {"jsonrpc": _JSONRPC_VERSION, "result": _serialize(result)}
        except DomainError as exc:  # noqa: BLE001
            # P0-2: 领域失败 — 返回 JSON-RPC error 带错误码
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "error": {
                    "code": _E_DOMAIN,
                    "message": f"{exc.code}: {exc.message}",
                },
            }
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return {
                "jsonrpc": _JSONRPC_VERSION,
                "error": {
                    "code": _E_INTERNAL,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            }

    def run_stdio(self) -> None:
        """stdin/stdout 行协议模式。"""
        print(_READY_SIGNAL, flush=True)
        # 第七批 R4：与 TCP 模式统一走限长字节读取，stdio 侧同样设上限
        self._serve(
            _iter_limited_lines(sys.stdin.buffer),
            lambda s: print(s, flush=True),
        )

    def run_connection(self, conn: Any) -> None:
        """处理一条 TCP 连接：按行协议读取并回写，直到连接关闭。

        第七批 R4：用**定长读取**代替无界 `makefile().readline()`。
        原实现对单行长度没有任何上限 —— 恶意或有缺陷的客户端只要不发
        换行符，服务端就会在 `readline` 里无上限累积缓冲，直至内存耗尽
        （即使只绑定 127.0.0.1，也挡不住本机其他进程/失控客户端）。
        """
        reader = conn.makefile("rb", newline="\n")

        def write_line(s: str) -> None:
            conn.sendall(s.encode("utf-8") + b"\n")

        self._serve(_iter_limited_lines(reader), write_line)

    def _serve(self, lines: Any, write: Any) -> None:
        """通用行协议调度：逐行解析 JSON-RPC 请求并回写响应。

        RPC 请求/响应里附带 `auth`（用于写操作鉴权），与 stdio 共用 _handle，
        因此 readonly / token 约束对 stdio 与 TCP 同时生效。

        第七批 R4：单行超过 `MAX_LINE_BYTES` 时返回 _E_INVALID_REQUEST 并丢弃该行剩余
        部分，不参与解析。
        """
        for raw in lines:
            if raw is None:
                # 超长行被截断丢弃（见 _iter_limited_lines）
                write(json.dumps({
                    "jsonrpc": _JSONRPC_VERSION,
                    "error": {
                        "code": _E_INVALID_REQUEST,
                        "message": (
                            f"Request line exceeds {MAX_LINE_BYTES} bytes"
                        ),
                    },
                }))
                continue
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", errors="replace").strip()
            else:
                # 兼容直接以文本可迭代对象调用 _serve 的测试路径
                line = str(raw).strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                write(json.dumps({
                    "jsonrpc": _JSONRPC_VERSION,
                    "error": {"code": _E_PARSE_ERROR, "message": "Invalid JSON"},
                }))
                continue

            method = request.get("method", "")
            params = request.get("params", {})
            resp_id = request.get("id")
            response = self._handle(method, params)
            if resp_id is not None:
                response["id"] = resp_id
            write(json.dumps(response))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", help="SQLite 数据库路径")
    parser.add_argument("--listen", type=int, help="TCP 端口（可选，默认绑定 127.0.0.1）")
    parser.add_argument("--token", help="写操作鉴权 token（可选，设置后写方法需携带 auth）")
    parser.add_argument("--readonly", action="store_true", help="只读模式：拒绝所有写方法")
    args = parser.parse_args()

    server = Server(args.db_path, readonly=args.readonly, token=args.token)
    server._storage.init()  # type: ignore[union-attr]

    if args.listen:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 默认仅回环，禁止未经显式配置暴露到整网
        s.bind(("127.0.0.1", args.listen))
        s.listen(5)
        print(f"Listening on 127.0.0.1:{args.listen}", flush=True)
        while True:
            conn, _ = s.accept()
            try:
                server.run_connection(conn)
            finally:
                conn.close()
    else:
        server.run_stdio()


if __name__ == "__main__":
    main()
