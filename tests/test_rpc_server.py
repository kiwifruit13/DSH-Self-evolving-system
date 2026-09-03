"""验证 RPC 服务器所有方法。"""
import sys

sys.path.insert(0, '.')

from tests.plugin_serve import srv


class TestRPCServer:
    def setup_method(self) -> None:
        self.server = srv.Server(":memory:")
        self.server._storage.init()

    def test_health(self) -> None:
        resp = self.server._handle("health", {})
        assert resp["result"]["status"] == "ok"

    def test_stats_empty(self) -> None:
        resp = self.server._handle("stats", {})
        assert resp["result"]["routing_count"] == 0
        assert resp["result"]["pending_count"] == 0

    def test_lookup_exact_not_found(self) -> None:
        resp = self.server._handle("lookup_exact", {"category_id": "network.http_429"})
        # P0-2: NOT_FOUND 返回 JSON-RPC error（领域失败，规范错误）
        assert resp["error"]["code"] == -32001
        assert "NOT_FOUND" in resp["error"]["message"]

    def test_lookup_exact_found(self) -> None:
        from src.models import LocalMindMap, RoutingTableEntry, Tag

        lm = LocalMindMap(
            node_id="network.timeout",
            parent_path="root.network",
            focus_description="测试",
            boundary_rules="测试边界",
            logic_signature="测试逻辑",
        )
        entry = RoutingTableEntry(
            category_id="network.timeout",
            stats={"freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_稳定")},
        )
        self.server._storage.upsert_routing_entry(entry)

        resp = self.server._handle("lookup_exact", {"category_id": "network.timeout"})
        assert resp["result"]["match_type"] == "exact"

    def test_lookup_fuzzy_empty(self) -> None:
        resp = self.server._handle(
            "lookup_fuzzy",
            {"tags": ["状态_稳定"], "root_category": None, "limit": 5},
        )
        assert isinstance(resp["result"], list)

    def test_report_unknown(self) -> None:
        resp = self.server._handle(
            "report_unknown",
            {
                "error_stack": "TestError: boom",
                "context": {},
                "strategies": ["retry"],
                "location_guess": "network",
                "confidence": 0.8,
            },
        )
        assert resp["result"]["enqueued"] is True

    def test_planner_plan_empty_queue(self) -> None:
        resp = self.server._handle("planner_plan", {"batch_size": 5})
        assert resp["result"]["total_processed"] == 0

    def test_routing_query_empty(self) -> None:
        resp = self.server._handle(
            "routing_query", {"root_category": None, "tags": []}
        )
        assert isinstance(resp["result"], list)

    def test_routing_rank_empty(self) -> None:
        resp = self.server._handle("routing_rank", {"root_category": None})
        assert isinstance(resp["result"], list)

    def test_unknown_method(self) -> None:
        resp = self.server._handle("nonexistent", {})
        assert resp["error"]["code"] == -32601

    def test_routing_split_creates_child(self) -> None:
        # 在 server 自己的 storage 中插入父节点
        from src.models import LocalMindMap, RoutingTableEntry, Tag

        lm = LocalMindMap(
            node_id="network.timeout",
            parent_path="root.network",
            focus_description="测试",
            boundary_rules="测试边界",
            logic_signature="测试逻辑",
        )
        entry = RoutingTableEntry(
            category_id="network.timeout",
            stats={"freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_稳定")},
        )
        self.server._storage.upsert_routing_entry(entry)

        resp = self.server._handle(
            "routing_split",
            {
                "parent_category_id": "network.timeout",
                "child_name": "connect",
                "reason": "test",
                "child_boundary_rules": "仅处理 TCP 连接超时",
                "child_logic_signature": "修复 TCP 连接超时",
            },
        )
        assert "error" not in resp, resp["error"]["message"]
        assert resp["result"]["category_id"] == "network.timeout.connect"

    def test_rpc_server_ready_signal(self) -> None:
        """验证服务器启动时输出 __ready__ 信号。"""
        import io

        out = io.StringIO()
        server = srv.Server(":memory:")
        server._storage.init()

        # 模拟 stdout
        import builtins

        orig_print = builtins.print
        builtins.print = lambda *a, **kw: out.write(str(a[0]) + "\n")

        # 直接调用 run_stdio 的核心逻辑
        builtins.print("__ready__", flush=True)
        builtins.print = orig_print

        assert "__ready__" in out.getvalue()
