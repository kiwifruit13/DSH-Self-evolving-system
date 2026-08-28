"""离线规划器 — 子代理消费暂存队列的完整闭环。

流程：
1. 从暂存队列 dequeue 举证包
2. 对每个举证包执行"三阶段规划"：
   Phase 1: 分析 —— 解析错误签名、推断根分类、提取边界
   Phase 2: 校验 —— 重叠率检查（< 70% 才允许创建）
   Phase 3: 落地 —— 创建路由表节点 + 编译 Skill
3. 记录规划决策日志（谁/何时/为何/是否通过）

与 SubAgent.consume_pending 的区别：
- consume_pending 是快速路径，适合在线处理
- OfflinePlanner 是完整路径，含重叠率门禁和详细规划日志

使用示例：
    planner = OfflinePlanner(storage, pending_queue)
    report = planner.plan(batch_size=10)
    # report 包含：通过数、拒绝数（含原因）、创建节点、编译 Skill
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    SpecializedSkill,
    Tag,
    UnclassifiedFailurePackage,
)
from src.overlap_checker import OverlapChecker
from src.pending_queue import PendingQueue
from src.routing_table import RoutingTable
from src.skill_compiler import SkillCompiler
from src.storage import Storage


@dataclass
class PlanningPhase:
    """单阶段规划结果。"""
    phase: str  # "analyze" / "validate" / "deploy"
    status: str  # "pass" / "reject"
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanningDecision:
    """单次规划决策记录。"""
    package: UnclassifiedFailurePackage
    candidate_category_id: str = ""
    candidate_signature: str = ""
    candidate_boundary: str = ""
    phases: list[PlanningPhase] = field(default_factory=list)
    created_entry: RoutingTableEntry | None = None
    compiled_skill: SpecializedSkill | None = None
    overlap_result: dict[str, Any] | None = None
    rejected: bool = False
    rejection_reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_error": self.package.error_stack[:80],
            "candidate_category_id": self.candidate_category_id,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "overlap": self.overlap_result,
            "created_entry": self.created_entry.category_id if self.created_entry else None,
            "compiled_skill": self.compiled_skill.skill_id if self.compiled_skill else None,
        }


@dataclass
class PlanningReport:
    """整批规划结果汇总。"""
    total_processed: int = 0
    accepted: int = 0
    rejected: int = 0
    decisions: list[PlanningDecision] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        if self.total_processed == 0:
            return 0.0
        return self.accepted / self.total_processed

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_processed": self.total_processed,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "decisions": [d.to_dict() for d in self.decisions],
        }


class OfflinePlanner:
    """离线规划器 — 子代理消费暂存队列的完整闭环。

    Args:
        storage: 底层持久化存储
        pending_queue: 反馈暂存队列
        overlap_threshold: 重叠率阈值，默认 0.7
    """

    def __init__(
        self,
        storage: Storage,
        pending_queue: PendingQueue,
        overlap_threshold: float = 0.7,
    ) -> None:
        self._storage = storage
        self._queue = pending_queue
        self._rt = RoutingTable(storage)
        self._checker = OverlapChecker(storage, threshold=overlap_threshold)
        self._compiler = SkillCompiler(storage)

    # ═══════════════════════════════════════════════════════════════
    # 主规划入口
    # ═══════════════════════════════════════════════════════════════

    def plan(self, batch_size: int = 10) -> PlanningReport:
        """执行整批离线规划。

        Args:
            batch_size: 每批处理的举证包数量

        Returns:
            PlanningReport，包含所有决策记录和统计。
        """
        report = PlanningReport()
        packages = self._queue.dequeue(limit=batch_size)
        report.total_processed = len(packages)

        for pkg in packages:
            try:
                decision = self._plan_single(pkg)
            except Exception as exc:  # noqa: BLE001
                # 单包规划中途异常：重新入队避免举证包丢失，并记录错误
                try:
                    self._queue.enqueue(pkg)
                except Exception:
                    pass
                report.errors.append(
                    f"规划 '{pkg.error_stack[:40]}' 失败: {exc}"
                )
                continue
            report.decisions.append(decision)
            if decision.rejected:
                report.rejected += 1
            else:
                report.accepted += 1

        return report

    # ═══════════════════════════════════════════════════════════════
    # 单包规划
    # ═══════════════════════════════════════════════════════════════

    def _plan_single(self, pkg: UnclassifiedFailurePackage) -> PlanningDecision:
        """对单个举证包执行三阶段规划。"""
        decision = PlanningDecision(package=pkg)

        # Phase 1: 分析
        analyze = self._phase_analyze(pkg)
        decision.phases.append(analyze)
        if analyze.status == "reject":
            decision.rejected = True
            decision.rejection_reason = analyze.reason
            return decision

        decision.candidate_category_id = analyze.data["category_id"]
        decision.candidate_signature = analyze.data["signature"]
        decision.candidate_boundary = analyze.data["boundary"]

        # Phase 2: 校验（重叠率门禁）
        validate = self._phase_validate(
            decision.candidate_category_id,
            decision.candidate_signature,
            decision.candidate_boundary,
        )
        decision.phases.append(validate)
        decision.overlap_result = validate.data
        if validate.status == "reject":
            decision.rejected = True
            decision.rejection_reason = validate.reason
            return decision

        # Phase 3: 落地
        deploy = self._phase_deploy(
            decision.candidate_category_id,
            pkg,
        )
        decision.phases.append(deploy)
        if deploy.status == "pass":
            decision.created_entry = deploy.data.get("entry")
            decision.compiled_skill = deploy.data.get("skill")

        return decision

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: 分析
    # ═══════════════════════════════════════════════════════════════

    def _phase_analyze(self, pkg: UnclassifiedFailurePackage) -> PlanningPhase:
        """Phase 1: 解析举证包，推断分类和边界。"""
        error_sig = pkg.error_stack.split("\n")[0][:60].strip()
        # 清洗 category_id：去除特殊字符，保留字母数字下划线
        clean_sig = "".join(c if c.isalnum() or c == "_" else "_" for c in error_sig.lower())
        root = pkg.location_guess or "network"

        # 确保根分类合法
        from src.models import ROOT_CATEGORIES
        if root not in ROOT_CATEGORIES:
            root = "network"

        category_id = f"{root}.{clean_sig}"
        boundary = self._infer_boundary(pkg, root, error_sig)

        return PlanningPhase(
            phase="analyze",
            status="pass",
            data={
                "category_id": category_id,
                "signature": error_sig,
                "boundary": boundary,
                "root": root,
                "confidence": pkg.confidence,
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: 校验
    # ═══════════════════════════════════════════════════════════════

    def _phase_validate(
        self,
        category_id: str,
        signature: str,
        boundary: str,
    ) -> PlanningPhase:
        """Phase 2: 重叠率校验。"""
        result = self._checker.check(
            candidate_category_id=category_id,
            candidate_signature=signature,
            candidate_boundary=boundary,
        )

        if result.allows_creation:
            return PlanningPhase(
                phase="validate",
                status="pass",
                data=result.to_dict(),
            )

        return PlanningPhase(
            phase="validate",
            status="reject",
            reason=(
                f"重叠率 {result.max_overlap:.2%} 超过阈值 {self._checker.threshold:.0%}，"
                f"与 '{result.max_overlap_with}' 重复，拒绝创建"
            ),
            data=result.to_dict(),
        )

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: 落地
    # ═══════════════════════════════════════════════════════════════

    def _phase_deploy(
        self, category_id: str, pkg: UnclassifiedFailurePackage
    ) -> PlanningPhase:
        """Phase 3: 创建路由表节点 + 编译 Skill。"""
        error_sig = pkg.error_stack.split("\n")[0][:60].strip()
        root = category_id.split(".")[0]
        confidence = pkg.confidence

        # 构建 LocalMindMap
        lm = LocalMindMap(
            node_id=category_id,
            parent_path=f"root.{root}",
            focus_description=f"聚焦 {error_sig} 修复",
            boundary_rules=self._infer_boundary(pkg, root, error_sig),
            logic_signature=f"基于反馈举证生成（置信度 {confidence}），待优化",
        )
        lm.append_log("create", f"离线规划落地（置信度 {confidence}）", "sub_agent")

        # 根据置信度分配标签
        tags = self._assign_tags_by_confidence(confidence, root)

        entry = RoutingTableEntry(
            category_id=category_id,
            stats={
                "freq": 1.0,
                "impact": max(0.0, min(1.0, confidence)),
                "trend": 0.0,
                "recover_cost": len(pkg.attempted_strategies) + 1,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            local_map=lm,
            tags=tags,
        )

        # 写入存储（通过统一创建入口，执行互斥检查）
        entry = self._rt.create_node(
            entry,
            validate_overlap=False,  # Phase 2 已执行，此处只做互斥校验
        )

        # 编译 Skill
        skill = self._compiler.compile_from_entry(entry)

        return PlanningPhase(
            phase="deploy",
            status="pass",
            data={"entry": entry, "skill": skill},
        )

    # ═══════════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════════

    def _infer_boundary(self, pkg: UnclassifiedFailurePackage, root: str, error_sig: str) -> str:
        """根据举证包推断边界规则描述，保持与候选节点边界高度一致。"""
        strategies = pkg.attempted_strategies
        sig_short = error_sig.split(":")[0] if ":" in error_sig else error_sig[:40]
        if strategies:
            return f"仅处理 {sig_short}，已尝试策略: {', '.join(strategies)}"
        return f"仅处理 {sig_short}"

    def _assign_tags_by_confidence(
        self, confidence: float, root: str
    ) -> set[Tag]:
        """根据置信度自动分配标签。

        高置信度（>= 0.8）：状态_实验性 + 场景_第三方依赖
        中置信度（0.5-0.8）：状态_实验性 + 场景_内部微服务
        低置信度（< 0.5）：状态_实验性（不加场景标签，需人工审核）
        """
        tags: set[Tag] = {Tag("状态_实验性")}

        if confidence >= 0.8:
            tags.add(Tag("场景_第三方依赖"))
        elif confidence >= 0.5:
            tags.add(Tag("场景_内部微服务"))
        # < 0.5 不加场景标签，标记待审核

        # 根据根分类添加代价标签
        cost_tag_map = {
            "network": Tag("代价_低消耗"),
            "data_parsing": Tag("代价_中消耗"),
            "llm_inference": Tag("代价_高延迟"),
            "resource_exhaustion": Tag("代价_高延迟"),
            "permission": Tag("代价_低消耗"),
        }
        cost_tag = cost_tag_map.get(root)
        if cost_tag:
            tags.add(cost_tag)

        return tags
