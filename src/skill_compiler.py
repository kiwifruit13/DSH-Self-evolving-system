"""Skill 编译器 — 从路由表节点自动生成专类 Skill 工作流。

设计要点：
- 从 RoutingTableEntry 的 local_map 和 logic_signature 推断 Skill 结构
- 默认生成三步 DAG：前置校验 → 核心动作 → 后置校验
- 每个步骤携带独立的 LocalMindMap（继承 + 细化边界）
- 支持自定义步骤模板（StepTemplate）
- 编译结果存入 storage 的 skills 表

使用示例：
    compiler = SkillCompiler(storage)
    skill = compiler.compile_from_entry(entry, name="HTTP429RetrySkill")
    # skill.steps 包含三步工作流
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    SkillStep,
    SpecializedSkill,
    Tag,
)
from src.storage import Storage

# ══════════════════════════════════════════════════════════════════
# Step 模板定义
# ══════════════════════════════════════════════════════════════════

@dataclass
class StepTemplate:
    """单步模板定义。

    每个模板描述了一个步骤的行为特征，编译器据此生成 SkillStep。
    """
    step_id: str
    action: str
    boundary_rules_suffix: str  # 追加到父节点 boundary_rules 的细化描述
    precondition: str | None = None
    postcondition: str | None = None
    retry_policy: dict[str, Any] | None = None

    def build_step(
        self,
        parent_map: LocalMindMap,
        step_counter: int,
    ) -> SkillStep:
        """根据父节点的 LocalMindMap 构建一个 SkillStep。"""
        step_map = LocalMindMap(
            node_id=f"{parent_map.node_id}.{self.step_id}",
            parent_path=parent_map.node_id,
            focus_description=f"[Step {step_counter}] {self.action}",
            boundary_rules=f"{parent_map.boundary_rules}；{self.boundary_rules_suffix}",
            logic_signature=f"{parent_map.logic_signature} → {self.action}",
        )
        step_map.append_log("compile", f"从路由节点 '{parent_map.node_id}' 编译", "sub_agent")

        return SkillStep(
            step_id=self.step_id,
            action=self.action,
            local_map=step_map,
            precondition=self.precondition,
            postcondition=self.postcondition,
            retry_policy=self.retry_policy,
        )


# 默认三步模板：前置校验 → 核心动作 → 后置校验
DEFAULT_TEMPLATES: list[StepTemplate] = [
    StepTemplate(
        step_id="precheck",
        action="前置校验",
        boundary_rules_suffix="仅检查执行前提条件，不修改任何状态",
        precondition="目标系统可达且处于已知状态",
    ),
    StepTemplate(
        step_id="execute",
        action="核心动作",
        boundary_rules_suffix="仅执行核心修复逻辑，不涉及旁路逻辑",
        postcondition="核心动作执行完毕，状态已变更",
        retry_policy={"max_retries": 3, "backoff": "exponential"},
    ),
    StepTemplate(
        step_id="postcheck",
        action="后置校验",
        boundary_rules_suffix="仅验证执行结果，不执行修复逻辑",
        precondition="核心动作已执行",
        postcondition="校验通过则标记修复成功，否则标记失败",
    ),
]


# ══════════════════════════════════════════════════════════════════
# Skill 模式枚举（Skill-Builder 4 模式 + generic）
# ══════════════════════════════════════════════════════════════════

# 模式 → 根分类映射（Skill-Builder patterns.md）
_PATTERN_BY_ROOT: dict[str, str] = {
    "network": "tool",             # 包装重试/限流工具
    "resource_exhaustion": "tool",  # 资源管理工具
    "data_parsing": "domain",      # 格式解析专家知识
    "llm_inference": "workflow",   # 多阶段推理管道
    "permission": "memory",        # 权限策略记忆
}


# 各模式对应的步骤模板

# Tool 模式：参数校验 → 执行重试 → 验证结果
_TOOL_TEMPLATES: list[StepTemplate] = [
    StepTemplate(
        step_id="validate_params",
        action="参数校验",
        boundary_rules_suffix="仅校验输入参数合法性，不发起任何外部调用",
        precondition="目标系统可达",
    ),
    StepTemplate(
        step_id="execute_with_retry",
        action="带重试执行",
        boundary_rules_suffix="使用指数退避重试，最多 3 次，超时 30s",
        postcondition="请求已发出，收到响应或达到重试上限",
        retry_policy={"max_retries": 3, "backoff": "exponential", "timeout_s": 30},
    ),
    StepTemplate(
        step_id="verify_result",
        action="结果验证",
        boundary_rules_suffix="仅验证返回结果符合预期，不执行额外操作",
        precondition="已收到响应或达到重试上限",
    ),
]

# Domain 模式：检测格式 → 解析负载 → 校验输出
_DOMAIN_TEMPLATES: list[StepTemplate] = [
    StepTemplate(
        step_id="detect_format",
        action="格式检测",
        boundary_rules_suffix="仅检测输入格式类型（JSON/XML/GraphQL等），不做转换",
        precondition="输入数据存在且非空",
    ),
    StepTemplate(
        step_id="parse_payload",
        action="解析负载",
        boundary_rules_suffix="按检测到的格式解析，使用标准库解析器，处理非法结构",
        postcondition="数据已解析为结构化对象或抛出格式错误",
    ),
    StepTemplate(
        step_id="validate_output",
        action="输出校验",
        boundary_rules_suffix="校验解析结果是否符合预期 schema，记录字段缺失",
        precondition="数据已解析成功",
    ),
]

# Workflow 模式：预处理 → 核心推理 → 后处理
_WORKFLOW_TEMPLATES: list[StepTemplate] = [
    StepTemplate(
        step_id="preprocess_input",
        action="预处理输入",
        boundary_rules_suffix="清洗输入 token、截断超限内容、添加系统提示词",
        precondition="原始输入已就绪",
    ),
    StepTemplate(
        step_id="run_inference",
        action="运行推理",
        boundary_rules_suffix="调用 LLM API 执行推理，监控 token 消耗，处理流式中断",
        postcondition="推理完成或达到上下文上限",
        retry_policy={"max_retries": 1, "backoff": "exponential", "timeout_s": 120},
    ),
    StepTemplate(
        step_id="postprocess_output",
        action="后处理输出",
        boundary_rules_suffix="解析模型输出、提取结构化字段、格式化最终响应",
        precondition="推理结果已获取",
    ),
]

# Memory 模式：检查策略 → 评估权限 → 记录决策
_MEMORY_TEMPLATES: list[StepTemplate] = [
    StepTemplate(
        step_id="check_policy",
        action="策略查询",
        boundary_rules_suffix="从记忆库检索当前请求的权限策略，区分显式/隐式规则",
        precondition="用户/资源标识已解析",
    ),
    StepTemplate(
        step_id="evaluate_access",
        action="权限评估",
        boundary_rules_suffix="基于策略规则评估访问请求，输出 allow/deny/unknown",
        postcondition="得到明确的访问决策结果",
    ),
    StepTemplate(
        step_id="record_decision",
        action="决策记录",
        boundary_rules_suffix="将本次评估结果写入记忆库，标记置信度和上下文快照",
        precondition="已得到访问决策",
    ),
]

# 模式模板注册表
_PATTERN_TEMPLATES: dict[str, list[StepTemplate]] = {
    "tool": _TOOL_TEMPLATES,
    "domain": _DOMAIN_TEMPLATES,
    "workflow": _WORKFLOW_TEMPLATES,
    "memory": _MEMORY_TEMPLATES,
    "generic": DEFAULT_TEMPLATES,  # 旧行为作为回退
}


class SkillCompiler:
    """Skill 编译器：从路由表节点生成专类 Skill。

    支持按根分类自动选择 Skill-Builder 模板模式：
        tool / domain / workflow / memory / generic

    如果调用方显式传入 templates，则优先使用传入模板。

    Args:
        storage: 底层持久化存储
        default_templates: 默认步骤模板，可覆盖
    """

    def __init__(
        self,
        storage: Storage,
        default_templates: list[StepTemplate] | None = None,
    ) -> None:
        self._storage = storage
        self._templates = default_templates or DEFAULT_TEMPLATES

    def _select_pattern(self, entry: RoutingTableEntry) -> str:
        """根据路由表节点的根分类选择 Skill-Builder 模板模式。

        映射规则（来自 skill-builder/patterns.md）：
            network → tool          包装重试/限流工具
            data_parsing → domain    格式解析专家知识
            llm_inference → workflow 多阶段推理管道
            permission → memory      权限策略记忆
            resource_exhaustion → tool 资源管理工具

        Args:
            entry: 路由表条目

        Returns:
            选中的模式名称
        """
        root = entry.category_id.split(".")[0]
        return _PATTERN_BY_ROOT.get(root, "generic")

    # ═══════════════════════════════════════════════════════════════
    # 从路由表节点编译
    # ═══════════════════════════════════════════════════════════════

    def compile_from_entry(
        self,
        entry: RoutingTableEntry,
        name: str | None = None,
        templates: list[StepTemplate] | None = None,
        extra_tags: set[Tag] | None = None,
    ) -> SpecializedSkill:
        """从路由表节点编译生成 Skill。

        Args:
            entry: 路由表条目（数据源）
            name: Skill 名称，默认从 category_id 派生
            templates: 自定义步骤模板，默认使用 DEFAULT_TEMPLATES
            extra_tags: 附加标签（与 entry.tags 合并）

        Returns:
            编译完成的 SpecializedSkill，已写入 storage。
        """
        # 按根分类自动选择模板模式
        selected_pattern = self._select_pattern(entry)
        if templates is None:
            templates = _PATTERN_TEMPLATES.get(selected_pattern) or self._templates
        else:
            selected_pattern = "generic"

        skill_name = name or self._derive_name(entry.category_id)
        skill_id = f"skill_{entry.category_id.replace('.', '_')}"

        # overview_map 继承自路由表节点的 local_map
        overview = LocalMindMap(
            node_id=skill_id,
            parent_path=entry.category_id,
            focus_description=f"Skill: {skill_name} — {entry.local_map.focus_description}",
            boundary_rules=entry.local_map.boundary_rules,
            logic_signature=f"工作流: {entry.local_map.logic_signature}",
        )
        overview.append_log(
            "compile",
            f"从路由节点 '{entry.category_id}' 编译生成",
            "sub_agent",
        )

        # 构建步骤
        steps: list[SkillStep] = []
        for i, template in enumerate(templates, start=1):
            step = template.build_step(overview, i)
            steps.append(step)

        # 合并标签
        merged_tags = set(entry.tags)
        if extra_tags:
            merged_tags |= extra_tags

        skill = SpecializedSkill(
            skill_id=skill_id,
            name=skill_name,
            pattern=selected_pattern,
            overview_map=overview,
            steps=steps,
            tools=self._infer_tools(entry, selected_pattern),
            context_keys=self._infer_context_keys(entry),
            tags=merged_tags,
        )

        # 写入存储
        self._storage.upsert_skill(skill)

        # 更新路由表条目的 primary_skill_id
        if entry.primary_skill_id != skill_id:
            entry.primary_skill_id = skill_id
            entry.local_map.append_log(
                "update",
                f"关联 Skill '{skill_id}' (name={skill_name})",
                "sub_agent",
            )
            self._storage.upsert_routing_entry(entry)

        return skill

    # ═══════════════════════════════════════════════════════════════
    # 运行时工具集推断（Agent-Builder Skill 运行时化）
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _infer_tools(entry: RoutingTableEntry, pattern: str) -> list[str]:
        """从路由表节点推断 Skill 运行时所需的工具集。

        工具推断基于模式 + 边界规则关键词匹配：
            - tool 模式：http_client, retry, circuit_breaker, rate_limiter
            - domain 模式：json_parser, xml_parser, graphql_client
            - workflow 模式：llm_api, token_counter, prompt_templater
            - memory 模式：memory_store, policy_engine, audit_logger
            - generic：仅 retry
        """
        boundary = entry.local_map.boundary_rules or ""
        tools: list[str] = []

        if pattern == "tool":
            tools.append("http_client")
            if "指数退避" in boundary or "retry" in boundary:
                tools.append("retry")
            if "熔断" in boundary or "circuit" in boundary:
                tools.append("circuit_breaker")
            if "限流" in boundary or "throttl" in boundary:
                tools.append("rate_limiter")
        elif pattern == "domain":
            tools.append("json_parser")
            if "xml" in boundary or "XML" in boundary:
                tools.append("xml_parser")
            if "graphql" in boundary or "GraphQL" in boundary:
                tools.append("graphql_client")
        elif pattern == "workflow":
            tools.append("llm_api")
            if "token" in boundary:
                tools.append("token_counter")
            if "prompt" in boundary or "提示词" in boundary:
                tools.append("prompt_templater")
        elif pattern == "memory":
            tools.append("memory_store")
            tools.append("policy_engine")
            tools.append("audit_logger")
        else:  # generic
            tools.append("retry")

        return tools

    @staticmethod
    def _infer_context_keys(entry: RoutingTableEntry) -> list[str]:
        """推断 Skill 执行时需要从主代理上下文读取的键名。"""
        focus = entry.local_map.focus_description or ""
        boundary = entry.local_map.boundary_rules or ""
        context = focus + " " + boundary
        keys: list[str] = []

        if "HTTP" in context:
            keys.append("http_config")
        if "超时" in context:
            keys.append("timeout")
        if "token" in context.lower() or "LLM" in context:
            keys.append("model_config")
        if "权限" in context or "访问" in context:
            keys.append("user_context")
        if "用户" in context:
            keys.append("user_context")
        if "数据库" in context or "SQL" in context:
            keys.append("db_config")

        return keys

    # ═══════════════════════════════════════════════════════════════
    # 从 category_id 编译（便捷方法）
    # ═══════════════════════════════════════════════════════════════

    def compile_by_id(
        self,
        category_id: str,
        name: str | None = None,
        templates: list[StepTemplate] | None = None,
        extra_tags: set[Tag] | None = None,
    ) -> SpecializedSkill | None:
        """通过 category_id 查找并编译 Skill。

        Returns:
            编译完成的 Skill，若条目不存在则返回 None。
        """
        entry = self._storage.get_routing_entry(category_id)
        if entry is None:
            return None
        return self.compile_from_entry(
            entry, name=name, templates=templates, extra_tags=extra_tags
        )

    # ═══════════════════════════════════════════════════════════════
    # 自定义编译
    # ═══════════════════════════════════════════════════════════════

    def compile_custom(
        self,
        skill_id: str,
        name: str,
        overview_map: LocalMindMap,
        steps: list[SkillStep],
        tags: set[Tag] | None = None,
    ) -> SpecializedSkill:
        """完全自定义地编译一个 Skill。

        用于子代理根据复杂业务逻辑手工构建 Skill 的场景。
        """
        skill = SpecializedSkill(
            skill_id=skill_id,
            name=name,
            overview_map=overview_map,
            steps=steps,
            tags=tags or set(),
        )
        self._storage.upsert_skill(skill)
        return skill

    # ═══════════════════════════════════════════════════════════════
    # 获取已编译的 Skill
    # ═══════════════════════════════════════════════════════════════

    def get_skill(self, skill_id: str) -> SpecializedSkill | None:
        """获取已编译的 Skill。"""
        return self._storage.get_skill(skill_id)

    def get_skill_for_entry(self, entry: RoutingTableEntry) -> SpecializedSkill | None:
        """获取路由表条目关联的 Skill。"""
        if entry.primary_skill_id is None:
            return None
        return self._storage.get_skill(entry.primary_skill_id)

    # ═══════════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _derive_name(category_id: str) -> str:
        """从 category_id 派生 Skill 名称。

        如 "network.rate_limit.429" → "NetworkRateLimit429Skill"
        下划线和点号均作为单词分隔符处理。
        """
        # 用点号和下划线分割成 token
        import re
        tokens = re.split(r"[._]", category_id)
        # 过滤空 token，每个 token 首字母大写
        parts = [t.capitalize() for t in tokens if t]
        return "".join(parts) + "Skill"
