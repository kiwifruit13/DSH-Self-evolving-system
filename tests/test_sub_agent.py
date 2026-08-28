"""子代理单元测试 — 蒸馏 / 暂存消费 / 路由维护 / Skill 孵化。"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, Tag
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
def agent(storage: Storage, queue: PendingQueue) -> SubAgent:
    return SubAgent(storage, queue)


# ══════════════════════════════════════════════════════════════════
# 日志蒸馏
# ══════════════════════════════════════════════════════════════════

class TestSubAgentDistill:
    def test_distill_with_log_reader(self, agent: SubAgent, storage: Storage) -> None:
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_429", "description": "Rate limit exceeded"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "exponential_backoff_retry", "impact_scope": "external_api"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "recovered"}},
        ]
        agent._log_reader = lambda: logs

        result = agent.distill()
        assert result.total_distilled == 1
        assert len(result.new_entries) == 1
        assert result.new_entries[0].category_id.startswith("network.")

    def test_distill_no_log_reader(self, agent: SubAgent) -> None:
        result = agent.distill()
        assert result.total_distilled == 0

    def test_distill_incomplete_chain(self, agent: SubAgent, storage: Storage) -> None:
        """缺少 success 事件时不应生成条目。"""
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_500"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "retry"}},
            # 缺少 success 事件
        ]
        agent._log_reader = lambda: logs

        result = agent.distill()
        assert result.total_distilled == 0

    def test_distill_update_existing(self, agent: SubAgent, storage: Storage) -> None:
        """已有节点时应更新而非新建。"""
        # 预置节点
        lm = LocalMindMap(
            node_id="network.http_429", parent_path="root.network",
            focus_description="聚焦 HTTP 429",
            boundary_rules="仅处理 HTTP 429",
            logic_signature="重试",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            category_id="network.http_429",
            stats={"freq": 5, "impact": 0.8, "trend": 0.0, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_实验性")},
        ))

        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_429"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "retry", "impact_scope": "internal"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "ok"}},
        ]
        agent._log_reader = lambda: logs

        result = agent.distill()
        assert len(result.new_entries) == 0
        assert len(result.updated_entries) == 1
        assert result.updated_entries[0].stats["freq"] == 6


# ══════════════════════════════════════════════════════════════════
# 暂存队列消费
# ══════════════════════════════════════════════════════════════════

class TestSubAgentConsumePending:
    def test_consume_pending_creates_entry_and_skill(self, agent: SubAgent, queue: PendingQueue) -> None:
        from src.models import UnclassifiedFailurePackage
        pkg = UnclassifiedFailurePackage(
            error_stack="GraphQL: Field 'user' not found",
            context_snapshot={"session_id": "s1"},
            attempted_strategies=["retry"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        queue.enqueue(pkg)

        result = agent.consume_pending(batch_size=5)
        assert result.processed_count == 1
        assert len(result.new_entries) == 1
        assert len(result.compiled_skills) == 1
        assert result.new_entries[0].category_id.startswith("data_parsing.")

    def test_consume_pending_empty(self, agent: SubAgent) -> None:
        result = agent.consume_pending()
        assert result.processed_count == 0

    def test_consume_pending_invalid_root(self, agent: SubAgent, queue: PendingQueue) -> None:
        from src.models import UnclassifiedFailurePackage
        pkg = UnclassifiedFailurePackage(
            error_stack="Some error",
            context_snapshot={},
            location_guess="invalid_root",  # 非法根分类
            confidence=0.5,
        )
        queue.enqueue(pkg)

        result = agent.consume_pending()
        assert result.processed_count == 1
        assert len(result.new_entries) == 1
        # 应降级为 network
        assert result.new_entries[0].category_id.startswith("network.")


# ══════════════════════════════════════════════════════════════════
# 路由维护
# ══════════════════════════════════════════════════════════════════

class TestSubAgentMaintain:
    def test_maintain_empty(self, agent: SubAgent) -> None:
        stats = agent.maintain()
        assert stats["split"] == 0
        assert stats["pruned"] == []

    def test_maintain_prune(self, agent: SubAgent, storage: Storage) -> None:
        # 预置低分节点
        for i in range(5):
            lm = LocalMindMap(
                node_id=f"network.low_{i}", parent_path="root.network",
                focus_description=f"低分节点 {i}",
                boundary_rules=f"边界 {i}",
                logic_signature=f"逻辑 {i}",
            )
            storage.upsert_routing_entry(RoutingTableEntry(
                category_id=f"network.low_{i}",
                stats={"freq": 0.1, "impact": 0.01, "trend": -0.9, "recover_cost": 9},
                local_map=lm,
                tags={Tag("状态_实验性")},
            ))

    def test_maintain_quality_gate(self, agent: SubAgent, storage: Storage) -> None:
        """低质量节点（全为冗余空话）应在维护中被标记。"""
        lm = LocalMindMap(
            node_id="network.redundant", parent_path="root.network",
            focus_description="聚焦修复",
            boundary_rules="仅处理 HTTP 修复",
            logic_signature="基于反馈举证自动生成",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            category_id="network.redundant",
            stats={"freq": 50, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
            local_map=lm,
            tags={Tag("状态_实验性")},
        ))

        stats = agent.maintain(quality_delta_min=0.1)
        assert len(stats["quality_gated"]) >= 1
        assert stats["quality_gated"][0]["category_id"] == "network.redundant"
        assert stats["quality_gated"][0]["quality_level"] == "redundant"

        # 验证维护日志中已记录
        updated = storage.get_routing_entry("network.redundant")
        assert updated is not None
        quality_logs = [
            entry_log for entry_log in updated.local_map.maintenance_log
            if "知识增量" in entry_log.reason
        ]
        assert len(quality_logs) >= 1


# ══════════════════════════════════════════════════════════════════
# Skill 孵化
# ══════════════════════════════════════════════════════════════════

class TestSubAgentCompileSkills:
    def test_compile_skills_top_k(self, agent: SubAgent, storage: Storage) -> None:
        # 预置两个无 Skill 的节点（含专家信号通过质量门禁）
        for cat_id in ("network.a", "network.b"):
            lm = LocalMindMap(
                node_id=cat_id, parent_path="root.network",
                focus_description="处理 HTTP 429 限流",
                boundary_rules="禁止重试超过 3 次；指数退避策略",
                logic_signature="retry with backoff",
            )
            storage.upsert_routing_entry(RoutingTableEntry(
                category_id=cat_id,
                stats={"freq": 50, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
                local_map=lm,
                tags={Tag("状态_实验性")},
                primary_skill_id=None,
            ))

        skills = agent.compile_skills(top_k=5)
        assert len(skills) == 2

    def test_compile_skills_skip_existing(self, agent: SubAgent, storage: Storage) -> None:
        # 预置一个已有 Skill 的节点
        lm = LocalMindMap(
            node_id="network.existing", parent_path="root.network",
            focus_description="已有",
            boundary_rules="边界",
            logic_signature="逻辑",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            category_id="network.existing",
            stats={"freq": 100, "impact": 0.9, "trend": 0.5, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_实验性")},
            primary_skill_id="skill_already_exists",
        ))

        skills = agent.compile_skills(top_k=5)
        assert len(skills) == 0

    def test_compile_skills_skip_low_quality(self, agent: SubAgent, storage: Storage) -> None:
        """低质量节点（全为冗余空话）应被跳过编译并记录日志。"""
        lm = LocalMindMap(
            node_id="network.low_quality",
            parent_path="root.network",
            focus_description="聚焦修复",
            boundary_rules="仅处理网络修复",
            logic_signature="待优化",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            category_id="network.low_quality",
            stats={"freq": 100, "impact": 0.9, "trend": 0.5, "recover_cost": 1},
            local_map=lm,
            tags={Tag("状态_实验性")},
            primary_skill_id=None,
        ))

        skills = agent.compile_skills(top_k=5)
        # 低质量节点应被跳过，不编译 Skill
        assert len(skills) == 0

        # 验证维护日志中有跳过记录
        updated = storage.get_routing_entry("network.low_quality")
        assert updated is not None
        skipped_logs = [
            entry_log for entry_log in updated.local_map.maintenance_log
            if "跳过" in entry_log.reason or "skip" in entry_log.reason
        ]
        assert len(skipped_logs) >= 1
