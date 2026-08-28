"""集成测试 — P0 核心闭环（Feature 1 场景 1 + Feature 2 场景 1）。

覆盖场景：
- 子代理从日志蒸馏 → 路由表更新
- 主代理精确查询 → Skill 执行 → 成功
- 未知错误 → 举证 → 子代理消费 → 路由表 + Skill 生成
"""
from pathlib import Path

import pytest

from src.main_agent import MainAgent
from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    Tag,
)
from src.pending_queue import PendingQueue
from src.storage import Storage
from src.sub_agent import SubAgent


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def queue(storage: Storage) -> PendingQueue:
    return PendingQueue(storage, capacity=100)


@pytest.fixture
def main_agent(storage: Storage, queue: PendingQueue) -> MainAgent:
    return MainAgent(storage, queue)


@pytest.fixture
def sub_agent(storage: Storage, queue: PendingQueue) -> SubAgent:
    return SubAgent(storage, queue)


# ══════════════════════════════════════════════════════════════════
# Feature 1 场景 1：子代理蒸馏 + 路由表更新
# ══════════════════════════════════════════════════════════════════

class TestFeature1Distillation:
    """Gherkin: 子代理基于会话日志蒸馏并维护路由表"""

    def test_distill_verified_fix_and_update_routing_table(
        self, sub_agent: SubAgent, storage: Storage
    ) -> None:
        """场景: 蒸馏"已验证可行"的错误修复并更新路由表节点"""
        # 模拟 DSH 会话日志：error → tool_call → success
        logs = [
            {"session_id": "session_001", "event_type": "error",
             "content": {"error_code": "HTTP_429", "description": "Rate limit exceeded"}},
            {"session_id": "session_001", "event_type": "tool_call",
             "content": {"tool": "exponential_backoff_retry", "impact_scope": "external_api",
                        "params": {"max_retries": 3}}},
            {"session_id": "session_001", "event_type": "success",
             "content": {"status": "task_recovered"}},
        ]
        sub_agent._log_reader = lambda: logs

        result = sub_agent.distill()

        # 提取了三元组
        assert result.total_distilled == 1
        assert len(result.new_entries) == 1

        entry = result.new_entries[0]
        # category_id 以 network 开头（HTTP_429 推断为 network）
        assert entry.category_id.startswith("network.")
        # 边界规则自动记述
        assert "HTTP_429" in entry.local_map.boundary_rules
        assert "不处理" in entry.local_map.boundary_rules
        # 自动打上标签
        assert Tag("状态_实验性") in entry.tags
        # 持久化验证
        retrieved = storage.get_routing_entry(entry.category_id)
        assert retrieved is not None


# ══════════════════════════════════════════════════════════════════
# Feature 2 场景 1：主代理精确查询 + Skill 执行
# ══════════════════════════════════════════════════════════════════

class TestFeature2ExactQuery:
    """Gherkin: 主代理基于路由表与标签查询规避方案"""

    def _seed_ssl_entry(self, storage: Storage) -> RoutingTableEntry:
        lm = LocalMindMap(
            node_id="network.ssl.cert_expired",
            parent_path="root.network.ssl",
            focus_description="聚焦 SSL 证书过期修复",
            boundary_rules="仅处理 SSL 证书过期，不处理 TLS 握手失败",
            logic_signature="重新加载证书并验证",
        )
        lm.append_log("create", "预置测试数据", "human")
        entry = RoutingTableEntry(
            category_id="network.ssl.cert_expired",
            stats={"freq": 200, "impact": 0.95, "trend": 0.1, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_稳定"), Tag("场景_第三方依赖")},
        )
        storage.upsert_routing_entry(entry)
        return entry

    def test_exact_match_find_and_execute_skill(
        self, main_agent: MainAgent, storage: Storage
    ) -> None:
        """场景: 通过分类精确匹配找到已验证方案并执行"""
        entry = self._seed_ssl_entry(storage)

        # 主代理查询
        result = main_agent.lookup_exact("network.ssl.cert_expired")
        assert result.match_type == "exact"
        assert result.entry is not None
        assert result.entry.category_id == "network.ssl.cert_expired"

        # Skill 不存在（未编译），需要子代理先编译
        assert result.skill is None

        # 子代理编译 Skill
        from src.skill_compiler import SkillCompiler
        compiler = SkillCompiler(storage)
        compiler.compile_from_entry(entry)

        # 主代理再次查询
        result2 = main_agent.lookup_exact("network.ssl.cert_expired")
        assert result2.skill is not None

        # 执行 Skill
        exec_result = main_agent.execute_skill(
            result2.skill, context={"target": "api.example.com"}
        )
        assert exec_result.overall_success is True
        assert exec_result.total_steps == 3

        # 主代理上下文不应产生对路由表的 Insert/Update
        assert storage.count_routing_entries() == 1


# ══════════════════════════════════════════════════════════════════
# Feature 3：未知错误反馈闭环
# ══════════════════════════════════════════════════════════════════

class TestFeature3UnknownFeedback:
    """Gherkin: 未知错误的反馈暂存与异步再规划"""

    def test_main_agent_reports_unknown_error(
        self, main_agent: MainAgent
    ) -> None:
        """场景: 主代理遇到未知错误并写入反馈暂存区"""
        ok = main_agent.report_unknown(
            error_stack="GraphQL: Field 'user' not found",
            context={"session_id": "s_graphql_001", "tool": "graphql_query"},
            attempted_strategies=["retry", "fallback", "cache_lookup"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        assert ok is True

        # 暂存区应有 1 条未处理
        assert main_agent._queue.pending_count == 1

    def test_sub_agent_consumes_feedback_and_creates_entry(
        self, main_agent: MainAgent, sub_agent: SubAgent, storage: Storage
    ) -> None:
        """场景: 子代理消费反馈暂存区并完成分类与 Skill 孵化"""
        # 主代理举证
        main_agent.report_unknown(
            error_stack="GraphQL: Field 'user' not found",
            context={"session_id": "s1", "tool": "graphql_query"},
            attempted_strategies=["retry", "fallback"],
            location_guess="data_parsing",
            confidence=0.7,
        )

        # 子代理消费
        result = sub_agent.consume_pending(batch_size=5)

        assert result.processed_count == 1
        assert len(result.new_entries) == 1
        assert len(result.compiled_skills) == 1

        entry = result.new_entries[0]
        assert entry.category_id.startswith("data_parsing.")
        assert entry.local_map.boundary_rules != ""
        assert entry.local_map.maintenance_log  # 有维护日志

        skill = result.compiled_skills[0]
        assert skill.name != ""
        assert len(skill.steps) >= 1
        # overview_map 继承自路由表节点
        assert skill.overview_map.parent_path == entry.category_id

    def test_full_p0_closed_loop(
        self, main_agent: MainAgent, sub_agent: SubAgent, storage: Storage
    ) -> None:
        """完整 P0 闭环：蒸馏 → 查询 → 执行 → 未知反馈 → 再分类"""
        # Step 1: 子代理蒸馏已知错误
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_429", "description": "Rate limited"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "backoff_retry", "impact_scope": "external"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "ok"}},
        ]
        sub_agent._log_reader = lambda: logs
        distill_result = sub_agent.distill()
        assert distill_result.total_distilled == 1
        distilled_id = distill_result.new_entries[0].category_id

        # Step 2: 主代理精确查询 + 执行
        lookup = main_agent.lookup_exact(distilled_id)
        assert lookup.match_type == "exact"
        assert lookup.entry is not None

        # 编译 Skill
        from src.skill_compiler import SkillCompiler
        compiler = SkillCompiler(storage)
        compiler.compile_from_entry(lookup.entry)

        lookup2 = main_agent.lookup_exact(distilled_id)
        assert lookup2.skill is not None
        exec_result = main_agent.execute_skill(lookup2.skill, context={})
        assert exec_result.overall_success is True

        # Step 3: 主代理遇到未知错误并举证
        ok = main_agent.report_unknown(
            error_stack="Unknown GraphQL error",
            context={},
            attempted_strategies=["retry"],
            location_guess="data_parsing",
            confidence=0.5,
        )
        assert ok is True

        # Step 4: 子代理消费暂存队列
        feedback_result = sub_agent.consume_pending()
        assert feedback_result.processed_count == 1
        assert len(feedback_result.new_entries) == 1
        assert len(feedback_result.compiled_skills) == 1

        # 验证路由表最终状态
        assert storage.count_routing_entries() >= 2
