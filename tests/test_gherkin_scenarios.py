"""Gherkin 契约验收测试 — 规避洞察路由表相关场景。

Gherkin.md 是系统行为的法律文本。本文件把它描述的场景转写为可执行断言，
确保重构不会让实现悄悄偏离契约。

当前覆盖：
- Feature 1 场景2：基于四维排序触发地图分裂（分裂判据）
- 支撑性回归：sample_count 累积、子类型直方图（分裂判据的输入）
"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, Tag
from src.pending_queue import PendingQueue
from src.storage import Storage
from src.sub_agent import SubAgent

# ══════════════════════════════════════════════════════════════════
# 夹具与构造辅助
# ══════════════════════════════════════════════════════════════════


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


def _make_entry(
    category_id: str,
    *,
    freq: float = 10.0,
    impact: float = 0.9,
    sample_count: float = 10.0,
    subtypes: dict[str, float] | None = None,
) -> RoutingTableEntry:
    """构造测试节点。

    focus_description 刻意以「流程」结尾——旧实现用
    ``focus.split()[-1][:20]`` 生成子节点名，会产出 "流程" 这个无意义
    的类别，相关断言用于锁定新行为。

    Args:
        subtypes: 子类型观测次数，如 {"read": 8, "connect": 2}。
            通过 record_subtype 逐次累积，不直接写 stats 内部键，
            以免测试与直方图的存储格式耦合。
    """
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="",
        focus_description=f"聚焦 {category_id} 的处理流程",
        boundary_rules=f"仅处理 {category_id}，不处理其他错误",
        logic_signature="执行针对性修复动作",
    )
    entry = RoutingTableEntry(
        category_id=category_id,
        stats={
            "freq": freq,
            "impact": impact,
            "trend": 0.0,
            "recover_cost": 1.0,
            "sample_count": sample_count,
        },
        local_map=lm,
        tags={Tag("状态_实验性")},
    )
    for name, count in (subtypes or {}).items():
        for _ in range(int(count)):
            entry.record_subtype(name)
    return entry


# ══════════════════════════════════════════════════════════════════
# Feature 1 场景2：基于四维排序触发地图分裂
# ══════════════════════════════════════════════════════════════════


class TestGherkinF1Scenario2Split:
    """Gherkin F1 场景2：

    假设 路由表中已有父节点的综合优先级评分连续3次进入Top 3
    并且 该节点下子分类占比超过总超时的70%
    当 子代理执行路由表维护周期时
    那么 子代理应执行「地图下钻（Split）」，分裂出子节点
    并且 父节点的 maintenance_log 必须追加记述分裂原因
    """

    def test_split_requires_three_consecutive_top_entries(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """连续 3 次进入 Top3 且主导子类型占比 >70% 才分裂。"""
        storage.upsert_routing_entry(
            _make_entry("network.timeout", subtypes={"read": 8, "connect": 2})
        )

        # 第 1、2 轮：连续次数不足，不得分裂
        for _ in range(2):
            result = agent.maintain(prune_threshold=0.0)
            assert result["split"] == 0
            assert storage.get_routing_entry("network.timeout.read") is None

        # 第 3 轮：连续 3 次达成，下钻出以主导子类型命名的子节点
        result = agent.maintain(prune_threshold=0.0)
        assert result["split"] == 1

        child = storage.get_routing_entry("network.timeout.read")
        assert child is not None
        assert child.local_map.parent_path == "network.timeout"

    def test_split_records_reason_in_parent_log(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """父节点 maintenance_log 必须记述分裂原因。"""
        storage.upsert_routing_entry(
            _make_entry("network.timeout", subtypes={"read": 8, "connect": 2})
        )
        for _ in range(3):
            agent.maintain(prune_threshold=0.0)

        parent = storage.get_routing_entry("network.timeout")
        assert parent is not None
        split_logs = [
            log for log in parent.local_map.maintenance_log if log.action == "split"
        ]
        assert split_logs, "分裂后父节点必须留下 split 记述"
        assert "read" in split_logs[-1].reason

    def test_no_split_when_no_dominant_subtype(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """子类型占比未超阈值时，即使连续进入 Top 也不分裂。"""
        storage.upsert_routing_entry(
            _make_entry("network.timeout", subtypes={"read": 5, "connect": 5})
        )

        for _ in range(3):
            result = agent.maintain(prune_threshold=0.0)

        assert result["split"] == 0
        assert storage.get_routing_entry("network.timeout.read") is None
        assert storage.get_routing_entry("network.timeout.connect") is None

    def test_no_split_when_samples_insufficient(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """样本数不足时，占比不具备统计意义，不分裂。"""
        storage.upsert_routing_entry(
            _make_entry(
                "network.timeout", sample_count=3, subtypes={"read": 3}
            )
        )

        for _ in range(3):
            result = agent.maintain(prune_threshold=0.0)

        assert result["split"] == 0
        assert storage.get_routing_entry("network.timeout.read") is None

    def test_no_immediate_resplit_after_success(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """分裂成功后重置连续计数与已分裂子类型，不得立即重复分裂。"""
        storage.upsert_routing_entry(
            _make_entry("network.timeout", subtypes={"read": 8, "connect": 2})
        )
        for _ in range(3):
            agent.maintain(prune_threshold=0.0)
        assert storage.get_routing_entry("network.timeout.read") is not None

        # 第 4 轮：连续计数已归零，不应再次分裂
        result = agent.maintain(prune_threshold=0.0)
        assert result["split"] == 0

    def test_child_name_comes_from_dominant_subtype(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """子节点名取自观测中占比最高的子类型，而非 focus 描述尾词。"""
        entry = _make_entry(
            "network.timeout", subtypes={"connect": 9, "read": 1}
        )
        assert entry.local_map.focus_description.endswith("流程")

        storage.upsert_routing_entry(entry)
        for _ in range(3):
            agent.maintain(prune_threshold=0.0)

        assert storage.get_routing_entry("network.timeout.connect") is not None
        assert storage.get_routing_entry("network.timeout.流程") is None

    def test_no_split_beyond_max_depth(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """已达最大分裂深度的节点不再下钻。"""
        storage.upsert_routing_entry(
            _make_entry("network.timeout.read", subtypes={"slow": 9, "fast": 1})
        )

        for _ in range(3):
            result = agent.maintain(prune_threshold=0.0)

        assert result["split"] == 0
        assert storage.get_routing_entry("network.timeout.read.slow") is None


# ══════════════════════════════════════════════════════════════════
# 支撑性回归：分裂判据的输入必须真实存在
# ══════════════════════════════════════════════════════════════════


class TestSplitPrerequisiteStats:
    """分裂判据依赖 sample_count 与子类型直方图。

    历史上二者都缺失过：sample_count 从未被累积，导致判据恒不成立、
    分裂功能实际是死代码。此处锁定其累积行为。
    """

    def test_distill_initializes_sample_count(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """新建节点时 sample_count 必须为 1。"""
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_429"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "retry", "impact_scope": "external_api"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "recovered"}},
        ]
        agent._log_reader = lambda: logs

        agent.distill()
        entry = storage.get_routing_entry("network.http_429")
        assert entry is not None
        assert float(entry.stats["sample_count"]) == 1.0

    def test_distill_increments_sample_count_on_update(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """重复蒸馏同一错误时 sample_count 递增。"""
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_429"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "retry", "impact_scope": "external_api"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "recovered"}},
        ]
        agent._log_reader = lambda: logs

        agent.distill()
        agent.distill()

        entry = storage.get_routing_entry("network.http_429")
        assert entry is not None
        assert float(entry.stats["sample_count"]) == 2.0

    def test_distill_accumulates_subtype_histogram(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """蒸馏需累积子类型直方图，供主导占比判据使用。"""
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "TIMEOUT", "subtype": "read"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "retry", "impact_scope": "internal"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "ok"}},
        ]
        agent._log_reader = lambda: logs

        agent.distill()
        entry = storage.get_routing_entry("network.timeout")
        assert entry is not None
        assert entry.dominant_subtype() == ("read", 1.0)

    def test_subtype_falls_back_to_fix_action(
        self, agent: SubAgent, storage: Storage
    ) -> None:
        """日志未标注子类型时回退到修复动作，避免直方图永远为空。"""
        logs = [
            {"session_id": "s1", "event_type": "error",
             "content": {"error_code": "HTTP_500"}},
            {"session_id": "s1", "event_type": "tool_call",
             "content": {"tool": "circuit_breaker", "impact_scope": "internal"}},
            {"session_id": "s1", "event_type": "success",
             "content": {"status": "ok"}},
        ]
        agent._log_reader = lambda: logs

        agent.distill()
        entry = storage.get_routing_entry("network.http_500")
        assert entry is not None
        assert entry.dominant_subtype() == ("circuit_breaker", 1.0)
