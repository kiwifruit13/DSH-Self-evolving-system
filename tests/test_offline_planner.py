"""离线规划器单元测试 — 三阶段规划全流程。"""
from pathlib import Path

import pytest

from src.models import Tag, UnclassifiedFailurePackage
from src.offline_planner import OfflinePlanner
from src.pending_queue import PendingQueue
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
def planner(storage: Storage, queue: PendingQueue) -> OfflinePlanner:
    return OfflinePlanner(storage, queue)


def _make_pkg(
    error_stack: str = "GraphQL: Field 'user' not found",
    location_guess: str = "data_parsing",
    confidence: float = 0.7,
    strategies: list[str] | None = None,
) -> UnclassifiedFailurePackage:
    return UnclassifiedFailurePackage(
        error_stack=error_stack,
        context_snapshot={"session_id": "test_session"},
        attempted_strategies=strategies or ["retry"],
        location_guess=location_guess,
        confidence=confidence,
    )


# ══════════════════════════════════════════════════════════════════
# 三阶段规划
# ══════════════════════════════════════════════════════════════════

class TestOfflinePlanner:
    def test_accepted_pkg_creates_entry_and_skill(
        self, planner: OfflinePlanner, queue: PendingQueue
    ) -> None:
        pkg = _make_pkg(confidence=0.7)
        queue.enqueue(pkg)

        report = planner.plan(batch_size=5)

        assert report.total_processed == 1
        assert report.accepted == 1
        assert report.rejected == 0

        decision = report.decisions[0]
        assert decision.rejected is False
        assert decision.created_entry is not None
        assert decision.compiled_skill is not None
        assert decision.candidate_category_id.startswith("data_parsing.")

        # 验证三阶段
        phase_names = [p.phase for p in decision.phases]
        assert phase_names == ["analyze", "validate", "deploy"]

    def test_rejected_pkg_due_to_overlap(
        self, planner: OfflinePlanner, queue: PendingQueue, storage: Storage
    ) -> None:
        """当已有高重叠节点时，举证包应被拒绝。"""
        from src.models import LocalMindMap, RoutingTableEntry, Tag

        # 预置一个相似节点（边界规则风格与规划器生成的一致）
        lm = LocalMindMap(
            node_id="data_parsing.graphql_field_user_not_found",
            parent_path="root.data_parsing",
            focus_description="聚焦 GraphQL 字段缺失",
            boundary_rules="仅处理 GraphQL，已尝试策略: retry",
            logic_signature="GraphQL: Field 'user' not found",
        )
        storage.upsert_routing_entry(RoutingTableEntry(
            category_id="data_parsing.graphql_field_user_not_found",
            stats={"freq": 10, "impact": 0.8, "trend": 0.0, "recover_cost": 2},
            local_map=lm,
            tags={Tag("状态_实验性")},
        ))

        # 提交一个高度相似的举证包
        pkg = _make_pkg(
            error_stack="GraphQL: Field 'user' not found",
            location_guess="data_parsing",
            confidence=0.9,
            strategies=["retry"],
        )
        queue.enqueue(pkg)

        report = planner.plan(batch_size=5)

        assert report.total_processed == 1
        assert report.rejected == 1
        assert report.accepted == 0

        decision = report.decisions[0]
        assert decision.rejected is True
        assert "重叠率" in decision.rejection_reason
        assert "拒绝创建" in decision.rejection_reason

    def test_accepted_pkg_validates_overlap(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        """通过的举证包，其决策记录应包含重叠率信息。"""
        pkg = _make_pkg(
            error_stack="NewUnknownError: Something broke",
            location_guess="network",
            confidence=0.6,
        )
        queue.enqueue(pkg)

        report = planner.plan(batch_size=5)

        assert report.accepted == 1
        decision = report.decisions[0]
        assert decision.overlap_result is not None
        assert decision.overlap_result["max_overlap"] < 0.7

    def test_plan_empty_queue(self, planner: OfflinePlanner) -> None:
        report = planner.plan()
        assert report.total_processed == 0
        assert report.accepted == 0
        assert report.rejected == 0

    def test_multiple_packages_mixed(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        """混合包：部分通过部分拒绝。"""
        from src.models import LocalMindMap, RoutingTableEntry, Tag

        # 预置一个会阻挡高置信度 GraphQL 错误的节点（匹配规划器生成的边界风格）
        lm = LocalMindMap(
            node_id="data_parsing.graphql_field_user_not_found",
            parent_path="root.data_parsing",
            focus_description="GraphQL 错误",
            boundary_rules="仅处理 GraphQL，已尝试策略: retry",
            logic_signature="GraphQL: Field 'user' not found",
        )
        planner._storage.upsert_routing_entry(RoutingTableEntry(
            category_id="data_parsing.graphql_field_user_not_found",
            stats={"freq": 5, "impact": 0.5, "trend": 0.0, "recover_cost": 3},
            local_map=lm,
            tags={Tag("状态_实验性")},
        ))

        pkg1 = _make_pkg(
            error_stack="GraphQL: Field 'user' not found",
            location_guess="data_parsing",
            confidence=0.85,
            strategies=["retry"],
        )
        pkg2 = _make_pkg(
            error_stack="NetworkTimeout: Connection refused",
            location_guess="network",
            confidence=0.6,
            strategies=["retry"],
        )
        queue.enqueue(pkg1)
        queue.enqueue(pkg2)

        report = planner.plan(batch_size=5)

        assert report.total_processed == 2
        # pkg1 应被拒绝（与预置节点高重叠），pkg2 应通过
        assert report.rejected >= 1
        assert report.accepted >= 1

    def test_report_to_dict(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        pkg = _make_pkg(confidence=0.5)
        queue.enqueue(pkg)
        report = planner.plan()
        d = report.to_dict()
        assert "total_processed" in d
        assert "acceptance_rate" in d
        assert "decisions" in d

    def test_custom_threshold(self, storage: Storage, queue: PendingQueue) -> None:
        """自定义重叠率阈值。"""
        planner = OfflinePlanner(storage, queue, overlap_threshold=0.5)
        pkg = _make_pkg(
            error_stack="SomeNewError: totally unique thing",
            location_guess="network",
            confidence=0.6,
        )
        queue.enqueue(pkg)
        report = planner.plan()
        assert report.accepted == 1


# ══════════════════════════════════════════════════════════════════
# 置信度标签分配
# ══════════════════════════════════════════════════════════════════

class TestConfidenceTagging:
    def test_high_confidence_tags(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        pkg = _make_pkg(confidence=0.9)
        queue.enqueue(pkg)
        report = planner.plan()
        assert report.accepted == 1
        entry = report.decisions[0].created_entry
        assert entry is not None
        assert Tag("状态_实验性") in entry.tags
        assert Tag("场景_第三方依赖") in entry.tags

    def test_medium_confidence_tags(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        pkg = _make_pkg(confidence=0.6)
        queue.enqueue(pkg)
        report = planner.plan()
        assert report.accepted == 1
        entry = report.decisions[0].created_entry
        assert entry is not None
        assert Tag("状态_实验性") in entry.tags
        assert Tag("场景_内部微服务") in entry.tags

    def test_low_confidence_no_scenario_tag(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        pkg = _make_pkg(confidence=0.3)
        queue.enqueue(pkg)
        report = planner.plan()
        assert report.accepted == 1
        entry = report.decisions[0].created_entry
        assert entry is not None
        # 低置信度不应有场景标签（待人工审核）
        scenario_tags = [t for t in entry.tags if "场景_" in t.value]
        assert len(scenario_tags) == 0

    def test_cost_tag_by_root(self, planner: OfflinePlanner, queue: PendingQueue) -> None:
        pkg = _make_pkg(
            error_stack="LLM: Token limit exceeded",
            location_guess="llm_inference",
            confidence=0.8,
        )
        queue.enqueue(pkg)
        report = planner.plan()
        assert report.accepted == 1
        entry = report.decisions[0].created_entry
        assert entry is not None
        assert Tag("代价_高延迟") in entry.tags
