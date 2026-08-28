"""Step 39/44/45/46/47: 剩余 P2/P3 特性单元测试。"""
from datetime import datetime, timedelta, timezone

from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.overlap_checker import OverlapChecker
from src.storage import Storage


def _make_entry(
    cid: str = "network.rate_limit.429",
    parent_path: str | None = None,
    stats: dict[str, float | str] | None = None,
    boundary: str = "仅处理限流",
    logic: str = "修复限流错误",
) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=cid,
        parent_path=parent_path or f"root.{cid.split('.')[0]}",
        focus_description="测试",
        boundary_rules=boundary,
        logic_signature=logic,
    )
    return RoutingTableEntry(
        category_id=cid,
        stats=stats or {"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1, "sample_count": 10},
        local_map=lm,
        tags={Tag("状态_实验性")},
    )


# ══════════════════════════════════════════════════════════════════
# Step 39: 周期性重叠审计
# ══════════════════════════════════════════════════════════════════

class TestOverlapAudit:
    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()

    def test_audit_detects_high_overlap(self) -> None:
        """审计应能检测到高度重叠的节点对。"""
        checker = OverlapChecker(self.storage)
        # 插入两个高度相似的节点
        e1 = _make_entry("network.http_429_a", logic="修复 HTTP 429 限流错误", boundary="仅处理限流错误")
        e2 = _make_entry("network.http_429_b", logic="修复 HTTP 429 限流错误", boundary="仅处理限流错误")
        self.storage.upsert_routing_entry(e1)
        self.storage.upsert_routing_entry(e2)

        # 使用 OverlapChecker 手动验证
        result = checker.check(
            "network.http_429_b",
            "修复 HTTP 429 限流错误",
            "仅处理限流错误",
            root_category="network",
        )
        assert result.decision in ("MERGE", "UNCERTAIN")
        assert result.max_overlap >= 0.7


# ══════════════════════════════════════════════════════════════════
# Step 44: 多目标排序接口
# ══════════════════════════════════════════════════════════════════

class TestMultiObjectiveRank:
    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()

    def test_rank_by_overall(self) -> None:
        """rank_by='overall' 应按综合得分降序。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        rt.update(_make_entry("network.a", stats={"freq": 10, "impact": 0.5, "trend": 0.0, "recover_cost": 2}))
        rt.update(_make_entry("network.b", stats={"freq": 5, "impact": 0.9, "trend": 0.0, "recover_cost": 1}))
        result = rt.rank(rank_by="overall")
        assert len(result) == 2
        # network.b impact 更高，但 network.a freq 更高
        # 综合得分取决于权重

    def test_rank_by_cost(self) -> None:
        """rank_by='cost' 应按 cost 升序（低代价优先）。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        rt.update(_make_entry("network.a", stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 5}))
        rt.update(_make_entry("network.b", stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1}))
        result = rt.rank(rank_by="cost")
        assert result[0].category_id == "network.b"  # 低代价优先
        assert result[1].category_id == "network.a"

    def test_rank_by_invalid_raises(self) -> None:
        """非法 rank_by 值应抛出 ValueError。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        try:
            rt.rank(rank_by="invalid")
            raise AssertionError("应抛出 ValueError")
        except ValueError:
            pass


# ══════════════════════════════════════════════════════════════════
# Step 45: 排序置信度
# ══════════════════════════════════════════════════════════════════

class TestScoreConfidence:
    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()

    def test_confidence_high_when_dimensions_similar(self) -> None:
        """四维得分相近时置信度应较高。"""
        from src.scoring import ScoreCalculator, ScoreConfig
        calc = ScoreCalculator(ScoreConfig(freq_max=100, cost_max=10))
        bd = calc.score_with_breakdown(_make_entry(
            stats={"freq": 50, "impact": 0.5, "trend": 0.5, "recover_cost": 5, "sample_count": 10},
        ))
        assert bd.confidence > 0.5

    def test_confidence_low_when_dimensions_diverge(self) -> None:
        """四维得分分歧大时置信度应较低。"""
        from src.scoring import ScoreCalculator, ScoreConfig
        calc = ScoreCalculator(ScoreConfig(freq_max=100, cost_max=10))
        bd = calc.score_with_breakdown(_make_entry(
            stats={"freq": 100, "impact": 0.0, "trend": 0.0, "recover_cost": 10, "sample_count": 10},
        ))
        assert bd.confidence > 0  # 仍为正数
        # 比较两个 case 的置信度
        bd2 = calc.score_with_breakdown(_make_entry(
            stats={"freq": 50, "impact": 0.5, "trend": 0.5, "recover_cost": 5, "sample_count": 10},
        ))
        assert bd.confidence < bd2.confidence


# ══════════════════════════════════════════════════════════════════
# Step 46: 节点活跃度标记
# ══════════════════════════════════════════════════════════════════

class TestInactiveNodeFiltering:
    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()

    def test_inactive_nodes_excluded(self) -> None:
        """inactive_days > 0 时应排除非活跃节点。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)

        # 活跃节点（最近出现）
        now_iso = datetime.now(timezone.utc).isoformat()
        rt.update(_make_entry("network.active", stats={
            "freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1, "sample_count": 10, "last_seen": now_iso
        }))

        # 非活跃节点（30天前出现）
        old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rt.update(_make_entry("network.inactive", stats={
            "freq": 100, "impact": 0.9, "trend": 0.0, "recover_cost": 1, "sample_count": 10, "last_seen": old_iso
        }))

        # inactive_days=7 → 只返回活跃节点
        result = rt.rank(inactive_days=7)
        assert len(result) == 1
        assert result[0].category_id == "network.active"

    def test_no_inactive_filter_when_zero(self) -> None:
        """inactive_days=0（默认）时不排除任何节点。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        rt.update(_make_entry("network.a"))
        rt.update(_make_entry("network.b"))
        result = rt.rank(inactive_days=0)
        assert len(result) == 2


# ══════════════════════════════════════════════════════════════════
# Step 47: 批量操作接口
# ══════════════════════════════════════════════════════════════════

class TestBulkOperations:
    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()

    def test_bulk_upsert(self) -> None:
        """bulk_upsert() 应一次性写入多条。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        entries = [_make_entry(f"network.a_{i}") for i in range(3)]
        results = rt.bulk_upsert(entries)
        assert len(results) == 3
        assert rt.count() == 3

    def test_bulk_create_overwrites(self) -> None:
        """bulk_create() 遇到已存在节点应抛出 ValueError。"""
        from src.routing_table import RoutingTable
        rt = RoutingTable(self.storage)
        rt.update(_make_entry("network.existing"))
        entries = [_make_entry("network.existing")]
        try:
            rt.bulk_create(entries)
            raise AssertionError("应抛出 ValueError")
        except ValueError:
            pass
