"""子代理池 + Skill 运行时单元测试 — Agent-Builder 子代理专业化 + Skill 运行时化。"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, SpecializedSkill, Tag
from src.pending_queue import PendingQueue
from src.skill_compiler import SkillCompiler
from src.storage import Storage
from src.sub_agent_pool import SpecializedSubAgent, SubAgentPool


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def queue(storage: Storage) -> PendingQueue:
    return PendingQueue(storage, capacity=100)


@pytest.fixture
def pool(storage: Storage, queue: PendingQueue) -> SubAgentPool:
    return SubAgentPool(storage, queue)


def _make_entry(
    category_id: str,
    focus: str = "测试",
    boundary: str = "测试边界",
    logic: str = "测试逻辑",
) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path=f"root.{category_id.split('.')[0]}",
        focus_description=focus,
        boundary_rules=boundary,
        logic_signature=logic,
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 50, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
        local_map=lm,
        tags={Tag("状态_实验性")},
    )


# ══════════════════════════════════════════════════════════════════
# 专用子代理工厂
# ══════════════════════════════════════════════════════════════════

class TestSpecializedSubAgent:
    def test_create_specialized(self, storage: Storage) -> None:
        agent = SpecializedSubAgent(root_category="network", storage=storage)
        assert agent.root_category == "network"
        assert agent.category_prefix == "network."

    def test_entry_count_empty(self, storage: Storage) -> None:
        agent = SpecializedSubAgent(root_category="network", storage=storage)
        assert agent.entry_count() == 0

    def test_entry_count_nonempty(self, storage: Storage) -> None:
        for i in range(3):
            storage.upsert_routing_entry(_make_entry(f"network.node_{i}"))
        agent = SpecializedSubAgent(root_category="network", storage=storage)
        assert agent.entry_count() == 3

    def test_entry_count_ignores_other_categories(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.node_1"))
        storage.upsert_routing_entry(_make_entry("data_parsing.xml"))
        agent = SpecializedSubAgent(root_category="network", storage=storage)
        assert agent.entry_count() == 1

    def test_maintain_no_entries(self, storage: Storage) -> None:
        agent = SpecializedSubAgent(root_category="network", storage=storage)
        stats = agent.maintain()
        assert stats["pruned"] == []
        assert stats["quality_gated"] == []


# ══════════════════════════════════════════════════════════════════
# 子代理池
# ══════════════════════════════════════════════════════════════════

class TestSubAgentPool:
    def test_initial_state(self, pool: SubAgentPool) -> None:
        assert pool.specialized_count == 0
        assert pool.specialized_categories == []

    def test_create_specialized(self, pool: SubAgentPool) -> None:
        agent = pool.create_specialized("network")
        assert agent.root_category == "network"
        assert pool.specialized_count == 1
        assert pool.specialized_categories == ["network"]

    def test_create_specialized_idempotent(self, pool: SubAgentPool) -> None:
        agent1 = pool.create_specialized("network")
        agent2 = pool.create_specialized("network")
        assert agent1 is agent2
        assert pool.specialized_count == 1

    def test_create_multiple_specialized(self, pool: SubAgentPool) -> None:
        pool.create_specialized("network")
        pool.create_specialized("data_parsing")
        pool.create_specialized("llm_inference")
        assert pool.specialized_count == 3
        assert set(pool.specialized_categories) == {
            "network", "data_parsing", "llm_inference",
        }

    def test_remove_specialized(self, pool: SubAgentPool) -> None:
        pool.create_specialized("network")
        pool.remove_specialized("network")
        assert pool.specialized_count == 0

    def test_remove_nonexistent_specialized(self, pool: SubAgentPool) -> None:
        pool.remove_specialized("network")  # 不应抛异常
        assert pool.specialized_count == 0

    def test_get_specialized(self, pool: SubAgentPool) -> None:
        assert pool.get_specialized("network") is None
        pool.create_specialized("network")
        assert pool.get_specialized("network") is not None

    def test_auto_balance_creates_when_needed(self, pool: SubAgentPool, storage: Storage) -> None:
        # 创建 51 个 network 节点（超过默认阈值 50）
        for i in range(51):
            storage.upsert_routing_entry(_make_entry(f"network.node_{i}"))
        created = pool.auto_balance(threshold=50)
        assert "network" in created
        assert pool.specialized_count == 1

    def test_auto_balance_skips_below_threshold(self, pool: SubAgentPool, storage: Storage) -> None:
        for i in range(10):
            storage.upsert_routing_entry(_make_entry(f"network.node_{i}"))
        created = pool.auto_balance(threshold=50)
        assert "network" not in created
        assert pool.specialized_count == 0

    def test_auto_balance_skip_existing(self, pool: SubAgentPool, storage: Storage) -> None:
        for i in range(51):
            storage.upsert_routing_entry(_make_entry(f"network.node_{i}"))
        pool.create_specialized("network")
        created = pool.auto_balance(threshold=50)
        assert "network" not in created  # 已存在，不重复创建

    def test_pool_summary(self, pool: SubAgentPool, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.node_1"))
        pool.create_specialized("network")
        summary = pool.pool_summary()
        assert summary["total_agents"] == 2
        assert summary["specialized_agents"][0]["root_category"] == "network"

    def test_maintain_dispatches_to_specialized(self, pool: SubAgentPool, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry(
            "network.redundant",
            focus="聚焦修复",
            boundary="仅处理 HTTP 修复",
            logic="基于反馈举证自动生成",
        ))
        pool.create_specialized("network")
        results = pool.maintain(quality_delta_min=0.1)
        assert "network" in results["specialized"]
        # 低质量节点应被标记
        network_stats = results["specialized"]["network"]
        assert len(network_stats["quality_gated"]) >= 1


# ══════════════════════════════════════════════════════════════════
# Skill 运行时工具集推断
# ══════════════════════════════════════════════════════════════════

class TestSkillRuntime:
    def test_compile_tool_pattern_infer_tools(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "network.http_429",
            focus="处理 HTTP 429 限流",
            boundary="禁止重试超过 3 次；指数退避策略；限流处理",
        )
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "tool"
        assert "http_client" in skill.tools
        assert "retry" in skill.tools
        assert "rate_limiter" in skill.tools

    def test_compile_domain_pattern_infer_tools(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "data_parsing.xml_parse",
            focus="解析 XML 数据",
            boundary="检测 XML 格式；使用标准库解析；处理非法结构",
        )
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "domain"
        assert "json_parser" in skill.tools
        assert "xml_parser" in skill.tools

    def test_compile_workflow_pattern_infer_tools(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "llm_inference.timeout",
            focus="处理 LLM 推理超时",
            boundary="监控 token 消耗；处理流式中断",
        )
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "workflow"
        assert "llm_api" in skill.tools
        assert "token_counter" in skill.tools

    def test_compile_memory_pattern_infer_tools(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "permission.forbidden",
            focus="处理权限被拒",
            boundary="检查权限策略；记录审计日志",
        )
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "memory"
        assert "memory_store" in skill.tools
        assert "policy_engine" in skill.tools
        assert "audit_logger" in skill.tools

    def test_compile_infer_context_keys(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "network.http_timeout",
            focus="处理 HTTP 超时",
            boundary="检查用户权限；设置数据库超时",
        )
        skill = compiler.compile_from_entry(entry)
        assert "http_config" in skill.context_keys
        assert "timeout" in skill.context_keys
        assert "user_context" in skill.context_keys
        assert "db_config" in skill.context_keys

    def test_compile_generic_pattern(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "permission.custom_node",
            focus="通用处理",
            boundary="标准处理流程",
        )
        # permission 应映射到 memory 模式
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "memory"
        assert skill.tools  # 不应为空

    def test_tools_and_context_keys_serialization(self, storage: Storage) -> None:
        compiler = SkillCompiler(storage)
        entry = _make_entry(
            "network.http_500",
            focus="处理 HTTP 500 错误",
            boundary="指数退避重试",
        )
        skill = compiler.compile_from_entry(entry)
        d = skill.to_dict()
        assert "tools" in d
        assert "context_keys" in d
        assert d["tools"] == skill.tools
        assert d["context_keys"] == skill.context_keys

        restored = SpecializedSkill.from_dict(d)
        assert restored.tools == skill.tools
        assert restored.context_keys == skill.context_keys

