#!/usr/bin/env python3
"""JSON-RPC 服务器 — 将自进化 Agent 核心暴露为 stdin/stdout JSON-RPC。

⚠️ 注意：此为开发/CI 用副本。Cordis 插件实际运行的是
`plugins/dsh-self-evolving-agent/scripts/serve.py`（单一真源，含相同加固）。
修改本文件的安全逻辑时，须同步到插件副本；否则加固不会作用于生产路径。

用法：
    python scripts/serve.py /path/to/agents.db
    python scripts/serve.py /path/to/agents.db --listen  # TCP 模式

协议（stdin/stdout 行协议）：
    请求: {"jsonrpc":"2.0","id":1,"method":"lookup_exact","params":{"category_id":"network.http_429"}}
    响应: {"jsonrpc":"2.0","id":1,"result":{...}}
    错误: {"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found"}}
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

# 将项目根目录加入 sys.path，确保能 import src 包
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import src.main_agent as main_agent_mod
import src.models as models_mod
import src.offline_planner as planner_mod
import src.pending_queue as queue_mod
import src.storage as storage_mod


class RPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class DomainError(Exception):
    """领域失败 — 返回规范错误而非异常。"""
    def __init__(self, code: str, message: str) -> None:
        self.code = code  # NOT_FOUND / OVERLAP_REJECTED / INVALID_INPUT
        self.message = message
        super().__init__(message)


# 允许经 RPC 暴露的方法白名单（禁止访问私有/任意属性）
_ALLOWED_METHODS = frozenset({
    "init",
    "stats",
    "lookup_exact",
    "lookup_fuzzy",
    "report_unknown",
    "planner_plan",
    "routing_query",
    "routing_rank",
    "routing_split",
    "routing_prune",
    "health",
})

# 读方法：仅查询/观测，无副作用，始终放行（RFC 语义对标 GET/head）
_READ_METHODS = frozenset({
    "stats",
    "lookup_exact",
    "lookup_fuzzy",
    "routing_query",
    "routing_rank",
    "health",
})

# 写方法：变更路由表 / Skill / 暂存队列，需受鉴权与 readonly 约束（对标 POST/PUT/DELETE）
_WRITE_METHODS = frozenset({
    "init",
    "report_unknown",
    "planner_plan",
    "routing_split",
    "routing_prune",
})


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
        return [_serialize(item) for item in sorted(obj)]
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
            "categories": list(
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
        batch_size = params.get("batch_size", 10)
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
            raise DomainError("OVERLAP_REJECTED", str(exc)) from exc
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
            supplied = params.get("auth")
            if supplied != self._token:
                return "Write operation forbidden: missing/invalid auth token"
        return None

    def _handle(
        self, method: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        params = params or {}
        if method not in _ALLOWED_METHODS:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not found",
                },
            }
        denied = self._authorize(method, params)
        if denied is not None:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": denied,
                },
            }
        handler = getattr(self, method, None)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not found",
                },
            }
        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "result": _serialize(result)}
        except DomainError as exc:  # noqa: BLE001
            # P0-2: 领域失败 — 返回 JSON-RPC error 带错误码
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32001,
                    "message": f"{exc.code}: {exc.message}",
                },
            }
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32000,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            }

    def run_stdio(self) -> None:
        """stdin/stdout 行协议模式。"""
        print("__ready__", flush=True)
        self._serve(sys.stdin, lambda s: print(s, flush=True))

    def run_connection(self, conn: Any) -> None:
        """处理一条 TCP 连接：按行协议读取并回写，直到连接关闭。"""
        reader = conn.makefile("r", encoding="utf-8", newline="\n")

        def write_line(s: str) -> None:
            conn.sendall(s.encode("utf-8") + b"\n")

        self._serve(reader, write_line)

    def _serve(self, lines: Any, write: Any) -> None:
        """通用行协议调度：逐行解析 JSON-RPC 请求并回写响应。

        RPC 请求/响应里附带 `auth`（用于写操作鉴权），与 stdio 共用 _handle，
        因此 readonly / token 约束对 stdio 与 TCP 同时生效。
        """
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                write(json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Invalid JSON"},
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
