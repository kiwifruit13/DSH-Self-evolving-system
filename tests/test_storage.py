"""存储层单元测试 — 覆盖路由表 / 暂存队列 / Skill 库 的 CRUD。"""
from pathlib import Path

import pytest

from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    SkillStep,
    SpecializedSkill,
    Tag,
    UnclassifiedFailurePackage,
)
from src.storage import Storage

# ══════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


def _make_entry(category_id: str = "network.rate_limit.429") -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="root.network",
        focus_description="聚焦 HTTP 429",
        boundary_rules="仅处理 HTTP 429",
        logic_signature="指数退避重试",
    )
    lm.append_log("create", "初始创建", "human")
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 100.0, "impact": 0.9, "trend": 0.3, "recover_cost": 0.2},
        local_map=lm,
        tags={Tag("状态_实验性"), Tag("场景_第三方依赖")},
        primary_skill_id="skill_retry_429",
    )


# ══════════════════════════════════════════════════════════════════
# 路由表 CRUD
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableStorage:
    def test_upsert_and_get(self, storage: Storage) -> None:
        entry = _make_entry()
        storage.upsert_routing_entry(entry)
        retrieved = storage.get_routing_entry("network.rate_limit.429")
        assert retrieved is not None
        assert retrieved.category_id == "network.rate_limit.429"
        assert retrieved.primary_skill_id == "skill_retry_429"
        assert len(retrieved.tags) == 2

    def test_upsert_overwrites(self, storage: Storage) -> None:
        entry = _make_entry()
        storage.upsert_routing_entry(entry)

        updated = _make_entry("network.rate_limit.429")
        updated.primary_skill_id = "skill_retry_v2"
        updated.stats = {"freq": 200.0, "impact": 0.95, "trend": 0.5, "recover_cost": 0.15}
        storage.upsert_routing_entry(updated)

        retrieved = storage.get_routing_entry("network.rate_limit.429")
        assert retrieved is not None
        assert retrieved.primary_skill_id == "skill_retry_v2"
        assert retrieved.stats["freq"] == 200.0

    def test_get_nonexistent(self, storage: Storage) -> None:
        assert storage.get_routing_entry("nonexistent") is None

    def test_query_by_root_category(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry("network.rate_limit.429"))
        entry2 = _make_entry("data_parsing.graphql")
        entry2.category_id = "data_parsing.graphql"
        storage.upsert_routing_entry(entry2)

        results = storage.query_routing_entries(root_category="network")
        assert len(results) == 1
        assert results[0].category_id == "network.rate_limit.429"

    def test_query_by_tags(self, storage: Storage) -> None:
        entry1 = _make_entry("network.rate_limit.429")
        entry1.tags = {Tag("状态_实验性"), Tag("场景_第三方依赖")}
        storage.upsert_routing_entry(entry1)

        entry2 = _make_entry("data_parsing.graphql")
        entry2.tags = {Tag("状态_稳定")}
        storage.upsert_routing_entry(entry2)

        results = storage.query_routing_entries(tags={Tag("场景_第三方依赖")})
        assert len(results) == 1
        assert results[0].category_id == "network.rate_limit.429"

    def test_delete(self, storage: Storage) -> None:
        storage.upsert_routing_entry(_make_entry())
        assert storage.count_routing_entries() == 1
        assert storage.delete_routing_entry("network.rate_limit.429") is True
        assert storage.count_routing_entries() == 0
        assert storage.delete_routing_entry("network.rate_limit.429") is False

    def test_count(self, storage: Storage) -> None:
        assert storage.count_routing_entries() == 0
        storage.upsert_routing_entry(_make_entry())
        assert storage.count_routing_entries() == 1

    # ══════════════════════════════════════════════════════════════════
    # Step 84: parent_path 过滤下推到 SQL
    # ══════════════════════════════════════════════════════════════════

    def test_query_by_parent_path(self, storage: Storage) -> None:
        """query_routing_entries(parent_path=X) 只返回以 X 为 parent_path 的条目。"""
        # 创建不同父路径的节点
        a = _make_entry("network.timeout.read")
        a.local_map.parent_path = "network.timeout"
        storage.upsert_routing_entry(a)

        b = _make_entry("network.timeout.connect")
        b.local_map.parent_path = "network.timeout"
        storage.upsert_routing_entry(b)

        c = _make_entry("data_parsing.json")
        c.local_map.parent_path = "data_parsing.format"
        storage.upsert_routing_entry(c)

        # 只查询 parent_path = "network.timeout"
        results = storage.query_routing_entries(parent_path="network.timeout")
        assert len(results) == 2
        ids = {r.category_id for r in results}
        assert ids == {"network.timeout.read", "network.timeout.connect"}

    def test_query_parent_path_combined_with_root_category(self, storage: Storage) -> None:
        """parent_path + root_category 组合过滤。"""
        a = _make_entry("network.a")
        a.local_map.parent_path = "root.network"
        storage.upsert_routing_entry(a)

        b = _make_entry("data_parsing.b")
        b.local_map.parent_path = "root.data_parsing"
        storage.upsert_routing_entry(b)

        results = storage.query_routing_entries(
            root_category="network", parent_path="root.network"
        )
        assert len(results) == 1
        assert results[0].category_id == "network.a"

    def test_query_parent_path_no_match(self, storage: Storage) -> None:
        """parent_path 无匹配时返回空列表。"""
        storage.upsert_routing_entry(_make_entry("network.a"))
        results = storage.query_routing_entries(parent_path="nonexistent.parent")
        assert len(results) == 0

    def test_maintenance_log_roundtrip(self, storage: Storage) -> None:
        entry = _make_entry()
        entry.local_map.append_log("update", "修改边界", "sub_agent")
        entry.local_map.append_log("split", "分裂子节点", "sub_agent")
        storage.upsert_routing_entry(entry)

        retrieved = storage.get_routing_entry("network.rate_limit.429")
        assert retrieved is not None
        assert len(retrieved.local_map.maintenance_log) == 3
        actions = [log.action for log in retrieved.local_map.maintenance_log]
        assert actions == ["create", "update", "split"]


# ══════════════════════════════════════════════════════════════════
# 暂存队列 CRUD
# ══════════════════════════════════════════════════════════════════

class TestPendingQueueStorage:
    def test_enqueue_and_dequeue(self, storage: Storage) -> None:
        pkg = UnclassifiedFailurePackage(
            error_stack="GraphQL: Field 'user' not found",
            context_snapshot={"session_id": "s1"},
            attempted_strategies=["retry"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        pkg2 = UnclassifiedFailurePackage(
            error_stack="SSL error",
            context_snapshot={"session_id": "s2"},
            location_guess="network",
            confidence=0.5,
        )
        storage.enqueue_feedback(pkg)
        storage.enqueue_feedback(pkg2)

        assert storage.pending_count() == 2
        dequeued = storage.dequeue_feedback(limit=1)
        assert len(dequeued) == 1
        assert dequeued[0].error_stack == "GraphQL: Field 'user' not found"
        assert storage.pending_count() == 1

    def test_dequeue_empty(self, storage: Storage) -> None:
        assert storage.dequeue_feedback() == []

    def test_dequeue_marks_processed(self, storage: Storage) -> None:
        pkg = UnclassifiedFailurePackage(
            error_stack="err", context_snapshot={}, confidence=0.5
        )
        storage.enqueue_feedback(pkg)
        storage.dequeue_feedback(limit=1)
        # 再次 dequeue 应返回空
        assert storage.dequeue_feedback() == []


# ══════════════════════════════════════════════════════════════════
# Skill 库 CRUD
# ══════════════════════════════════════════════════════════════════

class TestSkillStorage:
    def _make_skill(self) -> SpecializedSkill:
        overview = LocalMindMap(
            node_id="skill_ssl",
            parent_path="root.network.ssl",
            focus_description="SSL 修复",
            boundary_rules="仅处理 SSL 证书",
            logic_signature="三步校验",
        )
        skill = SpecializedSkill(
            skill_id="skill_ssl_cert",
            name="SSLCertFixSkill",
            overview_map=overview,
            tags={Tag("状态_稳定")},
        )
        skill.add_step(SkillStep(
            step_id="s1",
            action="校验证书",
            local_map=LocalMindMap(
                node_id="s1", parent_path="skill_ssl",
                focus_description="检查有效期",
                boundary_rules="仅检查有效期",
                logic_signature="比对当前时间",
            ),
        ))
        return skill

    def test_upsert_and_get(self, storage: Storage) -> None:
        skill = self._make_skill()
        storage.upsert_skill(skill)
        retrieved = storage.get_skill("skill_ssl_cert")
        assert retrieved is not None
        assert retrieved.name == "SSLCertFixSkill"
        assert len(retrieved.steps) == 1
        assert retrieved.steps[0].action == "校验证书"

    def test_upsert_overwrites(self, storage: Storage) -> None:
        skill = self._make_skill()
        storage.upsert_skill(skill)

        skill2 = self._make_skill()
        skill2.name = "SSLCertFixSkillV2"
        storage.upsert_skill(skill2)

        retrieved = storage.get_skill("skill_ssl_cert")
        assert retrieved is not None
        assert retrieved.name == "SSLCertFixSkillV2"

    def test_get_nonexistent(self, storage: Storage) -> None:
        assert storage.get_skill("nonexistent") is None
