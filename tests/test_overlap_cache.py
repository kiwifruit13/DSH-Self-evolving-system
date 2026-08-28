"""Step 48: OverlapChecker L1 缓存单元测试。"""
from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.overlap_checker import OverlapChecker
from src.storage import Storage


def _insert_entry(storage: Storage, cid: str, logic: str, boundary: str) -> None:
    lm = LocalMindMap(
        node_id=cid, parent_path=f"root.{cid.split('.')[0]}",
        focus_description="测试", boundary_rules=boundary, logic_signature=logic,
    )
    storage.upsert_routing_entry(RoutingTableEntry(
        category_id=cid,
        stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
        local_map=lm,
        tags={Tag("状态_实验性")},
    ))


class TestOverlapCheckCache:
    """Step 48：L1 缓存行为验证。"""

    def setup_method(self) -> None:
        self.storage = Storage(":memory:")
        self.storage.init()
        self.checker = OverlapChecker(self.storage, cache_capacity=8, cache_ttl_seconds=5.0)
        _insert_entry(self.storage, "network.http_429", "修复 HTTP 429 限流错误", "仅处理限流")

    def test_cache_hit_returns_same_decision(self) -> None:
        """同 key 的二次调用应命中缓存，返回相同决策。"""
        result1 = self.checker.check("network.x", "修复完全不同", "边界完全不同")
        result2 = self.checker.check("network.x", "修复完全不同", "边界完全不同")
        assert result1.decision == result2.decision
        assert result1.max_overlap == result2.max_overlap

    def test_cache_stores_entries(self) -> None:
        """check() 后缓存应有对应条目。"""
        self.checker.check("network.a", "测试签名", "边界")
        assert len(self.checker._cache) == 1

    def test_cache_different_keys(self) -> None:
        """不同 candidate_id 应产生不同缓存条目。"""
        self.checker.check("network.a", "签名 A", "边界 A")
        self.checker.check("network.b", "签名 B", "边界 B")
        assert len(self.checker._cache) == 2

    def test_clear_cache(self) -> None:
        """clear_cache() 后缓存应为空。"""
        self.checker.check("network.a", "签名 A", "边界 A")
        assert len(self.checker._cache) == 1
        self.checker.clear_cache()
        assert len(self.checker._cache) == 0

    def test_cache_eviction(self) -> None:
        """超过缓存容量时应淘汰最早条目。"""
        checker = OverlapChecker(self.storage, cache_capacity=2, cache_ttl_seconds=5.0)
        checker.check("network.a", "签名 A", "边界 A")
        checker.check("network.b", "签名 B", "边界 B")
        checker.check("network.c", "签名 C", "边界 C")
        # 容量为 2，淘汰最早
        assert len(checker._cache) == 2
        assert "network.a|network" not in checker._cache
