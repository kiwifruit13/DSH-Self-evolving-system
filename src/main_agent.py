"""主代理（前台 · 只读）— 错误查询、Skill 执行、未知举证。

职责边界（来自 AGENTS_01.md §3.1）：
- ✅ 读取路由表（精确分类匹配）
- ✅ 读取路由表（模糊标签匹配）
- ✅ 调用 Skill 工作流（只执行，不修改）
- ✅ 将未知错误举证包写入反馈暂存队列

- ❌ 创建/修改/删除路由表节点
- ❌ 创建/修改/删除 Skill
- ❌ 修改标签系统
- ❌ 执行 INSERT/UPDATE/DELETE 到路由表或 Skill 库

使用示例：
    agent = MainAgent(storage, pending_queue)

    # 精确查询
    result = agent.lookup_exact(error_signature="HTTP_429")

    # 模糊查询
    result = agent.lookup_fuzzy(tags={Tag("场景_第三方依赖")})

    # 执行 Skill
    outcome = agent.execute_skill(skill, context={"target": "api.example.com"})

    # 未知错误举证
    agent.report_unknown("GraphQL: Field not found", context, attempts=["retry"])
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models import (
    RoutingTableEntry,
    SkillStep,
    SpecializedSkill,
    Tag,
    UnclassifiedFailurePackage,
)
from src.pending_queue import PendingQueue
from src.routing_table import RoutingTable
from src.skill_compiler import SkillCompiler
from src.storage import Storage


@dataclass
class SkillExecutionStepResult:
    """单步执行结果。"""
    step_id: str
    action: str
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class SkillExecutionResult:
    """Skill 工作流执行结果。"""
    skill_id: str
    skill_name: str
    steps: list[SkillExecutionStepResult] = field(default_factory=list)
    overall_success: bool = False
    total_steps: int = 0
    successful_steps: int = 0

    def add_step_result(self, result: SkillExecutionStepResult) -> None:
        self.steps.append(result)
        self.total_steps += 1
        if result.success:
            self.successful_steps += 1

    @property
    def all_succeeded(self) -> bool:
        return self.total_steps > 0 and self.successful_steps == self.total_steps


@dataclass
class LookupResult:
    """查询结果。"""
    category_id: str
    entry: RoutingTableEntry | None
    skill: SpecializedSkill | None
    match_type: str  # "exact" | "fuzzy" | "none"
    note: str = ""


class MainAgent:
    """主代理 — 前台只读组件。

    Args:
        storage: 底层持久化存储
        pending_queue: 反馈暂存队列
    """

    def __init__(self, storage: Storage, pending_queue: PendingQueue) -> None:
        self._storage = storage
        self._rt = RoutingTable(storage)
        self._compiler = SkillCompiler(storage)
        self._queue = pending_queue

    # ═══════════════════════════════════════════════════════════════
    # 精确分类查询
    # ═══════════════════════════════════════════════════════════════

    def lookup_exact(self, category_id: str) -> LookupResult:
        """按 category_id 精确查询路由表节点和关联 Skill。

        Args:
            category_id: 完整的路由表节点 ID，如 "network.rate_limit.429"

        Returns:
            LookupResult，包含路由表条目和关联 Skill（如有）。
        """
        entry = self._rt.get(category_id)
        if entry is None:
            return LookupResult(
                category_id=category_id,
                entry=None,
                skill=None,
                match_type="none",
                note=f"路由表中不存在节点 '{category_id}'",
            )

        skill = self._compiler.get_skill_for_entry(entry)
        return LookupResult(
            category_id=category_id,
            entry=entry,
            skill=skill,
            match_type="exact",
        )

    # ═══════════════════════════════════════════════════════════════
    # 标签模糊查询
    # ═══════════════════════════════════════════════════════════════

    def lookup_fuzzy(
        self,
        required_tags: set[Tag],
        root_category: str | None = None,
        limit: int = 5,
    ) -> list[LookupResult]:
        """通过标签组合进行模糊查询。

        Args:
            required_tags: 必须匹配的所有标签（AND 语义）
            root_category: 可选的根分类过滤
            limit: 最大返回数量

        Returns:
            按路由表排序得分降序排列的 LookupResult 列表。
        """
        # 查询路由表
        entries = self._rt.query(root_category=root_category, tags=required_tags)

        # 按得分排序
        ranked = self._rt.rank(root_category=root_category)
        ranked_map = {r.category_id: r for r in ranked}

        results: list[LookupResult] = []
        for entry in entries:
            skill = self._compiler.get_skill_for_entry(entry)
            # 验证边界：确保条目的 boundary_rules 非空（框架强制约束）
            # 若有 Skill，也验证 Skill 的 overview_map 边界完整性
            boundary_ok = bool(entry.local_map.boundary_rules.strip())
            if skill is not None:
                boundary_ok = boundary_ok and bool(
                    skill.overview_map.boundary_rules.strip()
                )

            results.append(
                LookupResult(
                    category_id=entry.category_id,
                    entry=entry,
                    skill=skill if boundary_ok else None,
                    match_type="fuzzy",
                    note="标签模糊匹配（非精确分类）" if boundary_ok else "边界不匹配",
                )
            )

        # 按得分降序排序（不在 ranked_map 中的条目排末尾）
        def _score_key(r: LookupResult) -> float:
            return ranked_map[r.category_id].final_score if r.category_id in ranked_map else 0.0

        results.sort(key=_score_key, reverse=True)

        return results[:limit]

    # ═══════════════════════════════════════════════════════════════
    # Skill 执行
    # ═══════════════════════════════════════════════════════════════

    def execute_skill(
        self,
        skill: SpecializedSkill,
        context: dict[str, Any] | None = None,
        executor: Any | None = None,
    ) -> SkillExecutionResult:
        """执行 Skill 工作流。

        Args:
            skill: 要执行的 Skill
            context: 执行上下文（传递给每个步骤）
            executor: 自定义执行器。签名为 Callable[[SkillStep, dict], SkillExecutionStepResult]。
                      若为 None，使用默认执行器（仅记录，不实际执行）。

        Returns:
            SkillExecutionResult，包含每步执行结果和整体结果。
        """
        context = context or {}
        result = SkillExecutionResult(
            skill_id=skill.skill_id,
            skill_name=skill.name,
        )

        for step in skill.steps:
            if executor is not None:
                step_result = executor(step, context)
            else:
                step_result = self._default_executor(step, context)

            result.add_step_result(step_result)

        result.overall_success = result.all_succeeded
        return result

    # ═══════════════════════════════════════════════════════════════
    # 未知错误举证
    # ═══════════════════════════════════════════════════════════════

    def report_unknown(
        self,
        error_stack: str,
        context: dict[str, Any] | None = None,
        attempted_strategies: list[str] | None = None,
        location_guess: str = "",
        confidence: float = 0.0,
    ) -> bool:
        """生成未知错误举证包并写入反馈暂存队列。

        这是主代理唯一允许写入的操作（写入暂存队列，非路由表/Skill库）。

        Args:
            error_stack: 完整错误栈
            context: 上下文快照
            attempted_strategies: 已尝试的失败方案
            location_guess: 猜测归属根分类
            confidence: 置信度 [0, 1]

        Returns:
            True 表示成功入队，False 表示队列已满。
        """
        context = context or {}
        attempted_strategies = attempted_strategies or []

        pkg = UnclassifiedFailurePackage(
            error_stack=error_stack,
            context_snapshot=context,
            attempted_strategies=attempted_strategies,
            location_guess=location_guess,
            confidence=confidence,
        )

        return self._queue.enqueue(pkg)

    # ═══════════════════════════════════════════════════════════════
    # 内部默认执行器
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _default_executor(step: SkillStep, context: dict[str, Any]) -> SkillExecutionStepResult:
        """默认执行器：仅记录步骤信息，不实际执行外部调用。

        生产环境中应替换为真实的工具调用执行器。
        """
        return SkillExecutionStepResult(
            step_id=step.step_id,
            action=step.action,
            success=True,
            output={"status": "simulated", "context_keys": list(context.keys())},
        )
