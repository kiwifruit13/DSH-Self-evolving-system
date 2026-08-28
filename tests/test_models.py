"""核心数据模型单元测试 — TDD：先红后绿。"""
from datetime import datetime

import pytest

from src.models import (
    ROOT_CATEGORIES,
    LocalMindMap,
    RoutingTableEntry,
    SkillStep,
    SpecializedSkill,
    Tag,
    TagPrefix,
    UnclassifiedFailurePackage,
)

# ══════════════════════════════════════════════════════════════════
# LocalMindMap
# ══════════════════════════════════════════════════════════════════

class TestLocalMindMap:
    def test_create_and_append_log(self) -> None:
        m = LocalMindMap(
            node_id="n1",
            parent_path="root",
            focus_description="聚焦修复 HTTP 429",
            boundary_rules="仅处理 HTTP 429，不处理 TCP 超时",
            logic_signature="指数退避重试",
        )
        assert m.maintenance_log == []
        m.append_log("create", "首次创建", "human")
        assert len(m.maintenance_log) == 1
        log = m.maintenance_log[0]
        assert log.action == "create"
        assert log.reason == "首次创建"
        assert log.actor == "human"

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        m = LocalMindMap(
            node_id="n2",
            parent_path="root.network",
            focus_description="聚焦 SSL 证书",
            boundary_rules="仅处理证书校验，不处理 TLS 握手",
            logic_signature="校验证书有效期",
        )
        m.append_log("update", "更新边界", "sub_agent")
        data = m.to_dict()
        assert data["node_id"] == "n2"
        assert len(data["maintenance_log"]) == 1
        restored = LocalMindMap.from_dict(data)
        assert restored.node_id == "n2"
        assert restored.boundary_rules == "仅处理证书校验，不处理 TLS 握手"
        assert len(restored.maintenance_log) == 1


# ══════════════════════════════════════════════════════════════════
# Tag
# ══════════════════════════════════════════════════════════════════

class TestTag:
    def test_valid_tags(self) -> None:
        for tag_str, expected_prefix, expected_body in [
            ("状态_稳定", TagPrefix.STATUS, "稳定"),
            ("代价_低消耗", TagPrefix.COST, "低消耗"),
            ("场景_第三方依赖", TagPrefix.SCENARIO, "第三方依赖"),
        ]:
            t = Tag(tag_str)
            assert t.value == tag_str
            assert t.prefix == expected_prefix
            assert t.body == expected_body

    def test_empty_value_raises(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            Tag("")

    def test_missing_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="缺少合法前缀"):
            Tag("稳定")

    def test_invalid_body_raises(self) -> None:
        with pytest.raises(ValueError, match="不在允许列表"):
            Tag("状态_未知值")

    def test_frozen_and_hashable(self) -> None:
        t = Tag("代价_低消耗")
        s = {t}  # 可放入 set
        assert len(s) == 1
        with pytest.raises(AttributeError):
            t.value = "其它"  # frozen

    def test_equality(self) -> None:
        assert Tag("状态_稳定") == Tag("状态_稳定")
        assert Tag("状态_稳定") != Tag("状态_实验性")


# ══════════════════════════════════════════════════════════════════
# RoutingTableEntry
# ══════════════════════════════════════════════════════════════════

class TestRoutingTableEntry:
    def _make_entry(self, category_id: str) -> RoutingTableEntry:
        lm = LocalMindMap(
            node_id=category_id,
            parent_path="root",
            focus_description="测试聚焦",
            boundary_rules="测试边界",
            logic_signature="测试逻辑",
        )
        return RoutingTableEntry(
            category_id=category_id,
            stats={"freq": 10.0, "impact": 0.8, "trend": 0.1, "recover_cost": 0.3},
            local_map=lm,
            tags={Tag("状态_实验性"), Tag("场景_第三方依赖")},
            primary_skill_id="skill_429_retry",
        )

    def test_valid_entry(self) -> None:
        e = self._make_entry("network.rate_limit.429")
        assert e.category_id == "network.rate_limit.429"
        assert len(e.tags) == 2
        assert e.primary_skill_id == "skill_429_retry"

    def test_invalid_root_raises(self) -> None:
        with pytest.raises(ValueError, match="不在人类锁定的根分类"):
            self._make_entry("unknown.something")

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        e = self._make_entry("data_parsing.graphql")
        e.local_map.append_log("create", "新建", "sub_agent")
        data = e.to_dict()
        assert data["category_id"] == "data_parsing.graphql"
        assert "状态_实验性" in data["tags"]
        restored = RoutingTableEntry.from_dict(data)
        assert restored.category_id == "data_parsing.graphql"
        assert restored.primary_skill_id == "skill_429_retry"
        assert len(restored.local_map.maintenance_log) == 1

    def test_root_categories_contain_expected(self) -> None:
        assert "network" in ROOT_CATEGORIES
        assert "data_parsing" in ROOT_CATEGORIES
        assert "llm_inference" in ROOT_CATEGORIES


# ══════════════════════════════════════════════════════════════════
# SkillStep / SpecializedSkill
# ══════════════════════════════════════════════════════════════════

class TestSkill:
    def test_skill_creation_and_steps(self) -> None:
        overview = LocalMindMap(
            node_id="skill_ssl",
            parent_path="root.network.ssl",
            focus_description="SSL 证书修复工作流",
            boundary_rules="仅处理 SSL 证书",
            logic_signature="三步校验与修复",
        )
        skill = SpecializedSkill(
            skill_id="skill_ssl_cert",
            name="SSLCertFixSkill",
            overview_map=overview,
            tags={Tag("状态_稳定")},
        )

        step = SkillStep(
            step_id="step_1",
            action="校验证书有效期",
            local_map=LocalMindMap(
                node_id="step_1",
                parent_path="skill_ssl_cert",
                focus_description="检查证书是否过期",
                boundary_rules="仅检查有效期，不处理证书链",
                logic_signature="读取系统证书库并比对当前时间",
            ),
            precondition="目标主机可达",
            postcondition="证书状态已确定",
            retry_policy={"max_retries": 3, "backoff": "exponential"},
        )
        skill.add_step(step)
        assert len(skill.steps) == 1
        assert skill.steps[0].retry_policy["max_retries"] == 3

    def test_skill_to_dict_and_from_dict_roundtrip(self) -> None:
        overview = LocalMindMap(
            node_id="skill_gql",
            parent_path="root.data_parsing",
            focus_description="GraphQL 字段修复",
            boundary_rules="仅处理查询字段缺失",
            logic_signature="自动补全字段",
        )
        skill = SpecializedSkill(
            skill_id="skill_graphql",
            name="GraphQLFieldFixSkill",
            overview_map=overview,
        )
        skill.add_step(SkillStep(
            step_id="s1",
            action="解析查询",
            local_map=LocalMindMap(
                node_id="s1", parent_path="skill_graphql",
                focus_description="解析", boundary_rules="仅解析",
                logic_signature="AST 解析",
            ),
        ))
        data = skill.to_dict()
        assert data["name"] == "GraphQLFieldFixSkill"
        assert len(data["steps"]) == 1
        restored = SpecializedSkill.from_dict(data)
        assert restored.skill_id == "skill_graphql"
        assert len(restored.steps) == 1


# ══════════════════════════════════════════════════════════════════
# UnclassifiedFailurePackage
# ══════════════════════════════════════════════════════════════════

class TestUnclassifiedFailurePackage:
    def test_create_and_timestamp(self) -> None:
        pkg = UnclassifiedFailurePackage(
            error_stack="GraphQL: Field 'user' not found",
            context_snapshot={"session_id": "s123", "tool": "graphql_query"},
            attempted_strategies=["retry", "fallback"],
            location_guess="data_parsing",
            confidence=0.7,
        )
        assert pkg.error_stack == "GraphQL: Field 'user' not found"
        assert pkg.confidence == 0.7
        assert isinstance(pkg.timestamp, datetime)

    def test_confidence_range_validation(self) -> None:
        with pytest.raises(ValueError, match="confidence 必须在"):
            UnclassifiedFailurePackage(
                error_stack="err", context_snapshot={}, confidence=1.5
            )
        with pytest.raises(ValueError, match="confidence 必须在"):
            UnclassifiedFailurePackage(
                error_stack="err", context_snapshot={}, confidence=-0.1
            )

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        pkg = UnclassifiedFailurePackage(
            error_stack="Stack overflow",
            context_snapshot={"depth": 999},
            attempted_strategies=["limit_depth"],
            location_guess="resource_exhaustion",
            confidence=0.9,
        )
        data = pkg.to_dict()
        restored = UnclassifiedFailurePackage.from_dict(data)
        assert restored.error_stack == "Stack overflow"
        assert restored.confidence == 0.9
        assert restored.attempted_strategies == ["limit_depth"]
