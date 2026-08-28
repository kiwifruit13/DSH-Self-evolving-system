"""路由表模块单元测试 — CRUD / 排序 / 分裂 / 剪枝。"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.routing_table import RoutingTable, SplitRejectedError
from src.storage import Storage


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def rt(storage: Storage) -> RoutingTable:
    return RoutingTable(storage)


def _make_entry(
    category_id: str,
    tags: set[Tag] | None = None,
    parent_path: str = "root",
    stats: dict | None = None,
    boundary_rules: str = "仅处理测试场景",
    logic_signature: str = "测试逻辑",
    focus_description: str = "聚焦测试",
) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path=parent_path,
        focus_description=focus_description,
        boundary_rules=boundary_rules,
        logic_signature=logic_signature,
    )
    lm.append_log("create", "初始创建", "human")
    return RoutingTableEntry(
        category_id=category_id,
        stats=stats or {"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
        local_map=lm,
        tags=tags or {Tag("状态_实验性"), Tag("场景_第三方依赖")},
        primary_skill_id="skill_test",
    )


# ══════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableCRUD:
    def test_insert_and_get(self, rt: RoutingTable) -> None:
        entry = _make_entry("network.timeout.read")
        rt.insert(entry)
        retrieved = rt.get("network.timeout.read")
        assert retrieved is not None
        assert retrieved.category_id == "network.timeout.read"

    def test_update(self, rt: RoutingTable) -> None:
        entry = _make_entry("network.timeout.read")
        rt.insert(entry)

        entry.stats = {"freq": 200, "impact": 0.95, "trend": 0.5, "recover_cost": 1}
        rt.update(entry)

        retrieved = rt.get("network.timeout.read")
        assert retrieved is not None
        assert retrieved.stats["freq"] == 200

    def test_delete(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.timeout.read"))
        assert rt.delete("network.timeout.read") is True
        assert rt.get("network.timeout.read") is None

    def test_count(self, rt: RoutingTable) -> None:
        assert rt.count() == 0
        rt.insert(_make_entry("network.a"))
        rt.insert(_make_entry("network.b"))
        assert rt.count() == 2

    def test_get_nonexistent(self, rt: RoutingTable) -> None:
        assert rt.get("nonexistent") is None


# ══════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableQuery:
    def test_query_by_root(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.timeout.read"))
        rt.insert(_make_entry("data_parsing.graphql"))
        results = rt.query(root_category="network")
        assert len(results) == 1
        assert results[0].category_id == "network.timeout.read"

    def test_query_all(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.a"))
        rt.insert(_make_entry("data_parsing.b"))
        assert len(rt.query_all()) == 2


# ══════════════════════════════════════════════════════════════════
# 排序
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableRank:
    def test_rank_descending(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.high", stats={
            "freq": 500, "impact": 0.95, "trend": 0.8, "recover_cost": 1
        }))
        rt.insert(_make_entry("network.low", stats={
            "freq": 5, "impact": 0.3, "trend": -0.5, "recover_cost": 8
        }))
        ranked = rt.rank()
        assert ranked[0].category_id == "network.high"
        assert ranked[1].category_id == "network.low"

    def test_top_k(self, rt: RoutingTable) -> None:
        for i in range(5):
            rt.insert(_make_entry(f"network.node_{i}", stats={
                "freq": (i + 1) * 50, "impact": 0.5 + i * 0.1, "trend": 0.0, "recover_cost": 2
            }))
        top2 = rt.top_k(k=2)
        assert len(top2) == 2
        assert top2[0].final_score >= top2[1].final_score


# ══════════════════════════════════════════════════════════════════
# 分裂
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableSplit:
    def test_split_creates_child(self, rt: RoutingTable) -> None:
        parent = _make_entry("network.timeout")
        parent.tags = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        rt.insert(parent)

        child = rt.split(
            parent_category_id="network.timeout",
            child_name="connect",
            reason="连接超时占比过高，需要单独处理",
            actor="sub_agent",
            child_boundary_rules="仅处理 TCP 连接超时，不处理读超时",
            child_overrides={Tag("代价_低消耗")},  # 覆盖代价标签
        )

        assert child.category_id == "network.timeout.connect"
        assert child.local_map.parent_path == "network.timeout"
        assert child.local_map.boundary_rules == "仅处理 TCP 连接超时，不处理读超时"
        # 标签变异：代价_高延迟 被覆盖为 代价_低消耗
        assert Tag("代价_低消耗") in child.tags
        assert Tag("代价_高延迟") not in child.tags
        # 遗传：状态和场景保留
        assert Tag("状态_实验性") in child.tags
        assert Tag("场景_第三方依赖") in child.tags

        # 父节点 maintenance_log 应有 split 记录
        parent_retrieved = rt.get("network.timeout")
        assert parent_retrieved is not None
        actions = [log.action for log in parent_retrieved.local_map.maintenance_log]
        assert "split" in actions

    def test_split_child_already_exists(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.timeout"))
        rt.insert(_make_entry("network.timeout.connect"))
        with pytest.raises(ValueError, match="已存在"):
            rt.split("network.timeout", "connect", "test")

    def test_split_parent_not_found(self, rt: RoutingTable) -> None:
        with pytest.raises(ValueError, match="不存在"):
            rt.split("nonexistent", "child", "test")


# ══════════════════════════════════════════════════════════════════
# 剪枝
# ══════════════════════════════════════════════════════════════════

class TestRoutingTablePrune:
    def test_prune_returns_low_scorers(self, rt: RoutingTable) -> None:
        rt.insert(_make_entry("network.good", stats={
            "freq": 500, "impact": 0.95, "trend": 0.5, "recover_cost": 1
        }))
        rt.insert(_make_entry("network.bad", stats={
            "freq": 1, "impact": 0.05, "trend": -0.9, "recover_cost": 9
        }))
        rt.insert(_make_entry("network.terrible", stats={
            "freq": 0, "impact": 0.0, "trend": -1.0, "recover_cost": 99
        }))

        candidates = rt.prune_lowest(threshold=0.2, bottom_pct=0.5)
        # "network.terrible" 得分最低，应被标记
        target_ids = [p.target_id for p in candidates]
        assert "network.terrible" in target_ids

    def test_prune_empty_table(self, rt: RoutingTable) -> None:
        assert rt.prune_lowest() == []


# ══════════════════════════════════════════════════════════════════
# P0 修复回归测试
# ══════════════════════════════════════════════════════════════════

class TestInsertIdempotency:
    """insert() 保证互斥——已存在则报错"""

    def test_insert_existing_raises(self, rt: RoutingTable) -> None:
        entry = _make_entry("network.test")
        rt.insert(entry)
        with pytest.raises(ValueError, match="已存在"):
            rt.insert(entry)

    def test_update_existing_succeeds(self, rt: RoutingTable) -> None:
        entry = _make_entry("network.test")
        rt.update(entry)
        # 再次 update 成功
        updated = _make_entry("network.test", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(updated)
        assert rt.get("network.test").stats["freq"] == 100


class TestSplitOverlapValidation:
    """split() 执行重叠校验——语义重复时拒绝"""

    def test_split_rejected_when_overlap_high(
        self, storage: Storage, rt: RoutingTable
    ) -> None:
        from src.overlap_checker import OverlapChecker

        # 预置同级节点
        parent = _make_entry("network.timeout", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(parent)

        existing_child = _make_entry("network.timeout.read", stats={
            "freq": 0, "impact": 0, "trend": 0, "recover_cost": 0
        })
        existing_child.local_map.boundary_rules = "仅处理 HTTP 超时"
        existing_child.local_map.logic_signature = "修复 HTTP 超时"
        rt.update(existing_child)

        # 创建一个新的 OverlapChecker（确保使用当前配置）
        checker = OverlapChecker(storage)
        rt._overlap_checker = checker

        with pytest.raises(Exception, match="拒绝|overlap|重叠"):
            rt.split(
                "network.timeout",
                "read2",
                "测试重叠",
                child_boundary_rules="仅处理 HTTP 超时",
                child_logic_signature="修复 HTTP 超时",
            )

    def test_split_rejected_when_exceeds_depth(self, rt: RoutingTable) -> None:
        parent = _make_entry("network.timeout", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(parent)
        child = _make_entry("network.timeout.connect", stats={
            "freq": 0, "impact": 0, "trend": 0, "recover_cost": 0
        })
        rt.update(child)

        with pytest.raises(ValueError, match="深度"):
            rt.split("network.timeout.connect", "socket", "超深")

    def test_split_zero_stats(self, rt: RoutingTable) -> None:
        """Step 42：子节点继承父节点 stats 的 30%。"""
        parent = _make_entry("network.timeout", stats={
            "freq": 200, "impact": 0.95, "trend": 0.1, "recover_cost": 1
        })
        rt.update(parent)

        child = rt.split(
            "network.timeout",
            "connect",
            "测试",
            child_boundary_rules="仅处理 TCP 连接超时",
            child_logic_signature="修复 TCP 连接超时",
        )
        # 子节点 freq = parent.freq * 0.3
        assert abs(child.stats["freq"] - 60.0) < 0.01
        # impact 从父节点继承
        assert abs(child.stats["impact"] - 0.95) < 0.01
        # trend 从零开始
        assert child.stats["trend"] == 0.0
        # recover_cost 从父节点继承
        assert abs(child.stats["recover_cost"] - 1.0) < 0.01

        # 父节点 freq 应减少 30%
        updated_parent = rt.get("network.timeout")
        assert updated_parent is not None
        assert abs(updated_parent.stats["freq"] - 140.0) < 0.01


class TestMergeIntoParent:
    """merge_into_parent() 实际合并"""

    def test_merge_combines_stats_and_tags(self, rt: RoutingTable) -> None:
        parent = _make_entry("network.timeout", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(parent)

        child = _make_entry("network.timeout.read", stats={
            "freq": 50, "impact": 0.5, "trend": 0.0, "recover_cost": 2
        })
        child.local_map.parent_path = "network.timeout"
        child.tags = {Tag("状态_实验性")}
        rt.update(child)

        plan = rt.merge_into_parent("network.timeout.read", "剪枝")
        assert plan.target_id == "network.timeout.read"
        assert plan.parent_id == "network.timeout"

        merged_parent = rt.get("network.timeout")
        assert merged_parent is not None
        assert merged_parent.stats["freq"] == 150
        assert "状态_实验性" in [t.value for t in merged_parent.tags]

        # 子节点已删除
        assert rt.get("network.timeout.read") is None

    def test_merge_no_parent_raises(self, rt: RoutingTable) -> None:
        child = _make_entry("network.timeout.read")
        child.local_map.parent_path = "network.timeout"
        rt.update(child)

        with pytest.raises(ValueError, match="父节点"):
            rt.merge_into_parent("network.timeout.read")


class TestPruneExecutes:
    """prune_lowest(execute=True) 实际合并"""

    def test_prune_merges_and_deletes(self, rt: RoutingTable) -> None:
        parent = _make_entry("network.timeout", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(parent)
        child = _make_entry("network.timeout.bad", stats={
            "freq": 0, "impact": 0.0, "trend": -1.0, "recover_cost": 99
        })
        child.local_map.parent_path = "network.timeout"
        rt.update(child)

        plans = rt.prune_lowest(threshold=0.5, bottom_pct=0.5, execute=True)
        assert len(plans) >= 1
        target_ids = [p.target_id for p in plans]
        assert "network.timeout.bad" in target_ids

        assert rt.get("network.timeout.bad") is None

    def test_prune_execute_false_only_plans(self, rt: RoutingTable) -> None:
        parent = _make_entry("network.timeout", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1
        })
        rt.update(parent)
        child = _make_entry("network.timeout.bad", stats={
            "freq": 0, "impact": 0.0, "trend": -1.0, "recover_cost": 99
        })
        child.local_map.parent_path = "network.timeout"
        rt.update(child)

        plans = rt.prune_lowest(threshold=0.5, bottom_pct=0.5, execute=False)
        assert len(plans) >= 1


# ══════════════════════════════════════════════════════════════════
# Step 83: 同级兄弟重叠检测
# ══════════════════════════════════════════════════════════════════


class TestSiblingOverlap:
    """验证 Step 83：split() 同级兄弟重叠检测。"""

    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()
        self.rt = RoutingTable(self.storage)

    def test_split_sibling_overlap_rejected(self) -> None:
        """父节点已有两个重叠的同级子节点，再分裂重叠子节点应被拒绝。"""
        # 创建父节点
        parent = _make_entry(
            "network.http_429",
            parent_path="root.network",
            stats={"freq": 50, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
        )
        parent.local_map.boundary_rules = "处理所有 HTTP 429 限流错误"
        parent.local_map.logic_signature = "退避重试策略"
        self.rt.update(parent)

        # 创建同级兄弟 A
        sibling_a = _make_entry(
            "network.http_429.retry",
            parent_path="network.http_429",
            stats={"freq": 5, "impact": 0.6, "trend": 0.0, "recover_cost": 1},
        )
        sibling_a.local_map.boundary_rules = "处理退避重试逻辑"
        sibling_a.local_map.logic_signature = "指数退避重试策略"
        self.rt.update(sibling_a)

        # 尝试分裂重叠的同级兄弟 B
        with pytest.raises(SplitRejectedError):
            self.rt.split(
                parent_category_id="network.http_429",
                child_name="retry_backoff",
                reason="测试",
                child_boundary_rules="处理退避重试逻辑",
                child_logic_signature="指数退避重试策略",
            )

    def test_split_sibling_no_overlap_allowed(self) -> None:
        """父节点已有子节点，但候选子节点边界不同，应允许分裂。"""
        parent = _make_entry(
            "network.timeout",
            parent_path="root.network",
            stats={"freq": 30, "impact": 0.7, "trend": 0.0, "recover_cost": 1},
            boundary_rules="处理所有网络超时错误",
            logic_signature="网络超时修复",
        )
        self.rt.update(parent)

        # 已有同级兄弟 A：连接超时
        sibling_a = _make_entry(
            "network.timeout.connect",
            parent_path="network.timeout",
            stats={"freq": 3, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
            boundary_rules="仅处理 TCP 连接阶段超时",
            logic_signature="修复 TCP 连接超时",
        )
        self.rt.update(sibling_a)

        # 分裂读超时（与父节点和兄弟都不同边界，不应被拒绝）
        child = self.rt.split(
            parent_category_id="network.timeout",
            child_name="read",
            reason="测试",
            child_boundary_rules="仅处理 HTTP 读超时阶段",
            child_logic_signature="修复 HTTP 读超时",
        )
        assert child.category_id == "network.timeout.read"

    def test_split_with_no_siblings_allowed(self) -> None:
        """父节点无已有子节点时，split 应正常通过（需给出不同边界）。"""
        parent = _make_entry(
            "llm_inference.rate",
            parent_path="root.llm_inference",
            stats={"freq": 20, "impact": 0.6, "trend": 0.0, "recover_cost": 1},
            boundary_rules="处理所有 LLM 速率限制错误",
            logic_signature="LLM 速率限制修复",
        )
        self.rt.update(parent)

        child = self.rt.split(
            parent_category_id="llm_inference.rate",
            child_name="throttle",
            reason="测试",
            child_boundary_rules="仅处理客户端端限流策略",
            child_logic_signature="客户端端限流算法修复",
        )
        assert child.category_id == "llm_inference.rate.throttle"

    def test_split_sibling_overlap_with_distinct_boundary_passes(self) -> None:
        """候选子节点与同级兄弟边界完全不同时，应允许分裂。"""
        parent = _make_entry(
            "data_parsing.format",
            parent_path="root.data_parsing",
            stats={"freq": 15, "impact": 0.7, "trend": 0.0, "recover_cost": 1},
            boundary_rules="处理所有数据格式解析错误",
            logic_signature="数据格式解析修复",
        )
        self.rt.update(parent)

        # 已有同级兄弟：JSON 解析
        sibling = _make_entry(
            "data_parsing.format.json",
            parent_path="data_parsing.format",
            stats={"freq": 2, "impact": 0.4, "trend": 0.0, "recover_cost": 1},
            boundary_rules="仅处理 JSON 格式解析错误",
            logic_signature="JSON Schema 验证与修复",
        )
        self.rt.update(sibling)

        # 分裂 CSV 解析（完全无关边界）
        child = self.rt.split(
            parent_category_id="data_parsing.format",
            child_name="csv",
            reason="测试",
            child_boundary_rules="仅处理 CSV 格式解析错误",
            child_logic_signature="CSV 列对齐修复",
        )
        assert child.category_id == "data_parsing.format.csv"


# ══════════════════════════════════════════════════════════════════
# Step 84: O(n) 全量扫描优化
# ══════════════════════════════════════════════════════════════════

class TestQueryOptimization:
    """验证 Step 84：parent_path 过滤下推到 SQL 层，避免全表扫描。"""

    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()
        self.rt = RoutingTable(self.storage)

    def test_query_by_parent_path_returns_only_children(self) -> None:
        """query(parent_path=X) 只返回以 X 为父节点的直接子节点。"""
        # 创建父节点 + 2 个子节点
        parent = _make_entry(
            "network.timeout", parent_path="root.network",
            stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
        )
        self.rt.update(parent)

        for i in range(2):
            child = _make_entry(
                f"network.timeout.child_{i}",
                parent_path="network.timeout",
                stats={"freq": 5, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
            )
            self.rt.update(child)

        # 创建其他不相关的节点
        other = _make_entry(
            "network.other_node", parent_path="root.network",
            stats={"freq": 3, "impact": 0.3, "trend": 0.0, "recover_cost": 1},
        )
        self.rt.update(other)

        # 查询 parent_path = "network.timeout" 的节点
        results = self.rt.query(parent_path="network.timeout")
        assert len(results) == 2
        for r in results:
            assert r.category_id.startswith("network.timeout.child_")

    def test_query_all_without_parent_path_returns_all(self) -> None:
        """query() 无 parent_path 时返回全部条目（向后兼容）。"""
        for i in range(5):
            self.rt.update(_make_entry(
                f"network.node_{i}", parent_path="root.network",
                stats={"freq": i, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
            ))

        results = self.rt.query()
        assert len(results) == 5

    def test_query_by_expression_with_parent_path(self) -> None:
        """query_by_expression() 的 parent_path 过滤也应下推到 SQL。"""
        # 创建两个不同父节点的节点
        a = _make_entry(
            "network.a", parent_path="root.network",
            stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
            tags={Tag("状态_实验性")},
        )
        self.rt.update(a)

        b = _make_entry(
            "network.b", parent_path="root.network",
            stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
            tags={Tag("状态_实验性"), Tag("场景_第三方依赖")},
        )
        self.rt.update(b)

        query_expr = {"type": "and", "children": [{"type": "has", "tag": "状态_实验性"}]}
        results = self.rt.query_by_expression(query_expr, parent_path="root.network")
        assert len(results) == 2
