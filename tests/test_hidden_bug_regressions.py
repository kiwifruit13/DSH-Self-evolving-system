"""隐蔽 bug 修复的回归测试。

覆盖本轮排查确认并修复的隐匿 bug：
- T1/T2 tag_query：or_()/end_group() 破坏链式调用，后续条件被静默丢弃
- T3  storage：毒条目导致反馈队列永久阻塞
- T5/T6 sub_agent：重叠被拒后节点仍被持久化
- T7  routing_table：分裂时兄弟校验失败后残留孤立子节点
- T9  storage：upsert 返回连接级累计行数（非本次影响行数）
- T10 offline_planner：批次中间异常导致剩余举证包丢失
- T12 routing_table：合并归一化指标直接相加导致溢出
- T14 storage：SQL LIKE 通配符误匹配
- T16 models：LocalMindMap.from_dict 时间戳退化为字符串
- T17 routing_table：合并中间节点导致孙节点孤立
- R1-R3 serve.py：RPC 未做方法白名单，可访问私有/任意属性
- A   overlap_checker：对已存在节点 check 不排除自身 → 假高重叠 UNCERTAIN
- C   sub_agent_pool：specialized compile_skills 忽略 quality_delta_min（死参数）
"""
from __future__ import annotations

from datetime import datetime

import pytest

import scripts.serve as srv
from src.models import (
    LocalMindMap,
    MaintenanceLog,
    RoutingTableEntry,
    Tag,
    UnclassifiedFailurePackage,
)
from src.offline_planner import OfflinePlanner
from src.pending_queue import PendingQueue
from src.routing_table import RoutingTable
from src.storage import Storage, _escape_like
from src.sub_agent import SubAgent
from src.tag_query import TagQueryBuilder, evaluate_query


@pytest.fixture
def storage(tmp_db_path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


def _make_entry(category_id: str, parent_path: str = "root.network") -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path=parent_path,
        focus_description=f"聚焦 {category_id}",
        boundary_rules=f"仅处理 {category_id}",
        logic_signature="与己比较逻辑",
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 1.0, "impact": 0.5, "trend": 0.0, "recover_cost": 1.0},
        local_map=lm,
        tags=set(),
    )


# ══════════════════════════════════════════════════════════════════
# T1/T2：tag_query 链式调用
# ══════════════════════════════════════════════════════════════════

class TestTagQueryChaining:
    def test_or_keeps_following_conditions(self) -> None:
        """end_group().or_() 之后追加的条件不应丢失。"""
        q = TagQueryBuilder()
        expr = (
            q.must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .must(Tag("场景_第三方依赖"))
            .build()
        )
        assert "groups" in expr
        group_tags = {
            g[0]["must"][0]["tag"] for g in expr["groups"]
        }
        assert group_tags == {"状态_稳定", "场景_第三方依赖"}

    def test_or_query_evaluation(self) -> None:
        q = TagQueryBuilder()
        expr = (
            q.must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .must(Tag("场景_第三方依赖"))
            .build()
        )
        assert evaluate_query({Tag("状态_稳定")}, expr)
        assert evaluate_query({Tag("场景_第三方依赖")}, expr)
        assert not evaluate_query({Tag("代价_低消耗")}, expr)

    def test_plain_and_still_works(self) -> None:
        q = TagQueryBuilder()
        expr = q.must(Tag("状态_稳定")).must_not(Tag("场景_本地计算")).build()
        assert evaluate_query({Tag("状态_稳定")}, expr)
        assert not evaluate_query({Tag("状态_稳定"), Tag("场景_本地计算")}, expr)


# ══════════════════════════════════════════════════════════════════
# T3：毒条目阻塞队列
# ══════════════════════════════════════════════════════════════════

class TestPoisonQueueEntry:
    def test_poison_entry_does_not_block_queue(self, storage: Storage) -> None:
        from src.storage import _now_iso

        pkg = UnclassifiedFailurePackage(
            error_stack="普通错误\nline2",
            context_snapshot={},
            attempted_strategies=["retry"],
            location_guess="network",
            confidence=0.8,
        )
        storage.enqueue_feedback(pkg)
        # 直接注入一条无法反序列化的毒条目
        conn = storage._get_conn()
        conn.execute(
            "INSERT INTO pending_queue (data, created_at) VALUES (?, ?)",
            ("{{{{ not-json", _now_iso()),
        )
        conn.commit()

        # 首次出队：毒条目被跳过，正常包返回，且不抛异常
        items = storage.dequeue_feedback(limit=10)
        assert len(items) == 1
        assert items[0].error_stack == "普通错误\nline2"
        # 队列已清空（毒条目被"占用"，不再永久阻塞）
        assert storage.pending_count() == 0
        assert storage.dequeue_feedback(limit=10) == []


# ══════════════════════════════════════════════════════════════════
# T5/T6：sub_agent 重叠被拒不应持久化
# ══════════════════════════════════════════════════════════════════

class _RejectResult:
    allows_creation = False
    max_overlap = 0.95
    max_overlap_with = "network.other"


class _RejectChecker:
    threshold = 0.7

    def check(self, *args, **kwargs) -> _RejectResult:
        return _RejectResult()


class TestOverlapRejectNotPersisted:
    def test_feedback_overlap_reject_returns_none_and_not_persisted(
        self, storage: Storage
    ) -> None:
        queue = PendingQueue(storage)
        agent = SubAgent(storage, queue)
        agent._checker = _RejectChecker()  # type: ignore[assignment]

        pkg = UnclassifiedFailurePackage(
            error_stack="HTTP 502 Bad Gateway\nstack",
            context_snapshot={},
            attempted_strategies=["reboot"],
            location_guess="network",
            confidence=0.9,
        )
        entry = agent._process_feedback(pkg)
        assert entry is None
        # 被拒节点不得写入路由表
        assert storage.count_routing_entries() == 0


# ══════════════════════════════════════════════════════════════════
# T7：分裂时兄弟校验失败，不得残留孤立子节点
# ══════════════════════════════════════════════════════════════════

class TestSplitNoOrphan:
    def test_sibling_reject_no_child(self, storage: Storage, monkeypatch) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout"))
        rt = RoutingTable(storage)

        def boom(*args, **kwargs):
            raise ValueError("模拟兄弟重叠拒绝")

        monkeypatch.setattr(rt, "_check_sibling_overlap", boom)
        with pytest.raises(ValueError):
            rt.split(
                parent_category_id="network.timeout",
                child_name="connect",
                reason="test",
            )
        # 兄弟校验先于持久化：子节点不得残留
        assert storage.get_routing_entry("network.timeout.connect") is None


# ══════════════════════════════════════════════════════════════════
# T9：upsert 返回本次影响行数
# ══════════════════════════════════════════════════════════════════

class TestUpsertRowCount:
    def test_upsert_routing_entry_rowcount(self, storage: Storage) -> None:
        entry = _make_entry("network.timeout")
        assert storage.upsert_routing_entry(entry) == 1

    def test_upsert_skill_rowcount(self, storage: Storage) -> None:
        from src.models import SpecializedSkill

        skill = SpecializedSkill(skill_id="skill_1", name="测试")
        assert storage.upsert_skill(skill) == 1


# ══════════════════════════════════════════════════════════════════
# T10：offline_planner 批次异常不丢包
# ══════════════════════════════════════════════════════════════════

class TestPlannerRequeue:
    def test_mid_batch_error_requeues(self, storage: Storage, monkeypatch) -> None:
        queue = PendingQueue(storage)
        for i in range(2):
            queue.enqueue(
                UnclassifiedFailurePackage(
                    error_stack=f"错误{i}",
                    context_snapshot={},
                    attempted_strategies=[],
                    location_guess="network",
                    confidence=0.5,
                )
            )
        planner = OfflinePlanner(storage, queue)

        def boom(*args, **kwargs):
            raise RuntimeError("plan 中途失败")

        monkeypatch.setattr(planner, "_plan_single", boom)
        report = planner.plan(batch_size=10)
        assert len(report.errors) == 2
        # 异常包重新入队，未丢失
        assert storage.pending_count() == 2


# ══════════════════════════════════════════════════════════════════
# T12 / T17：合并指标与孙节点重挂
# ══════════════════════════════════════════════════════════════════

class TestMerge:
    def test_merge_stats_no_overflow(self, storage: Storage) -> None:
        parent = _make_entry("network.timeout")
        parent.stats = {"freq": 10.0, "impact": 0.8, "trend": 0.2, "recover_cost": 0.3}
        child = _make_entry("network.timeout.connect", parent_path="network.timeout")
        child.stats = {"freq": 5.0, "impact": 0.9, "trend": 0.4, "recover_cost": 0.6}
        storage.upsert_routing_entry(parent)
        storage.upsert_routing_entry(child)

        rt = RoutingTable(storage)
        rt.merge_into_parent("network.timeout.connect")
        merged = storage.get_routing_entry("network.timeout")
        assert merged is not None
        # freq 累加、归一化指标取较大值，均不溢出
        assert merged.stats["freq"] == 15.0
        assert merged.stats["impact"] == 0.9
        assert merged.stats["trend"] == 0.4
        assert merged.stats["recover_cost"] == 0.6

    def test_merge_reparents_grandchildren(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout"))
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect", "network.timeout")
        )
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect.host", "network.timeout.connect")
        )

        rt = RoutingTable(storage)
        rt.merge_into_parent("network.timeout.connect")

        # 子节点已删除
        assert storage.get_routing_entry("network.timeout.connect") is None
        # 孙节点重挂到父节点，未孤立
        grandchild = storage.get_routing_entry("network.timeout.connect.host")
        assert grandchild is not None
        assert grandchild.local_map.parent_path == "network.timeout"


# ══════════════════════════════════════════════════════════════════
# T14：SQL LIKE 通配符转义
# ══════════════════════════════════════════════════════════════════

class TestLikeEscaping:
    def test_escape_like_escapes_underscore(self) -> None:
        assert _escape_like("场景_第三方依赖") == "场景\\_第三方依赖"

    def test_tag_filter_precise(self, storage: Storage) -> None:
        e1 = _make_entry("network.timeout")
        e1.tags = {Tag("场景_第三方依赖")}
        e2 = _make_entry("network.retry")
        e2.tags = {Tag("场景_内部微服务")}
        storage.upsert_routing_entry(e1)
        storage.upsert_routing_entry(e2)

        matched = storage.query_routing_entries(
            root_category="network", tags={Tag("场景_第三方依赖")}
        )
        assert [e.category_id for e in matched] == ["network.timeout"]


# ══════════════════════════════════════════════════════════════════
# T16：LocalMindMap.from_dict 时间戳恢复为 datetime
# ══════════════════════════════════════════════════════════════════

class TestMaintenanceLogTimestamp:
    def test_from_dict_restores_datetime(self) -> None:
        lm = LocalMindMap(
            node_id="network.timeout",
            parent_path="root.network",
            focus_description="f",
            boundary_rules="b",
            logic_signature="l",
        )
        lm.append_log("create", "测试", "human")
        restored = LocalMindMap.from_dict(lm.to_dict())
        assert len(restored.maintenance_log) == 1
        log = restored.maintenance_log[0]
        assert isinstance(log, MaintenanceLog)
        assert isinstance(log.timestamp, datetime)


# ══════════════════════════════════════════════════════════════════
# R1-R3：RPC 方法白名单
# ══════════════════════════════════════════════════════════════════

class TestRpcMethodWhitelist:
    def setup_method(self) -> None:
        self.server = srv.Server(":memory:")
        self.server._storage.init()

    def test_allowed_method_works(self) -> None:
        resp = self.server._handle("stats", {})
        assert resp["result"]["routing_count"] == 0

    def test_private_method_denied(self) -> None:
        resp = self.server._handle("_handle", {})
        assert resp["error"]["code"] == -32601
        assert "not found" in resp["error"]["message"]

    def test_dunder_method_denied(self) -> None:
        resp = self.server._handle("__class__", {})
        assert resp["error"]["code"] == -32601

    def test_arbitrary_attribute_denied(self) -> None:
        resp = self.server._handle("_storage", {})
        assert resp["error"]["code"] == -32601


# ══════════════════════════════════════════════════════════════════
# 第二轮：overlap_audit 对库中节点自查产生的假高重叠（A）
# ══════════════════════════════════════════════════════════════════

class TestOverlapAuditSelfExclusion:
    def test_single_node_audit_produces_no_self_pair(self, storage: Storage) -> None:
        e = _make_entry("network.timeout")
        storage.upsert_routing_entry(e)
        agent = SubAgent(storage, PendingQueue(storage))
        pairs = agent.overlap_audit()
        # 单个节点，无其它可比对象 → 不得因自比较 self-overlap 产出高重叠对
        assert pairs == []

    def test_two_distinct_nodes_still_compared(self, storage: Storage) -> None:
        e1 = _make_entry("network.a")
        e1.local_map.boundary_rules = "仅处理 A"
        e1.local_map.logic_signature = "AAA"
        e2 = _make_entry("network.b")
        e2.local_map.boundary_rules = "仅处理 B"
        e2.local_map.logic_signature = "BBB"
        storage.upsert_routing_entry(e1)
        storage.upsert_routing_entry(e2)

        agent = SubAgent(storage, PendingQueue(storage))
        pairs = agent.overlap_audit()
        # 两个完全不重叠的节点：排除自比较后不应产生高重叠对
        assert pairs == []
        # 且不应存在 a↔a 的自对
        for p in pairs:
            assert p["category_a"] != p["category_b"]


# ══════════════════════════════════════════════════════════════════
# 第二轮：SpecializedSubAgent.compile_skills 忽略 quality_delta_min
# ══════════════════════════════════════════════════════════════════

def _low_quality_entry(category_id: str) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="root.network",
        focus_description="聚焦 xxx 修复",
        boundary_rules="仅处理 xxx",
        logic_signature="待优化",
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 5.0, "impact": 0.9, "trend": 0.5, "recover_cost": 0.2},
        local_map=lm,
        tags={Tag("状态_实验性")},
    )


class TestSpecializedCompileQualityGate:
    def test_specialized_respects_delta_min(self, storage: Storage) -> None:
        from src.sub_agent_pool import SpecializedSubAgent

        storage.upsert_routing_entry(_low_quality_entry("network.low"))
        spec = SpecializedSubAgent("network", storage)
        compiled = spec.compile_skills(top_k=5, quality_delta_min=0.9)
        assert compiled == []

    def test_specialized_compiles_high_quality(self, storage: Storage) -> None:
        from src.sub_agent_pool import SpecializedSubAgent

        lm = LocalMindMap(
            node_id="network.retry",
            parent_path="root.network",
            focus_description="HTTP 429 指数退避重试",
            boundary_rules="若收到 429 则指数退避重试 3 次，触发熔断则降级",
            logic_signature="指数退避 + 熔断降级",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            "network.retry",
            {"freq": 5.0, "impact": 0.9, "trend": 0.5, "recover_cost": 0.2},
            lm, {Tag("状态_实验性")},
        ))
        spec = SpecializedSubAgent("network", storage)
        compiled = spec.compile_skills(top_k=5, quality_delta_min=0.1)
        # 高质量节点(429/指数退避/熔断/降级/重试3次)通过门禁，编译成功
        assert len(compiled) == 1
        assert compiled[0].skill_id == "skill_network_retry"
        # entry 已回填 primary_skill_id
        assert storage.get_routing_entry("network.retry").primary_skill_id == "skill_network_retry"


# ══════════════════════════════════════════════════════════════════
# 第三轮：consume_pending 出队即丢（异常包未重新入队）
# ══════════════════════════════════════════════════════════════════

class TestConsumePendingNoLoss:
    def test_failed_package_requeued(self, storage: Storage, monkeypatch) -> None:
        queue = PendingQueue(storage)
        for i in range(2):
            queue.enqueue(
                UnclassifiedFailurePackage(
                    error_stack=f"错误{i}",
                    context_snapshot={},
                    attempted_strategies=[],
                    location_guess="network",
                    confidence=0.5,
                )
            )
        agent = SubAgent(storage, queue)
        # 每次处理都抛异常 → 包应被重新入队而非丢失
        monkeypatch.setattr(
            agent, "_process_feedback",
            lambda pkg: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        res = agent.consume_pending(batch_size=10)
        assert len(res.errors) == 2
        # dequeue 已标记 processed，异常后重新入队 → 队列不应为空
        assert storage.pending_count() == 2


# ══════════════════════════════════════════════════════════════════
# Phase 15 P0①：RPC 访问边界（readonly / token / 读写分组）
# Gherkin per AGENTS.md：正常 / 异常 / 边界 / 权限
# ══════════════════════════════════════════════════════════════════

class TestRpcAccessBoundary:
    def _new(self, readonly: bool = False, token: str | None = None) -> srv.Server:
        server = srv.Server(":memory:", readonly=readonly, token=token)
        server._storage.init()
        return server

    # 权限：readonly 拒绝写方法
    def test_readonly_denies_write(self) -> None:
        server = self._new(readonly=True)
        assert server._handle("stats", {})["result"]["routing_count"] == 0
        resp = server._handle("report_unknown", {"error_stack": "x"})
        assert resp["error"]["code"] == -32600
        assert "read-only" in resp["error"]["message"]

    # 权限：readonly 下读方法照常放行
    def test_readonly_allows_read(self) -> None:
        server = self._new(readonly=True)
        assert server._handle("lookup_exact", {"category_id": "network.x"})["error"]["code"] == -32001
        assert server._handle("health", {})["result"]["status"] == "ok"

    # 权限：token 模式下写命令需携带 auth，读放行
    def test_token_requires_auth_for_write(self) -> None:
        server = self._new(token="secret")
        # 读方法无需 token
        assert server._handle("stats", {})["result"]["routing_count"] == 0
        # 写方法缺 auth → 拒绝
        resp = server._handle("report_unknown", {"error_stack": "x"})
        assert resp["error"]["code"] == -32600
        # 写方法带错误 auth → 拒绝
        resp2 = server._handle("report_unknown", {"error_stack": "x", "auth": "wrong"})
        assert resp2["error"]["code"] == -32600
        # 写方法带正确 auth → 放行
        resp3 = server._handle("report_unknown", {"error_stack": "x", "auth": "secret"})
        assert resp3.get("result") == {"enqueued": True}

    # 正常：无鉴权配置时写方法照常（兼容既有测试与内部 stdio 信任）
    def test_no_auth_write_allowed_by_default(self) -> None:
        server = self._new()
        resp = server._handle("report_unknown", {"error_stack": "x"})
        assert resp.get("result") == {"enqueued": True}

    # 边界：白名单外方法仍被拒（鉴权不绕过白名单；-32601 优先后于 -32600）
    def test_whitelist_takes_precedence(self) -> None:
        server = self._new(token="secret")
        resp = server._handle("_storage", {"auth": "secret"})
        assert resp["error"]["code"] == -32601

    # 端到端：stdio 行协议经 _serve 后 readonly 仍生效
    def test_serve_respects_readonly(self, tmp_path) -> None:
        server = self._new(readonly=True)
        out: list[str] = []

        def write_line(s: str) -> None:
            out.append(s)

        import json as _json
        server._serve(iter([
            _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "report_unknown", "params": {"error_stack": "x"}}),
        ]), write_line)
        resp = _json.loads(out[0])
        assert resp["error"]["code"] == -32600
        assert resp["id"] == 1


# ══════════════════════════════════════════════════════════════════
# 配置核查修复：插件 serve.py 为真源，加固须与之一致（双副本漂移回归）
# ══════════════════════════════════════════════════════════════════

class TestPluginServeHardeningSynced:
    def test_plugin_serve_has_whitelist_and_auth(self) -> None:
        import runpy

        ns = runpy.run_path(
            "plugins/dsh-self-evolving-agent/scripts/serve.py",
            run_name="__plugin_serve_under_test__",
        )
        server_cls = ns["Server"]
        s = server_cls(":memory:", readonly=True)
        s._storage.init()
        # 白名单：私有属性被拒（-32601 先于 -32600）
        r = s._handle("_storage", {})
        assert r["error"]["code"] == -32601
        # readonly：写方法拒绝
        r2 = s._handle("report_unknown", {"error_stack": "x"})
        assert r2["error"]["code"] == -32600
        # 读方法放行
        assert s._handle("stats", {})["result"]["routing_count"] == 0

    def test_plugin_serve_token_enforced(self) -> None:
        import runpy

        ns = runpy.run_path(
            "plugins/dsh-self-evolving-agent/scripts/serve.py",
            run_name="__plugin_serve_under_test__",
        )
        server_cls = ns["Server"]
        s = server_cls(":memory:", token="secret")
        s._storage.init()
        assert s._handle("report_unknown", {"error_stack": "x"})["error"]["code"] == -32600
        with_auth = s._handle("report_unknown", {"error_stack": "x", "auth": "secret"})
        assert with_auth.get("result") == {"enqueued": True}


# ══════════════════════════════════════════════════════════════════
# Phase 15 P1②：树操作事务化 —— orphan_audit + delete_force(迭代)
# ══════════════════════════════════════════════════════════════════

class TestOrphanAudit:
    def test_clean_tree_no_false_positive(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect", "network.timeout")
        )
        rt = RoutingTable(storage)
        assert rt.orphan_audit() == []

    def test_detects_orphan_parent(self, storage: Storage) -> None:
        # parent_path 指向不存在的真实父节点
        e = _make_entry("network.timeout.connect", "network.missing_parent")
        storage.upsert_routing_entry(e)
        rt = RoutingTable(storage)
        orphans = rt.orphan_audit()
        assert any(o["type"] == "orphan_parent" and o["referenced_id"] == "network.missing_parent"
                   for o in orphans)

    def test_detects_orphan_skill(self, storage: Storage) -> None:

        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        # 关联一个不存在的 skill_id
        e = storage.get_routing_entry("network.timeout")
        e.primary_skill_id = "skill_ghost"
        storage.upsert_routing_entry(e)
        rt = RoutingTable(storage)
        orphans = rt.orphan_audit()
        assert any(o["type"] == "orphan_skill" and o["referenced_id"] == "skill_ghost"
                   for o in orphans)

    def test_virtual_root_parent_not_reported(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        rt = RoutingTable(storage)
        assert rt.orphan_audit() == []


class TestDeleteForceIterative:
    def test_deletes_whole_subtree(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect", "network.timeout")
        )
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect.host", "network.timeout.connect")
        )
        rt = RoutingTable(storage)
        assert rt.delete_force("network.timeout") is True
        assert storage.get_routing_entry("network.timeout") is None
        assert storage.get_routing_entry("network.timeout.connect") is None
        assert storage.get_routing_entry("network.timeout.connect.host") is None

    def test_deep_tree_no_stack_overflow(self, storage: Storage) -> None:
        # 构造超深链（远大于默认递归上限 1000 的深度），验证迭代实现不爆栈且整链可删
        rt = RoutingTable(storage)
        parent = "root.network"
        prev = None
        chain_top = None
        for depth in range(1, 1200):
            cid = "network." + "d" * depth
            storage.upsert_routing_entry(_make_entry(cid, parent if prev is None else prev))
            if chain_top is None:
                chain_top = cid
            parent = cid
            prev = cid
        # 删除链顶应删掉整条 1199 节点链
        assert rt.delete_force(chain_top) is True
        assert storage.count_routing_entries() == 0


# ══════════════════════════════════════════════════════════════════
# Phase 15 P2③：契约测试守序列化 —— round-trip + Tag.coerce 容错
# ══════════════════════════════════════════════════════════════════

class TestModelRoundTrip:
    def test_local_mind_map_round_trip(self) -> None:
        lm = LocalMindMap("network.timeout", "root.network", "聚焦超时",
                          "仅处理 HTTP 超时", "指数退避重试")
        lm.append_log("split", "测试分裂", "sub_agent")
        restored = LocalMindMap.from_dict(lm.to_dict())
        assert restored.to_dict() == lm.to_dict()

    def test_routing_entry_round_trip(self) -> None:
        entry = _make_entry("network.timeout", "root.network")
        entry.tags = {Tag("状态_实验性"), Tag("场景_第三方依赖")}
        entry.primary_skill_id = "skill_x"
        restored = RoutingTableEntry.from_dict(entry.to_dict())
        assert restored.to_dict() == entry.to_dict()

    def test_specialized_skill_round_trip(self) -> None:
        from src.models import SkillStep, SpecializedSkill

        lm = LocalMindMap("skill_network_timeout", "network.timeout", "聚焦",
                          "边界", "逻辑")
        step = SkillStep("precheck", "前置校验", lm, "pre", "post", {"max_retries": 3})
        skill = SpecializedSkill("skill_network_timeout", "NetworkTimeoutSkill",
                                 overview_map=lm, steps=[step],
                                 tools=["retry"], context_keys=["http_config"],
                                 tags={Tag("状态_实验性")})
        restored = SpecializedSkill.from_dict(skill.to_dict())
        assert restored.to_dict() == skill.to_dict()

    def test_failure_package_round_trip(self) -> None:
        pkg = UnclassifiedFailurePackage(
            error_stack="GraphQL: Field not found",
            context_snapshot={"target": "example.com"},
            attempted_strategies=["retry", "fallback"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        restored = UnclassifiedFailurePackage.from_dict(pkg.to_dict())
        assert restored.to_dict() == pkg.to_dict()
        # 时间戳必须是 datetime（非字符串）
        from datetime import datetime
        assert isinstance(restored.timestamp, datetime)


class TestTagCoerceLenient:
    def test_coerce_keeps_unknown_body(self) -> None:
        from src.models import TagPrefix

        # 历史/超纲本体不再抛错，保留原值
        t = Tag.coerce("状态_待观察")  # 待观察 不在 _VALID_TAG_VALUES
        assert t is not None
        assert t.value == "状态_待观察"
        assert t.prefix == TagPrefix.STATUS

    def test_coerce_none_for_bare_value(self) -> None:
        assert Tag.coerce("") is None
        assert Tag.coerce("裸标签") is None  # 无合法前缀

    def test_strict_constructor_still_validates(self) -> None:
        # 新数据写入仍用严格构造，超纲抛错不被放松
        with pytest.raises(ValueError):
            Tag("状态_待观察")

    def test_from_dict_tolerates_old_tag(self) -> None:
        # 含过期标签的老数据能反序列化，且合法标签保留
        data = _make_entry("network.timeout", "root.network").to_dict()
        data["tags"] = ["状态_废弃", "场景_旧值已淘汰"]
        entry = RoutingTableEntry.from_dict(data)
        assert Tag("状态_废弃") in entry.tags  # 白名单内标签保留
        assert Tag.coerce("场景_旧值已淘汰") in entry.tags  # 超纲标签宽容保留

    def test_row_to_entry_tolerates_old_tag(self, storage: Storage) -> None:
        # SQL 行还原路径同样容错（storage._row_to_entry）
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        conn = storage._get_conn()
        conn.execute(
            "UPDATE routing_table SET tags = '状态_稳定,场景_已淘汰值' "
            "WHERE category_id = 'network.timeout'"
        )
        conn.commit()
        entry = storage.get_routing_entry("network.timeout")
        assert Tag.coerce("状态_稳定") in entry.tags
        assert Tag.coerce("场景_已淘汰值") in entry.tags


# ══════════════════════════════════════════════════════════════════
# Phase 15 P2③ 收尾 · Step 112：stats 缺失键默认值契约
# 锁定 score_with_breakdown / compute_priority 对缺失键的口径，防漂移
# 默认口径：freq→0, impact→0, trend→0.5(映射), recover_cost→1.0(零代价), sample→0
# ══════════════════════════════════════════════════════════════════

class TestScoreDefaultsContract:
    def _score_calc(self):
        from src.scoring import ScoreCalculator
        return ScoreCalculator()

    def test_compute_priority_empty_stats(self) -> None:
        sc = self._score_calc()
        # 默认权重 0.25/0.35/0.20/0.20；trend缺→0.5, cost缺→1.0
        # = 0*0.25 + 0*0.35 + 0.5*0.20 + 1.0*0.20 = 0.30
        assert abs(sc.compute_priority({}) - 0.30) < 1e-9

    def test_score_with_breakdown_empty_stats_matches_priority(self) -> None:
        from src.scoring import ScoreCalculator
        sc = ScoreCalculator()
        entry = _make_entry("network.x", "root.network")
        entry.stats = {}  # 全缺
        bd = sc.score_with_breakdown(entry)
        assert abs(bd.priority - 0.30) < 1e-9
        # 无 last_seen → days=0 → 无衰减
        assert bd.decay_factor == 1.0
        assert abs(bd.final_score - 0.30) < 1e-9

    def test_missing_cost_defaults_to_max_cost_score(self) -> None:
        sc = self._score_calc()
        # recover_cost 缺失 → 归一化 cost = 1.0（视为零代价，最高分）
        assert sc.normalize_cost(0.0) == 1.0
        # 只有 freq 提供，其余全缺
        assert abs(sc.compute_priority({"freq": 1000.0}) - sc.compute_priority({})) > 0

    def test_freq_max_clamps_above_max(self) -> None:
        sc = self._score_calc()
        assert sc.normalize_freq(100000.0) == 1.0  # 超 freq_max 被钳制到 1.0

    def test_sample_missing_count_means_zero(self) -> None:
        sc = self._score_calc()
        # sample_count 缺失 → 视为 0 → sample_aware_impact 收缩
        impact_n, conf, penalty = sc.sample_aware_impact(0.9, 0)
        assert conf == 0.0
        assert penalty == 1.0
        assert impact_n < 0.9


# ══════════════════════════════════════════════════════════════════
# Phase 15 P3④ · Step 113：overlap_audit 完整用例
# ══════════════════════════════════════════════════════════════════

def _high_overlap_entry(category_id: str) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id, parent_path="root.network",
        focus_description="聚焦 HTTP 429", boundary_rules="仅处理 HTTP 429",
        logic_signature="指数退避重试",
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 1.0, "impact": 0.5, "trend": 0.0, "recover_cost": 1.0},
        local_map=lm, tags={Tag("状态_实验性")},
    )


class TestOverlapAuditComplete:
    def _agent(self, storage: Storage) -> SubAgent:
        return SubAgent(storage, PendingQueue(storage))

    def test_marks_high_overlap_pairs(self, storage: Storage) -> None:
        for cid in ("network.a", "network.b", "network.c"):
            storage.upsert_routing_entry(_high_overlap_entry(cid))
        pairs = self._agent(storage).overlap_audit()
        assert len(pairs) >= 1
        # 无 a↔a 自对
        for p in pairs:
            assert p["category_a"] != p["category_b"]

    def test_cross_root_not_reported(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_high_overlap_entry("network.a"))
        # 不同根分类的节点不应与 network 互挡
        dlm = LocalMindMap(
            node_id="data_parsing.x", parent_path="root.data_parsing",
            focus_description="聚焦 X", boundary_rules="仅处理 HTTP 429",
            logic_signature="指数退避重试",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            "data_parsing.x",
            {"freq": 1.0, "impact": 0.5, "trend": 0.0, "recover_cost": 1.0},
            dlm, {Tag("状态_实验性")},
        ))
        pairs = self._agent(storage).overlap_audit()
        for p in pairs:
            assert p["category_a"].split(".")[0] == p["category_b"].split(".")[0]


# ══════════════════════════════════════════════════════════════════
# Phase 15 P3④ · Step 116：导航完整性不变式测试
# 任意 split/merge/delete 后：无孤儿 parent、无悬空 skill、树深有界
# ══════════════════════════════════════════════════════════════════

class TestNavigationIntegrity:
    # -- 不变式：任意操作后 orphan_audit 应为空 --
    def test_no_orphan_after_split(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        rt = RoutingTable(storage)
        rt.split("network.timeout", "connect", "test", actor="sub_agent",
                 child_boundary_rules="仅处理连接", child_logic_signature="连接")
        assert rt.orphan_audit() == []

    def test_no_orphan_after_merge(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect", "network.timeout")
        )
        storage.upsert_routing_entry(
            _make_entry("network.timeout.connect.host", "network.timeout.connect")
        )
        rt = RoutingTable(storage)
        rt.merge_into_parent("network.timeout.connect")
        # 孙节点应被 reparent 到父，无孤儿
        assert rt.orphan_audit() == []
        gc = storage.get_routing_entry("network.timeout.connect.host")
        assert gc is not None and gc.local_map.parent_path == "network.timeout"

    def test_no_orphan_after_delete_force(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.marked", "root.network"))
        rt = RoutingTable(storage)
        # 删除不存在的节点不应引入孤儿（no-op）
        rt.delete_force("network.ghost")
        assert rt.orphan_audit() == []

    # -- 不变式：树深有界 --
    def test_tree_depth_bounded(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.timeout", "root.network"))
        rt = RoutingTable(storage)
        # 3 段（network.timeout.connect）为 MAX_SPLIT_DEPTH=3 上限，允许
        rt.split("network.timeout", "connect", "test", actor="sub_agent",
                 child_boundary_rules="仅处理连接", child_logic_signature="连接")
        # 续层到 4 段 → 超过深度上限 → 拒绝，层级守住上界
        with pytest.raises(ValueError):
            rt.split("network.timeout.connect", "host", "test", actor="sub_agent",
                     child_boundary_rules="仅处理 host", child_logic_signature="host")

    # -- 不变式：有效引用无悬空（skill 存在时不报）--
    def test_valid_skill_not_reported(self, storage: Storage) -> None:
        from src.models import SpecializedSkill

        skill = SpecializedSkill(skill_id="skill_retry", name="Retry")
        storage.upsert_skill(skill)
        e = _make_entry("network.timeout", "root.network")
        e.primary_skill_id = "skill_retry"
        storage.upsert_routing_entry(e)
        rt = RoutingTable(storage)
        assert rt.orphan_audit() == []


# ══════════════════════════════════════════════════════════════════
# Phase 15 P3④ · Step 114：serve.py TCP（run_connection）行协议
# ══════════════════════════════════════════════════════════════════

class TestRpcTcpMode:
    def test_tcp_roundtrip_and_authorization(self) -> None:
        import json as _json
        import socket
        import threading

        server = srv.Server(":memory:", readonly=True)
        server._storage.init()
        a, b = socket.socketpair()

        def serve_pair() -> None:
            try:
                server.run_connection(a)
            finally:
                a.close()

        t = threading.Thread(target=serve_pair, daemon=True)
        t.start()
        try:
            # 发送一个写请求（readonly 应拒绝 -32600）
            b.sendall(_json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "report_unknown", "params": {"error_stack": "x"}}
            ).encode() + b"\n")
            b.shutdown(socket.SHUT_WR)
            data = b.recv(4096)
            resp = _json.loads(data)
            assert resp["error"]["code"] == -32600
            assert resp["id"] == 1
        finally:
            b.close()
            t.join(timeout=2)
