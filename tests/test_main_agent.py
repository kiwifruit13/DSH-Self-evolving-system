"""主代理单元测试 — 只读查询 / Skill 执行 / 未知举证。"""
from pathlib import Path

import pytest

from src.main_agent import MainAgent
from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.pending_queue import PendingQueue
from src.skill_compiler import SkillCompiler
from src.storage import Storage


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def queue(storage: Storage) -> PendingQueue:
    return PendingQueue(storage, capacity=100)


@pytest.fixture
def agent(storage: Storage, queue: PendingQueue) -> MainAgent:
    return MainAgent(storage, queue)


def _seed_data(storage: Storage) -> None:
    """向路由表预置测试数据。"""
    lm = LocalMindMap(
        node_id="network.rate_limit.429",
        parent_path="root.network",
        focus_description="聚焦 HTTP 429",
        boundary_rules="仅处理 HTTP 429",
        logic_signature="指数退避重试",
    )
    lm.append_log("create", "测试数据", "human")
    entry = RoutingTableEntry(
        category_id="network.rate_limit.429",
        stats={"freq": 100, "impact": 0.9, "trend": 0.3, "recover_cost": 2},
        local_map=lm,
        tags={Tag("状态_实验性"), Tag("场景_第三方依赖")},
    )
    storage.upsert_routing_entry(entry)

    # 编译 Skill
    compiler = SkillCompiler(storage)
    compiler.compile_from_entry(entry)

    # 第二个节点（不同标签）
    lm2 = LocalMindMap(
        node_id="network.ssl.cert",
        parent_path="root.network",
        focus_description="SSL 证书",
        boundary_rules="仅处理 SSL 证书",
        logic_signature="校验证书",
    )
    entry2 = RoutingTableEntry(
        category_id="network.ssl.cert",
        stats={"freq": 50, "impact": 0.8, "trend": 0.1, "recover_cost": 3},
        local_map=lm2,
        tags={Tag("状态_稳定"), Tag("场景_第三方依赖")},
    )
    storage.upsert_routing_entry(entry2)


# ══════════════════════════════════════════════════════════════════
# 精确查询
# ══════════════════════════════════════════════════════════════════

class TestMainAgentExactLookup:
    def test_lookup_exact_found(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        result = agent.lookup_exact("network.rate_limit.429")
        assert result.match_type == "exact"
        assert result.entry is not None
        assert result.entry.category_id == "network.rate_limit.429"
        assert result.skill is not None
        assert result.skill.name == "NetworkRateLimit429Skill"

    def test_lookup_exact_not_found(self, agent: MainAgent) -> None:
        result = agent.lookup_exact("nonexistent")
        assert result.match_type == "none"
        assert result.entry is None
        assert result.skill is None
        assert "不存在" in result.note


# ══════════════════════════════════════════════════════════════════
# 模糊查询
# ══════════════════════════════════════════════════════════════════

class TestMainAgentFuzzyLookup:
    def test_lookup_fuzzy_by_tag(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        results = agent.lookup_fuzzy(
            required_tags={Tag("场景_第三方依赖")}, limit=10
        )
        assert len(results) >= 1
        # 两个节点都有 场景_第三方依赖
        ids = {r.category_id for r in results}
        assert "network.rate_limit.429" in ids
        assert "network.ssl.cert" in ids

    def test_lookup_fuzzy_no_match(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        results = agent.lookup_fuzzy(
            required_tags={Tag("场景_本地计算")}, limit=10
        )
        assert len(results) == 0

    def test_lookup_fuzzy_with_root_filter(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        results = agent.lookup_fuzzy(
            required_tags={Tag("场景_第三方依赖")},
            root_category="network",
        )
        assert len(results) >= 1
        for r in results:
            assert r.category_id.startswith("network.")


# ══════════════════════════════════════════════════════════════════
# Skill 执行
# ══════════════════════════════════════════════════════════════════

class TestMainAgentSkillExecution:
    def test_execute_skill_default(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        lookup = agent.lookup_exact("network.rate_limit.429")
        assert lookup.skill is not None

        result = agent.execute_skill(lookup.skill, context={"target": "api.example.com"})
        assert result.skill_id == lookup.skill.skill_id
        assert result.total_steps == 3
        assert result.all_succeeded is True
        assert result.overall_success is True

        for step_result in result.steps:
            assert step_result.success is True
            # network 根分类 → tool 模式 → validate_params/execute_with_retry/verify_result
            # generic → precheck/execute/postcheck
            assert step_result.step_id in (
                "precheck", "execute", "postcheck",
                "validate_params", "execute_with_retry", "verify_result",
            )

    def test_execute_skill_custom_executor(self, agent: MainAgent, storage: Storage) -> None:
        _seed_data(storage)
        lookup = agent.lookup_exact("network.rate_limit.429")
        assert lookup.skill is not None

        def custom_executor(step, context):
            from src.main_agent import SkillExecutionStepResult
            return SkillExecutionStepResult(
                step_id=step.step_id,
                action=step.action,
                success="api" in context.get("target", ""),
                output={"called": True},
            )

        # 成功场景
        result = agent.execute_skill(lookup.skill, context={"target": "api.example.com"}, executor=custom_executor)
        assert result.all_succeeded is True

        # 失败场景
        result2 = agent.execute_skill(lookup.skill, context={"target": "other"}, executor=custom_executor)
        assert result2.all_succeeded is False
        assert result2.successful_steps == 0


# ══════════════════════════════════════════════════════════════════
# 未知错误举证
# ══════════════════════════════════════════════════════════════════

class TestMainAgentReportUnknown:
    def test_report_unknown_success(self, agent: MainAgent) -> None:
        ok = agent.report_unknown(
            error_stack="GraphQL: Field 'user' not found",
            context={"session_id": "s123", "tool": "graphql_query"},
            attempted_strategies=["retry", "fallback"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        assert ok is True

    def test_report_unknown_fills_queue(self, agent: MainAgent) -> None:
        agent.report_unknown("Error 1", confidence=0.5)
        agent.report_unknown("Error 2", confidence=0.3)
        assert agent._queue.pending_count == 2

    def test_report_unknown_queue_full(self, storage: Storage) -> None:
        queue = PendingQueue(storage, capacity=2)
        agent = MainAgent(storage, queue)
        assert agent.report_unknown("Error 1") is True
        assert agent.report_unknown("Error 2") is True
        # 第三次应失败（无回调时抛异常，有回调时返回 False）
        from src.pending_queue import QueueFullError
        with pytest.raises(QueueFullError):
            agent.report_unknown("Error 3")
