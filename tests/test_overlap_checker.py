"""重叠率校验器单元测试 — 核心算法 + 边界场景。"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.overlap_checker import (
    OverlapChecker,
    _boundary_overlap,
    _levenshtein_distance,
    _signature_similarity,
)
from src.storage import Storage


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def checker(storage: Storage) -> OverlapChecker:
    return OverlapChecker(storage)


def _make_entry(category_id: str, boundary: str) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="root",
        focus_description="测试",
        boundary_rules=boundary,
        logic_signature="测试逻辑",
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
        local_map=lm,
        tags={Tag("状态_实验性")},
    )


# ══════════════════════════════════════════════════════════════════
# 算法函数
# ══════════════════════════════════════════════════════════════════

class TestLevenshtein:
    def test_identical(self) -> None:
        assert _levenshtein_distance("hello", "hello") == 0

    def test_completely_different(self) -> None:
        assert _levenshtein_distance("abc", "xyz") == 3

    def test_one_edit(self) -> None:
        assert _levenshtein_distance("kitten", "sitten") == 1

    def test_empty(self) -> None:
        assert _levenshtein_distance("", "abc") == 3
        assert _levenshtein_distance("abc", "") == 3
        assert _levenshtein_distance("", "") == 0


class TestSignatureSimilarity:
    def test_identical(self) -> None:
        assert _signature_similarity("HTTP_429", "HTTP_429") == 1.0

    def test_completely_different(self) -> None:
        assert _signature_similarity("ABCD", "WXYZ") == 0.0

    def test_partial_match(self) -> None:
        # "HTTP_429" vs "HTTP_500" — 共享 "HTTP_" 和数字模式
        sim = _signature_similarity("HTTP_429", "HTTP_500")
        assert 0.3 < sim < 0.9

    def test_empty(self) -> None:
        assert _signature_similarity("", "anything") == 0.0


class TestBoundaryOverlap:
    def test_identical_boundaries(self, storage: Storage) -> None:
        e1 = _make_entry("network.a", "仅处理 HTTP 429 状态码，不处理 TCP 连接超时")
        e2 = _make_entry("network.b", "仅处理 HTTP 429 状态码，不处理 TCP 连接超时")
        overlap = _boundary_overlap(e1, e2)
        assert overlap > 0.5

    def test_completely_different(self, storage: Storage) -> None:
        e1 = _make_entry("network.a", "仅处理 HTTP 429 状态码，不处理 TCP 连接超时")
        e2 = _make_entry("network.b", "只处理数据库连接池耗尽，不涉及网络层")
        overlap = _boundary_overlap(e1, e2)
        assert overlap < 0.3

    def test_partial_overlap(self, storage: Storage) -> None:
        e1 = _make_entry("network.a", "仅处理 HTTP 429，不涉及认证")
        e2 = _make_entry("network.b", "仅处理 HTTP 500，不涉及认证")
        overlap = _boundary_overlap(e1, e2)
        assert 0.1 < overlap < 0.6


# ══════════════════════════════════════════════════════════════════
# OverlapChecker
# ══════════════════════════════════════════════════════════════════

class TestOverlapChecker:
    def test_empty_table_allows(self, checker: OverlapChecker) -> None:
        result = checker.check(
            candidate_category_id="network.new_error",
            candidate_signature="HTTP_503",
            candidate_boundary="仅处理 HTTP 503",
        )
        assert result.max_overlap == 0.0
        assert result.allows_creation is True

    def test_similar_entry_blocks(self, checker: OverlapChecker, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry(
            "network.http_503",
            "仅处理 HTTP 503 状态码，不处理连接超时",
        ))

        result = checker.check(
            candidate_category_id="network.http_503",
            candidate_signature="测试逻辑",
            candidate_boundary="仅处理 HTTP 503 状态码，不处理连接超时",
        )
        assert result.max_overlap >= 0.7
        assert result.allows_creation is False
        assert result.max_overlap_with == "network.http_503"

    def test_different_entry_allows(self, checker: OverlapChecker, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry(
            "network.http_429",
            "仅处理 HTTP 429 状态码，不处理 TCP 连接超时",
        ))

        result = checker.check(
            candidate_category_id="network.ssl_cert",
            candidate_signature="SSL_CERT_EXPIRED",
            candidate_boundary="仅处理 SSL 证书过期，不处理 TLS 握手",
        )
        assert result.allows_creation is True

    def test_partial_overlap_allows(self, storage: Storage) -> None:
        """部分相似但低于阈值时允许创建。"""
        checker = OverlapChecker(storage, threshold=0.9)
        storage.upsert_routing_entry(_make_entry(
            "network.http_500",
            "仅处理 HTTP 500 服务器错误，不处理客户端错误",
        ))

        result = checker.check(
            candidate_category_id="network.http_502",
            candidate_signature="HTTP_502",
            candidate_boundary="仅处理 HTTP 502 网关错误，不处理服务器内部错误",
        )
        # HTTP_500 和 HTTP_502 高度相似，但低于 0.9 阈值
        assert result.allows_creation is True

    def test_result_to_dict(self, checker: OverlapChecker) -> None:
        result = checker.check("net.a", "ERR", "test")
        d = result.to_dict()
        assert "candidate_id" in d
        assert "max_overlap" in d
        assert "allows_creation" in d

    def test_threshold_configurable(self, storage: Storage) -> None:
        checker = OverlapChecker(storage, threshold=0.5)
        storage.upsert_routing_entry(_make_entry(
            "network.http_429",
            "仅处理 HTTP 429",
        ))

        result = checker.check(
            "network.http_500",
            "HTTP_500",
            "仅处理 HTTP 500",
        )
        # 同根分类 + 相似边界可能超过 0.5
        assert result.max_overlap > 0.0
        assert checker.threshold == 0.5

    def test_weighted_scoring(self, checker: OverlapChecker, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry(
            "network.http_500",
            "仅处理 HTTP 500 服务器错误",
        ))

        result = checker.check(
            "network.http_500",
            "测试逻辑",
            "仅处理 HTTP 500 服务器错误",
        )
        # 边界完全匹配（子集/超集检测）→ 边界重叠 = 1.0
        # 签名完全匹配 → sig_sim = 1.0
        # total = 0.55*1.0 + 0.45*1.0 = 1.0
        assert result.max_overlap >= 0.8

    def test_scores_detail(self, checker: OverlapChecker, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.a", "test boundary"))
        storage.upsert_routing_entry(_make_entry("network.b", "test boundary"))
        storage.upsert_routing_entry(_make_entry("data_parsing.c", "test boundary"))

        result = checker.check("network.new", "ERR", "new boundary")
        # 根分类过滤：只返回 network 类别的条目
        assert len(result.all_scores) == 2
        for score in result.all_scores:
            assert "category_id" in score
            assert "sig_similarity" in score
            assert "boundary_overlap" in score
            assert "total_overlap" in score
            assert "root_match" not in score

    # ══════════════════════════════════════════════════════════════════
    # P0 修复回归测试
    # ══════════════════════════════════════════════════════════════════

    def test_signature_uses_candidate_signature(self, storage: Storage) -> None:
        """验证签名相似度使用 candidate_signature 对比 entry.logic_signature"""
        storage.upsert_routing_entry(_make_entry(
            "network.http_500",
            "仅处理 HTTP 500",
        ))

        # 相同的 signature → 高重叠
        checker = OverlapChecker(storage)
        result_same = checker.check(
            "network.http_501",
            "测试逻辑",  # 与 entry.logic_signature 一致
            "仅处理 HTTP 500",
        )
        # BUG-34 修复后缓存层已整体移除，check() 每次真实计算，无需清缓存

        # 不同的 signature → 低重叠
        result_diff = checker.check(
            "network.http_502",
            "完全无关的签名",
            "仅处理 HTTP 500",
        )

        assert result_same.max_overlap > result_diff.max_overlap

    def test_root_category_filtering(self, storage: Storage) -> None:
        """不同根分类的节点互不阻挡"""
        storage.upsert_routing_entry(_make_entry(
            "network.http_500",
            "仅处理 HTTP 500 服务器错误",
        ))

        checker = OverlapChecker(storage)
        # data_parsing 根分类，与 network 不同
        result = checker.check(
            "data_parsing.json_error",
            "JSON_PARSE_FAILED",
            "仅处理 JSON 解析错误",
        )
        # 同根分类下没有节点 → 直接允许
        assert result.max_overlap == 0.0
        assert result.allows_creation is True
        assert len(result.all_scores) == 0

    def test_subset_detection(self, storage: Storage) -> None:
        """子集关系应识别为高重叠"""
        entry = _make_entry(
            "network.http_500",
            "仅处理 HTTP 500 服务器错误，不处理客户端错误，不处理网络层",
        )
        storage.upsert_routing_entry(entry)

        checker = OverlapChecker(storage)
        # 候选节点边界是 entry 的子集
        result = checker.check(
            "network.http_500_short",
            "测试逻辑",
            "仅处理 HTTP 500 服务器错误",
        )
        # 子集关系 → 重叠率 = len(子集)/len(全集)
        assert result.max_overlap >= 0.5
        assert result.allows_creation is False

    def test_stop_words_excluded(self, storage: Storage) -> None:
        """停用词不参与 Jaccard 计算"""
        entry1 = _make_entry("network.a", "仅处理 HTTP 429，不涉及认证")
        storage.upsert_routing_entry(entry1)

        checker = OverlapChecker(storage)
        result = checker.check(
            "network.b",
            "测试逻辑",
            "仅处理 HTTP 429，不涉及 SSL",
        )
        # 去掉停用词后，"仅处理" 不贡献重叠
        # 但 "HTTP", "429", "SSL" 是有效词
        # 重叠应在合理范围内
        assert result.max_overlap > 0.0
