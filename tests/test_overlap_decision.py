"""Step 38: 重叠校验决策枚举 + 合并建议单元测试。"""
from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.overlap_checker import (
    DECISION_ACCEPT,
    DECISION_MERGE,
    DECISION_UNCERTAIN,
    OverlapChecker,
)
from src.storage import Storage


class TestOverlapCheckDecision:
    """验证 Step 38：OverlapCheckResult.decision + merge_target。"""

    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()
        self.checker = OverlapChecker(self.storage)

    def _insert(self, cid: str, logic: str, boundary: str) -> None:
        lm = LocalMindMap(
            node_id=cid, parent_path=f"root.{cid.split('.')[0]}",
            focus_description="测试", boundary_rules=boundary, logic_signature=logic,
        )
        self.storage.upsert_routing_entry(RoutingTableEntry(
            category_id=cid,
            stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_实验性")},
        ))

    def test_decision_accept_low_overlap(self) -> None:
        """重叠率低于阈值 70% 时应为 ACCEPT。"""
        self._insert("network.http_429", "修复 HTTP 429", "仅处理限流")
        result = self.checker.check(
            "network.timeout",
            "修复 TCP 超时",
            "仅处理连接超时",
        )
        assert result.decision == DECISION_ACCEPT
        assert result.merge_target is None
        assert result.allows_creation
        assert not result.should_merge

    def test_decision_merge_high_overlap(self) -> None:
        """重叠率高于阈值时应为 MERGE，并指定 merge_target。"""
        self._insert(
            "network.http_429",
            "修复 HTTP 429 限流错误",
            "仅处理 HTTP 429 限流错误",
        )
        # 与已有节点高度相似但非完全相同
        result = self.checker.check(
            "network.http_429_retry",
            "修复 HTTP 429 限流",
            "仅处理 HTTP 429",
        )
        assert result.decision == DECISION_MERGE
        assert result.merge_target == "network.http_429"
        assert not result.allows_creation
        assert result.should_merge

    def test_decision_uncertain_near_duplicate(self) -> None:
        """重叠率 >= 0.95 时应为 UNCERTAIN。"""
        self._insert(
            "network.http_429",
            "修复 HTTP 429 限流错误处理逻辑",
            "仅处理 HTTP 429 限流错误处理逻辑",
        )
        result = self.checker.check(
            "network.http_429_retry",
            "修复 HTTP 429 限流错误处理逻辑",
            "仅处理 HTTP 429 限流错误处理逻辑",
        )
        assert result.decision == DECISION_UNCERTAIN
        assert result.max_overlap >= 0.9

    def test_should_merge_property(self) -> None:
        """should_merge 属性应正确反映 decision。"""
        self._insert("network.a", "修复 HTTP 429 限流错误", "仅处理 HTTP 429 限流")
        # 高重叠但非完全相同 → MERGE
        merge_result = self.checker.check(
            "network.a_dup",
            "修复 HTTP 429 限流",
            "仅处理 HTTP 429 限流",
        )
        # 低重叠 → ACCEPT
        accept_result = self.checker.check(
            "network.z",
            "修复完全不同错误类型",
            "处理完全不同的边界规则",
        )
        assert merge_result.should_merge
        assert not accept_result.should_merge
