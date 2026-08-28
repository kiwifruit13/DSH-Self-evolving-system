"""四维排序计算器单元测试。"""
from datetime import datetime, timedelta, timezone

import pytest

from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.routing_table import RoutingTable
from src.scoring import ScoreCalculator, ScoreConfig
from src.storage import Storage


def _make_entry(category_id: str, stats: dict | None = None) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="root",
        focus_description="测试",
        boundary_rules="测试边界",
        logic_signature="测试逻辑",
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats=stats or {"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
        local_map=lm,
        tags={Tag("状态_实验性")},
    )


# ══════════════════════════════════════════════════════════════════
# 归一化函数
# ══════════════════════════════════════════════════════════════════

class TestNormalize:
    @pytest.fixture
    def calc(self) -> ScoreCalculator:
        return ScoreCalculator()

    def test_normalize_freq(self, calc: ScoreCalculator) -> None:
        assert calc.normalize_freq(0) == 0.0
        assert calc.normalize_freq(1) > 0.0
        # 高频时接近 1
        assert calc.normalize_freq(1000) > 0.9
        assert calc.normalize_freq(10000) > 0.99

    def test_normalize_impact(self, calc: ScoreCalculator) -> None:
        assert calc.normalize_impact(0.0) == 0.0
        assert calc.normalize_impact(0.5) == 0.5
        assert calc.normalize_impact(1.0) == 1.0
        assert calc.normalize_impact(1.5) == 1.0  # 钳制
        assert calc.normalize_impact(-0.5) == 0.0

    def test_normalize_trend(self, calc: ScoreCalculator) -> None:
        assert calc.normalize_trend(-1.0) == 0.0
        assert calc.normalize_trend(0.0) == 0.5
        assert calc.normalize_trend(1.0) == 1.0
        assert calc.normalize_trend(2.0) == 1.0  # 钳制

    def test_normalize_cost(self, calc: ScoreCalculator) -> None:
        assert calc.normalize_cost(0) == 1.0
        assert calc.normalize_cost(10) == pytest.approx(0.5, abs=0.01)
        assert calc.normalize_cost(100) < 0.1


# ══════════════════════════════════════════════════════════════════
# 核心计算
# ══════════════════════════════════════════════════════════════════

class TestScoreCalculator:
    @pytest.fixture
    def calc(self) -> ScoreCalculator:
        return ScoreCalculator()

    def test_compute_priority_zero(self, calc: ScoreCalculator) -> None:
        stats = {"freq": 0, "impact": 0, "trend": -1, "recover_cost": 999}
        score = calc.compute_priority(stats)
        # freq=0 → 0, impact=0 → 0, trend=-1 → 0, cost=999 → ~0
        assert score >= 0
        assert score < 0.2

    def test_compute_priority_high(self, calc: ScoreCalculator) -> None:
        stats = {"freq": 1000, "impact": 1.0, "trend": 1.0, "recover_cost": 0}
        score = calc.compute_priority(stats)
        assert score > 0.85

    def test_decay_factor(self, calc: ScoreCalculator) -> None:
        assert calc.decay_factor(0) == 1.0
        assert calc.decay_factor(7) == pytest.approx(0.5, abs=0.01)
        assert calc.decay_factor(14) == pytest.approx(0.25, abs=0.01)
        assert calc.decay_factor(100) < 0.001

    def test_compute_final_score_with_decay(self, calc: ScoreCalculator) -> None:
        stats = {"freq": 100, "impact": 0.9, "trend": 0.5, "recover_cost": 1}
        fresh = calc.compute_final_score(stats, days_since_last_seen=0)
        decayed = calc.compute_final_score(stats, days_since_last_seen=14)
        assert fresh > decayed

    def test_score_with_breakdown(self, calc: ScoreCalculator) -> None:
        entry = _make_entry("network.rate_limit.429")
        breakdown = calc.score_with_breakdown(entry, days_since_last_seen=1)
        assert breakdown.category_id == "network.rate_limit.429"
        assert 0.0 <= breakdown.final_score <= 1.0
        assert breakdown.decay_factor < 1.0
        # 验证 to_dict
        d = breakdown.to_dict()
        assert d["category_id"] == "network.rate_limit.429"
        assert "final_score" in d


# ══════════════════════════════════════════════════════════════════
# 排序
# ══════════════════════════════════════════════════════════════════

class TestRanking:
    @pytest.fixture
    def calc(self) -> ScoreCalculator:
        return ScoreCalculator()

    def test_rank_descending(self, calc: ScoreCalculator) -> None:
        entries = [
            _make_entry("network.high", {"freq": 1000, "impact": 1.0, "trend": 0.5, "recover_cost": 0}),
            _make_entry("network.low", {"freq": 1, "impact": 0.1, "trend": -0.5, "recover_cost": 10}),
        ]
        ranked = calc.rank(entries, days_since_last_seen=0)
        assert ranked[0].category_id == "network.high"
        assert ranked[1].category_id == "network.low"

    def test_top_k(self, calc: ScoreCalculator) -> None:
        entries = [
            _make_entry(f"network.node_{i}", {
                "freq": i * 100, "impact": 0.5 + i * 0.1, "trend": 0.0, "recover_cost": 2
            }) for i in range(10)
        ]
        top3 = calc.top_k(entries, k=3, days_since_last_seen=0)
        assert len(top3) == 3
        # 得分应降序
        for i in range(len(top3) - 1):
            assert top3[i].final_score >= top3[i + 1].final_score

    def test_rank_ascending(self, calc: ScoreCalculator) -> None:
        entries = [
            _make_entry("network.high", {"freq": 1000, "impact": 1.0, "trend": 0.5, "recover_cost": 0}),
            _make_entry("network.low", {"freq": 1, "impact": 0.1, "trend": -0.5, "recover_cost": 10}),
        ]
        ranked = calc.rank(entries, days_since_last_seen=0, reverse=False)
        assert ranked[0].category_id == "network.low"
        assert ranked[1].category_id == "network.high"


# ══════════════════════════════════════════════════════════════════
# P1-4: 时间衰减 per-entry 测试
# ══════════════════════════════════════════════════════════════════


class TestTimeDecayPerEntry:
    """验证 last_seen 时间戳驱动的 per-entry 时间衰减（蓝图对齐）。"""

    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()
        self.rt = RoutingTable(self.storage)

    def _insert_entry(
        self,
        category_id: str,
        freq: float,
        impact: float,
        last_seen: str | None = None,
    ) -> None:
        lm = LocalMindMap(
            node_id=category_id,
            parent_path=f"root.{category_id.split('.')[0]}",
            focus_description="测试",
            boundary_rules="测试边界",
            logic_signature="测试逻辑",
        )
        entry = RoutingTableEntry(
            category_id=category_id,
            stats={
                "freq": freq,
                "impact": impact,
                "trend": 0.0,
                "recover_cost": 1.0,
                "last_seen": last_seen,
            },
            local_map=lm,
            tags={Tag("状态_实验性")},
        )
        self.storage.upsert_routing_entry(entry)

    def test_fresh_entry_has_higher_score_than_stale(self) -> None:
        """今天被命中的节点得分 > 7 天前被命中的节点。"""
        now = datetime.now(timezone.utc)
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        self._insert_entry("network.fresh", freq=100, impact=0.9, last_seen=now.isoformat())
        self._insert_entry("network.stale", freq=100, impact=0.9, last_seen=seven_days_ago)

        ranked = self.rt.rank()
        assert len(ranked) == 2
        assert ranked[0].category_id == "network.fresh"
        assert ranked[0].final_score > ranked[1].final_score
        # fresh: decay = 1.0, stale: decay ≈ 0.5（半衰期 7 天）
        assert ranked[0].decay_factor > ranked[1].decay_factor + 0.3

    def test_same_last_seen_same_decay(self) -> None:
        """相同 last_seen 的节点应有相同衰减因子。"""
        # 使用固定时间戳（5 分钟前），避免两个 datetime.now() 调用产生微小差异
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self._insert_entry("network.a", freq=10, impact=0.8, last_seen=five_min_ago)
        self._insert_entry("network.b", freq=10, impact=0.8, last_seen=five_min_ago)

        ranked = self.rt.rank()
        assert len(ranked) == 2
        # 使用近似比较（1e-10 容差），因为 datetime.now() 的两次调用有纳秒差异
        assert abs(ranked[0].decay_factor - ranked[1].decay_factor) < 1e-10

    def test_missing_last_seen_falls_back_to_zero(self) -> None:
        """无 last_seen 的节点 decay_factor 应为 1.0（即 days=0）。"""
        self._insert_entry("network.no_date", freq=50, impact=0.7, last_seen=None)

        ranked = self.rt.rank()
        assert ranked[0].decay_factor == 1.0
        assert ranked[0].days_since_last_seen == 0.0

    def test_far_future_last_seen_treated_as_fresh(self) -> None:
        """last_seen 在未来（数据异常）时应处理为 days=0，decay=1.0。"""
        now = datetime.now(timezone.utc)
        future = (now + timedelta(days=10)).isoformat()
        self._insert_entry("network.future", freq=100, impact=0.9, last_seen=future)

        ranked = self.rt.rank()
        assert ranked[0].decay_factor == 1.0


# ══════════════════════════════════════════════════════════════════
# Step 77: 权重反馈回路
# ══════════════════════════════════════════════════════════════════

class TestReweightFeedback:
    """验证 Step 77：权重根据数据方差自适应调整。"""

    def test_reweight_increases_high_variance_dimension(self) -> None:
        calc = ScoreCalculator(ScoreConfig(reweight_alpha=0.5))
        stats_list = [
            {"freq": 1, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
            {"freq": 500, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
            {"freq": 1000, "impact": 0.5, "trend": 0.0, "recover_cost": 1},
        ]
        new_weights = calc.reweight(stats_list)
        assert new_weights["freq"] > ScoreConfig().freq_weight
        assert new_weights["impact"] <= ScoreConfig().impact_weight

    def test_reweight_single_entry_unchanged(self) -> None:
        calc = ScoreCalculator()
        stats_list = [{"freq": 100, "impact": 0.8, "trend": 0.0, "recover_cost": 2}]
        new_weights = calc.reweight(stats_list)
        assert new_weights["freq"] == 0.25
        assert new_weights["impact"] == 0.35

    def test_reweight_soft_update(self) -> None:
        calc = ScoreCalculator(ScoreConfig(reweight_alpha=0.1))
        stats_list = [
            {"freq": 0, "impact": 0.0, "trend": 0.0, "recover_cost": 0},
            {"freq": 1000, "impact": 1.0, "trend": 1.0, "recover_cost": 10},
        ]
        new_weights = calc.reweight(stats_list)
        assert 0.20 < new_weights["freq"] < 0.30
        assert 0.30 < new_weights["impact"] < 0.40


# ══════════════════════════════════════════════════════════════════
# Step 78: 数据驱动归一化
# ══════════════════════════════════════════════════════════════════

class TestCalibrateDataDriven:
    """验证 Step 78：从实际数据统计归一化参考值。"""

    def test_calibrate_updates_freq_max(self) -> None:
        calc = ScoreCalculator(ScoreConfig(freq_max=100.0))
        stats_list = [{"freq": i * 10} for i in range(1, 101)]
        result = calc.calibrate(stats_list)
        assert result["freq_max"] >= 900

    def test_calibrate_preserves_min(self) -> None:
        calc = ScoreCalculator(ScoreConfig(freq_max=1000.0))
        stats_list = [{"freq": 1} for _ in range(100)]
        result = calc.calibrate(stats_list)
        assert result["freq_max"] >= 100

    def test_calibrate_updates_cost_max(self) -> None:
        calc = ScoreCalculator(ScoreConfig(cost_max=1.0))
        stats_list = [{"recover_cost": i * 0.5} for i in range(1, 101)]
        result = calc.calibrate(stats_list)
        assert result["cost_max"] >= 45

    def test_calibrate_empty_list_unchanged(self) -> None:
        calc = ScoreCalculator()
        result = calc.calibrate([])
        assert result["freq_max"] == 1000.0
        assert result["cost_max"] == 10.0


# ══════════════════════════════════════════════════════════════════
# Step 80: 数据量感知
# ══════════════════════════════════════════════════════════════════

class TestSampleAwareImpact:
    """验证 Step 80：数据量感知的影响得分调整。"""

    def test_sufficient_samples_no_adjustment(self) -> None:
        calc = ScoreCalculator(ScoreConfig(sample_count_threshold=5))
        adjusted, confidence, penalty = calc.sample_aware_impact(0.8, 10)
        assert adjusted == 0.8
        assert confidence == 1.0
        assert penalty == 0.0

    def test_zero_samples_low_confidence(self) -> None:
        calc = ScoreCalculator(ScoreConfig(sample_count_threshold=5))
        adjusted, confidence, penalty = calc.sample_aware_impact(0.8, 0)
        expected = 0.8 * 0.3
        assert abs(adjusted - expected) < 1e-6
        assert confidence == 0.0
        assert penalty == 1.0

    def test_low_impact_stays_low(self) -> None:
        calc = ScoreCalculator(ScoreConfig(sample_count_threshold=5))
        adjusted, _, _ = calc.sample_aware_impact(0.01, 0)
        assert adjusted < 0.01

    def test_partial_samples_linear_confidence(self) -> None:
        calc = ScoreCalculator(ScoreConfig(sample_count_threshold=10))
        _, conf_3, _ = calc.sample_aware_impact(0.5, 3)
        _, conf_7, _ = calc.sample_aware_impact(0.5, 7)
        assert conf_3 < conf_7
        assert conf_3 == 0.3
        assert conf_7 == 0.7


# ══════════════════════════════════════════════════════════════════
# Step 79: score_with_breakdown 自动读取 last_seen
# ══════════════════════════════════════════════════════════════════

class TestScoreWithBreakdownAutoDecay:
    """验证 Step 79：score_with_breakdown 自动从 entry.stats 读取 last_seen。"""

    def setup_method(self) -> None:
        self.calc = ScoreCalculator()

    def _make_entry(self, category_id: str, last_seen: str | None, **kwargs) -> RoutingTableEntry:
        lm = LocalMindMap(
            node_id=category_id, parent_path="root",
            focus_description="测试", boundary_rules="测试边界", logic_signature="测试逻辑",
        )
        stats = {"freq": 50, "impact": 0.8, "trend": 0.0, "recover_cost": 1}
        if last_seen:
            stats["last_seen"] = last_seen
        stats.update(kwargs)
        return RoutingTableEntry(
            category_id=category_id, stats=stats, local_map=lm,
            tags={Tag("状态_实验性")},
        )

    def test_auto_reads_last_seen(self) -> None:
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        entry = self._make_entry("network.test", last_seen=seven_days_ago)
        bd = self.calc.score_with_breakdown(entry)
        assert 0.4 < bd.decay_factor < 0.6
        assert bd.days_since_last_seen > 6.5

    def test_missing_last_seen_defaults_to_zero(self) -> None:
        entry = self._make_entry("network.no_date", last_seen=None)
        bd = self.calc.score_with_breakdown(entry)
        assert bd.decay_factor == 1.0
        assert bd.days_since_last_seen == 0.0
