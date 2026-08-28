"""集成测试 — 跨节点属性聚合查询（标签进阶）。"""
from pathlib import Path

import pytest

from src.main_agent import MainAgent
from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.pending_queue import PendingQueue
from src.routing_table import RoutingTable
from src.storage import Storage
from src.tag_query import TagQueryBuilder, evaluate_query


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def queue(storage: Storage) -> PendingQueue:
    return PendingQueue(storage, capacity=100)


@pytest.fixture
def rt(storage: Storage) -> RoutingTable:
    return RoutingTable(storage)


def _seed_nodes(storage: Storage) -> None:
    """预置一组多样化的路由表节点，模拟真实场景。"""
    nodes = [
        ("network.http_429", "仅处理 HTTP 429 限流", {Tag("状态_稳定"), Tag("场景_第三方依赖"), Tag("代价_低消耗")},
         {"freq": 200, "impact": 0.95, "trend": 0.3, "recover_cost": 1}),
        ("network.http_500", "仅处理 HTTP 500 服务器错误", {
            Tag("状态_实验性"), Tag("场景_内部微服务"), Tag("代价_中消耗"),
        },
         {"freq": 80, "impact": 0.7, "trend": 0.1, "recover_cost": 3}),
        ("network.ssl_cert", "仅处理 SSL 证书过期", {Tag("状态_稳定"), Tag("场景_第三方依赖"), Tag("代价_低消耗")},
         {"freq": 150, "impact": 0.9, "trend": 0.0, "recover_cost": 2}),
        ("data_parsing.json", "仅处理 JSON 解析错误", {Tag("状态_稳定"), Tag("场景_内部微服务"), Tag("代价_低消耗")},
         {"freq": 100, "impact": 0.85, "trend": -0.1, "recover_cost": 2}),
        ("data_parsing.graphql", "仅处理 GraphQL 字段缺失", {
            Tag("状态_实验性"), Tag("场景_内部微服务"), Tag("代价_中消耗"),
        },
         {"freq": 30, "impact": 0.5, "trend": 0.5, "recover_cost": 4}),
        ("llm_inference.timeout", "仅处理 LLM 推理超时", {
            Tag("状态_实验性"), Tag("场景_第三方依赖"), Tag("代价_高延迟"),
        },
         {"freq": 50, "impact": 0.6, "trend": 0.2, "recover_cost": 5}),
    ]

    for cat_id, boundary, tags, stats in nodes:
        lm = LocalMindMap(
            node_id=cat_id,
            parent_path=f"root.{cat_id.split('.')[0]}",
            focus_description=boundary,
            boundary_rules=boundary,
            logic_signature=f"修复 {cat_id}",
        )
        entry = RoutingTableEntry(
            category_id=cat_id,
            stats=stats,
            local_map=lm,
            tags=tags,
        )
        storage.upsert_routing_entry(entry)


# ══════════════════════════════════════════════════════════════════
# 跨节点属性聚合查询
# ══════════════════════════════════════════════════════════════════

class TestCrossNodeAggregation:
    """Gherkin 场景扩展：通过多维标签进行跨节点属性聚合查询"""

    def test_query_stable_third_party_only(self, rt: RoutingTable, storage: Storage) -> None:
        """查所有 状态_稳定 AND 场景_第三方依赖 的节点"""
        _seed_nodes(storage)

        q = TagQueryBuilder().must(Tag("状态_稳定")).must(Tag("场景_第三方依赖")).build()
        results = rt.query_by_expression(q)

        ids = {r.category_id for r in results}
        assert "network.http_429" in ids
        assert "network.ssl_cert" in ids
        assert "data_parsing.json" not in ids  # 状态_稳定但场景_内部微服务
        assert "network.http_500" not in ids   # 状态_实验性

    def test_query_excluding_local_computation(self, rt: RoutingTable, storage: Storage) -> None:
        """查所有 代价_低消耗 AND NOT 场景_本地计算 的节点"""
        _seed_nodes(storage)

        q = TagQueryBuilder().must(Tag("代价_低消耗")).must_not(Tag("场景_本地计算")).build()
        results = rt.query_by_expression(q)

        ids = {r.category_id for r in results}
        # 三个代价_低消耗的节点都匹配
        assert "network.http_429" in ids
        assert "network.ssl_cert" in ids
        assert "data_parsing.json" in ids

    def test_query_or_groups(self, rt: RoutingTable, storage: Storage) -> None:
        """(状态_稳定 AND 场景_第三方依赖) OR (状态_实验性 AND 代价_中消耗)"""
        _seed_nodes(storage)

        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .must(Tag("场景_第三方依赖"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .must(Tag("代价_中消耗"))
            .end_group()
            .build()
        )
        results = rt.query_by_expression(q)

        ids = {r.category_id for r in results}
        # 第一组命中
        assert "network.http_429" in ids
        assert "network.ssl_cert" in ids
        # 第二组命中（状态_实验性 AND 代价_中消耗）
        assert "data_parsing.graphql" in ids
        assert "network.http_500" in ids

    def test_query_root_category_filter(self, rt: RoutingTable, storage: Storage) -> None:
        """限定 network 根分类下的标签查询"""
        _seed_nodes(storage)

        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        results = rt.query_by_expression(q, root_category="network")

        ids = {r.category_id for r in results}
        assert "network.http_429" in ids
        assert "network.ssl_cert" in ids
        assert "data_parsing.json" not in ids  # 不在 network 下

    def test_query_no_match(self, rt: RoutingTable, storage: Storage) -> None:
        """查询不存在的组合应返回空"""
        _seed_nodes(storage)

        q = TagQueryBuilder().must(Tag("状态_稳定")).must(Tag("场景_本地计算")).build()
        results = rt.query_by_expression(q)
        assert results == []

    def test_query_sorted_by_score(self, rt: RoutingTable, storage: Storage) -> None:
        """结果应按四维排序得分降序排列"""
        _seed_nodes(storage)

        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        results = rt.query_by_expression(q)

        assert len(results) >= 2
        # network.http_429 (freq=200) 应排在 data_parsing.json (freq=100) 前面
        first_ids = [r.category_id for r in results[:2]]
        assert first_ids[0] == "network.http_429"

    def test_evaluate_query_is_pure(self) -> None:
        """evaluate_query 应为纯函数，不修改输入"""
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        original_tags = set(tags)
        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        evaluate_query(tags, q)
        assert tags == original_tags


# ══════════════════════════════════════════════════════════════════
# 主代理集成：标签模糊匹配进阶
# ══════════════════════════════════════════════════════════════════

class TestMainAgentTagQuery:
    """主代理通过 TagQueryBuilder 执行高级标签查询"""

    def test_main_agent_fuzzy_lookup_equivalent(self, storage: Storage, queue: PendingQueue) -> None:
        """验证主代理的模糊查询与 TagQueryBuilder 结果一致"""
        _seed_nodes(storage)
        agent = MainAgent(storage, queue)

        # 简单标签查询
        results1 = agent.lookup_fuzzy(required_tags={Tag("状态_稳定")}, limit=10)

        # 用 TagQueryBuilder 查询
        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        rt = RoutingTable(storage)
        results2 = rt.query_by_expression(q)

        ids1 = {r.category_id for r in results1}
        ids2 = {r.category_id for r in results2}
        assert ids1 == ids2
