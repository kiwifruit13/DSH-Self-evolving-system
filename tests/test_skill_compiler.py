"""Skill 编译器单元测试。"""
from pathlib import Path

import pytest

from src.models import LocalMindMap, RoutingTableEntry, SpecializedSkill, Tag
from src.skill_compiler import (
    SkillCompiler,
    StepTemplate,
)
from src.storage import Storage


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def compiler(storage: Storage) -> SkillCompiler:
    return SkillCompiler(storage)


def _make_entry(
    category_id: str = "network.rate_limit.429",
    tags: set[Tag] | None = None,
) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path="root.network",
        focus_description="聚焦 HTTP 429 限流",
        boundary_rules="仅处理 HTTP 429 状态码，不处理 TCP 连接超时",
        logic_signature="指数退避重试 3 次",
    )
    lm.append_log("create", "初始创建", "human")
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 50, "impact": 0.85, "trend": 0.2, "recover_cost": 2},
        local_map=lm,
        tags=tags or {Tag("状态_实验性"), Tag("场景_第三方依赖")},
    )


# ══════════════════════════════════════════════════════════════════
# 默认编译
# ══════════════════════════════════════════════════════════════════

class TestSkillCompilerDefault:
    def test_compile_from_entry(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        skill = compiler.compile_from_entry(entry)

        assert skill.skill_id.startswith("skill_network%2Erate_limit%2E429_")
        assert skill.name == "NetworkRateLimit429Skill"
        assert skill.pattern == "tool"  # network → tool 模式
        assert len(skill.steps) == 3  # Tool 模式三步
        assert skill.steps[0].step_id == "validate_params"
        assert skill.steps[1].step_id == "execute_with_retry"
        assert skill.steps[2].step_id == "verify_result"

    def test_skill_overview_inherits_from_entry(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        skill = compiler.compile_from_entry(entry)

        assert skill.overview_map.parent_path == entry.category_id
        assert "HTTP 429" in skill.overview_map.focus_description
        assert skill.overview_map.boundary_rules == entry.local_map.boundary_rules

    def test_skill_steps_have_local_maps(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        skill = compiler.compile_from_entry(entry)

        for step in skill.steps:
            assert step.local_map.node_id.startswith(skill.overview_map.node_id)
            assert step.local_map.boundary_rules != ""
            assert step.local_map.focus_description != ""

    def test_skill_tags_inherit_from_entry(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(tags={Tag("状态_实验性"), Tag("场景_第三方依赖")})
        skill = compiler.compile_from_entry(entry)

        assert Tag("状态_实验性") in skill.tags
        assert Tag("场景_第三方依赖") in skill.tags

    def test_skill_extra_tags(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        skill = compiler.compile_from_entry(
            entry, extra_tags={Tag("代价_低消耗")}
        )
        assert Tag("代价_低消耗") in skill.tags
        assert Tag("状态_实验性") in skill.tags

    def test_primary_skill_id_updated(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        compiler.compile_from_entry(entry)

        updated = compiler.get_skill_for_entry(entry)
        assert updated is not None
        assert entry.primary_skill_id is not None
        assert entry.primary_skill_id.startswith("skill_network%2Erate_limit%2E429_")

    def test_compile_by_id(self, compiler: SkillCompiler, storage: Storage) -> None:
        entry = _make_entry()
        storage.upsert_routing_entry(entry)

        skill = compiler.compile_by_id("network.rate_limit.429")
        assert skill is not None
        assert skill.name == "NetworkRateLimit429Skill"

    def test_compile_by_id_not_found(self, compiler: SkillCompiler) -> None:
        assert compiler.compile_by_id("nonexistent") is None


# ══════════════════════════════════════════════════════════════════
# 自定义模板
# ══════════════════════════════════════════════════════════════════

class TestSkillCompilerCustom:
    def test_custom_templates(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        custom = [
            StepTemplate(
                step_id="parse",
                action="解析请求",
                boundary_rules_suffix="仅解析 HTTP 请求头，不处理请求体",
            ),
            StepTemplate(
                step_id="retry",
                action="指数退避重试",
                boundary_rules_suffix="最多重试 3 次，每次间隔翻倍",
                retry_policy={"max_retries": 3, "backoff": "exponential"},
            ),
        ]
        skill = compiler.compile_from_entry(entry, templates=custom)

        assert len(skill.steps) == 2
        assert skill.steps[0].step_id == "parse"
        assert skill.steps[1].step_id == "retry"
        assert skill.steps[1].retry_policy["max_retries"] == 3

    def test_compile_custom_direct(self, compiler: SkillCompiler) -> None:
        overview = LocalMindMap(
            node_id="skill_custom",
            parent_path="root.custom",
            focus_description="自定义 Skill",
            boundary_rules="自定义边界",
            logic_signature="自定义逻辑",
        )
        from src.models import SkillStep
        skill = compiler.compile_custom(
            skill_id="skill_manual",
            name="ManualSkill",
            overview_map=overview,
            steps=[
                SkillStep(
                    step_id="s1",
                    action="手动步骤",
                    local_map=LocalMindMap(
                        node_id="s1", parent_path="skill_custom",
                        focus_description="步骤1", boundary_rules="仅步骤1",
                        logic_signature="逻辑1",
                    ),
                ),
            ],
            tags={Tag("状态_稳定")},
        )

        assert skill.skill_id == "skill_manual"
        assert skill.name == "ManualSkill"
        assert len(skill.steps) == 1
        assert Tag("状态_稳定") in skill.tags

    def test_get_skill(self, compiler: SkillCompiler) -> None:
        entry = _make_entry()
        skill = compiler.compile_from_entry(entry)
        retrieved = compiler.get_skill(skill.skill_id)
        assert retrieved is not None
        assert retrieved.name == skill.name

    def test_get_skill_nonexistent(self, compiler: SkillCompiler) -> None:
        assert compiler.get_skill("nonexistent") is None


# ══════════════════════════════════════════════════════════════════
# 名称派生
# ══════════════════════════════════════════════════════════════════

class TestNameDerivation:
    def test_derive_name(self) -> None:
        assert SkillCompiler._derive_name("network.rate_limit.429") == "NetworkRateLimit429Skill"
        assert SkillCompiler._derive_name("data_parsing.graphql") == "DataParsingGraphqlSkill"
        assert SkillCompiler._derive_name("llm_inference.timeout") == "LlmInferenceTimeoutSkill"


# ══════════════════════════════════════════════════════════════════
# 模式适配（Skill-Builder pattern selection）
# ══════════════════════════════════════════════════════════════════

class TestPatternSelection:
    def test_select_pattern_network(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="network.timeout.connect")
        assert compiler._select_pattern(entry) == "tool"

    def test_select_pattern_data_parsing(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="data_parsing.json.schema")
        assert compiler._select_pattern(entry) == "domain"

    def test_select_pattern_llm(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="llm_inference.timeout")
        assert compiler._select_pattern(entry) == "workflow"

    def test_select_pattern_permission(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="permission.api_key")
        assert compiler._select_pattern(entry) == "memory"

    def test_select_pattern_resource(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="resource_exhaustion.oom")
        assert compiler._select_pattern(entry) == "tool"

    def test_select_pattern_unknown_root(self, compiler: SkillCompiler) -> None:
        """permission → memory（所有已知根分类均应有映射）"""
        entry = _make_entry(category_id="permission.api_key")
        assert compiler._select_pattern(entry) == "memory"

    def test_compile_uses_pattern_tool(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="network.http_500")
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "tool"
        step_ids = [s.step_id for s in skill.steps]
        assert step_ids == ["validate_params", "execute_with_retry", "verify_result"]

    def test_compile_uses_pattern_domain(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="data_parsing.xml.parse")
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "domain"
        step_ids = [s.step_id for s in skill.steps]
        assert step_ids == ["detect_format", "parse_payload", "validate_output"]

    def test_compile_uses_pattern_workflow(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="llm_inference.context_overflow")
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "workflow"
        step_ids = [s.step_id for s in skill.steps]
        assert step_ids == ["preprocess_input", "run_inference", "postprocess_output"]

    def test_compile_uses_pattern_memory(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="permission.forbidden_access")
        skill = compiler.compile_from_entry(entry)
        assert skill.pattern == "memory"
        step_ids = [s.step_id for s in skill.steps]
        assert step_ids == ["check_policy", "evaluate_access", "record_decision"]

    def test_custom_templates_override_pattern(self, compiler: SkillCompiler) -> None:
        """显式传入 templates 时，模式选择被覆盖。"""
        entry = _make_entry(category_id="network.http_500")
        custom = [
            StepTemplate(
                step_id="custom_step",
                action="自定义步骤",
                boundary_rules_suffix="自定义边界",
            ),
        ]
        skill = compiler.compile_from_entry(entry, templates=custom)
        assert len(skill.steps) == 1
        assert skill.steps[0].step_id == "custom_step"

    def test_pattern_serialization(self, compiler: SkillCompiler) -> None:
        entry = _make_entry(category_id="network.http_500")
        skill = compiler.compile_from_entry(entry)
        d = skill.to_dict()
        assert d["pattern"] == "tool"

        restored = SpecializedSkill.from_dict(d)
        assert restored.pattern == "tool"
        assert restored.skill_id == skill.skill_id
