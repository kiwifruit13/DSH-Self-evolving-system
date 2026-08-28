"""子代理（后台 · 只写）— 日志蒸馏 / 暂存消费 / 路由维护 / Skill 孵化。

职责边界（来自 AGENTS_01.md §3.2）：
- ✅ 扫描 DSH Session 日志，执行蒸馏
- ✅ 创建/更新/分裂/合并路由表节点
- ✅ 编译/更新/废弃 Skill
- ✅ 更新标签系统（遗传与变异）
- ✅ 消费反馈暂存队列

- ❌ 直接响应用户请求（用户交互由主代理负责）
- ❌ 修改人类锁定的根分类骨架
- ❌ 跳过 maintenance_log 直接修改节点

使用示例：
    agent = SubAgent(storage, pending_queue, log_source=log_reader)

    # 蒸馏
    new_entries = agent.distill()

    # 消费暂存队列
    processed = agent.consume_pending()

    # 路由表维护
    agent.maintain()

    # Skill 孵化
    agent.compile_skills(top_k=5)
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.models import (
    ROOT_CATEGORIES,
    LocalMindMap,
    RoutingTableEntry,
    SpecializedSkill,
    Tag,
    UnclassifiedFailurePackage,
)
from src.overlap_checker import OverlapChecker
from src.pending_queue import PendingQueue
from src.quality_scorer import NodeQualityScorer
from src.routing_table import RoutingTable
from src.scoring import ScoreCalculator
from src.skill_compiler import SkillCompiler
from src.storage import Storage

# ══════════════════════════════════════════════════════════════════
# 日志条目格式（来自 DSH Session 日志）
# ══════════════════════════════════════════════════════════════════

@dataclass
class DistilledFix:
    """蒸馏出的已验证修复方案。"""
    error_signature: str
    fix_action: str
    impact_scope: str
    session_id: str
    timestamp: datetime
    confidence: float = 1.0


@dataclass
class DistillationResult:
    """蒸馏结果汇总。"""
    new_entries: list[RoutingTableEntry] = field(default_factory=list)
    updated_entries: list[RoutingTableEntry] = field(default_factory=list)
    total_distilled: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class FeedbackProcessingResult:
    """暂存队列消费结果。"""
    processed_count: int = 0
    new_entries: list[RoutingTableEntry] = field(default_factory=list)
    compiled_skills: list[SpecializedSkill] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# 子代理
# ══════════════════════════════════════════════════════════════════

class SubAgent:
    """子代理 — 后台写操作组件。

    Args:
        storage: 底层持久化存储
        pending_queue: 反馈暂存队列
        log_reader: 日志读取函数，签名为 Callable[[], Iterable[dict]]
                    返回字典列表，每个字典包含 session_id / event_type / content 等字段
    """

    def __init__(
        self,
        storage: Storage,
        pending_queue: PendingQueue,
        log_reader: Callable[[], Iterable[dict[str, Any]]] | None = None,
        overlap_threshold: float = 0.7,
    ) -> None:
        self._storage = storage
        self._queue = pending_queue
        self._rt = RoutingTable(storage)
        self._compiler = SkillCompiler(storage)
        self._rank_scorer = ScoreCalculator()
        self._quality_scorer = NodeQualityScorer()
        self._checker = OverlapChecker(storage, threshold=overlap_threshold)
        self._log_reader = log_reader

    # ═══════════════════════════════════════════════════════════════
    # 日志蒸馏
    # ═══════════════════════════════════════════════════════════════

    def distill(self) -> DistillationResult:
        """扫描 Session 日志，提取已验证的错误修复方案。

        蒸馏逻辑：
        1. 读取日志，寻找 error → tool_call → success 的事件链
        2. 提取三元组 (错误签名, 成功修复动作, 影响范围)
        3. 在路由表中创建或更新对应节点

        Returns:
            DistillationResult 汇总。
        """
        if self._log_reader is None:
            return DistillationResult()

        result = DistillationResult()
        logs = list(self._log_reader())

        # 按 session 分组
        sessions: dict[str, list[dict[str, Any]]] = {}
        for log in logs:
            sid = log.get("session_id", "unknown")
            sessions.setdefault(sid, []).append(log)

        for session_id, events in sessions.items():
            fix = self._extract_fix_from_events(session_id, events)
            if fix is None:
                continue

            result.total_distilled += 1
            try:
                entry = self._create_entry_from_fix(fix, session_id)
                existing = self._storage.get_routing_entry(entry.category_id)
                if existing is None:
                    # 重叠率校验（新建节点时执行）
                    check_result = self._checker.check(
                        candidate_category_id=entry.category_id,
                        candidate_signature=entry.local_map.logic_signature,
                        candidate_boundary=entry.local_map.boundary_rules,
                    )
                    if check_result.allows_creation:
                        self._storage.upsert_routing_entry(entry)
                        result.new_entries.append(entry)
                    else:
                        # 重叠被拒：不持久化节点（记录原因到 result.errors）
                        result.errors.append(
                            f"跳过节点 '{entry.category_id}'：与 "
                            f"'{check_result.max_overlap_with}' 重叠率 "
                            f"{check_result.max_overlap:.2%} 超过阈值 "
                            f"{self._checker.threshold:.0%}，未写入路由表"
                        )
                else:
                    # 更新统计值
                    existing.stats["freq"] = float(existing.stats.get("freq", 0)) + 1
                    existing.stats["last_seen"] = datetime.now(timezone.utc).isoformat()
                    existing.local_map.append_log(
                        "update",
                        f"蒸馏更新：来自 session {session_id}",
                        "sub_agent",
                    )
                    self._storage.upsert_routing_entry(existing)
                    result.updated_entries.append(existing)
            except Exception as e:
                result.errors.append(f"处理 session {session_id} 失败: {e}")

        return result

    def _extract_fix_from_events(
        self, session_id: str, events: list[dict[str, Any]]
    ) -> DistilledFix | None:
        """从事件链中提取已验证的修复方案。

        寻找模式：error → tool_call → success
        """
        error_event = None
        tool_event = None
        success_event = None

        for event in events:
            etype = event.get("event_type", "")
            content = event.get("content", {})

            if etype == "error" and error_event is None:
                error_event = content
            elif etype == "tool_call" and tool_event is None:
                if error_event is not None:
                    tool_event = content
            elif etype == "success" and tool_event is not None:
                success_event = content
                break

        if not (error_event and tool_event and success_event):
            return None

        error_sig = error_event.get("error_code", str(error_event.get("description", "unknown")))
        fix_action = tool_event.get("tool", tool_event.get("action", "unknown"))
        impact = tool_event.get("impact_scope", session_id)

        return DistilledFix(
            error_signature=error_sig,
            fix_action=fix_action,
            impact_scope=impact,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
        )

    def _create_entry_from_fix(self, fix: DistilledFix, session_id: str) -> RoutingTableEntry:
        """从蒸馏出的修复方案创建路由表条目。"""
        # 推断根分类
        root = self._infer_root_category(fix.error_signature)
        category_id = f"{root}.{fix.error_signature.lower().replace(' ', '_')}"

        lm = LocalMindMap(
            node_id=category_id,
            parent_path=f"root.{root}",
            focus_description=f"聚焦 {fix.error_signature} 修复",
            boundary_rules=f"仅处理 {fix.error_signature}，不处理其他错误",
            logic_signature=fix.fix_action,
        )
        lm.append_log("create", f"日志蒸馏生成（session: {session_id}）", "sub_agent")

        # 推断标签
        tags = {Tag("状态_实验性")}
        if "third_party" in fix.impact_scope or "external" in fix.impact_scope:
            tags.add(Tag("场景_第三方依赖"))
        else:
            tags.add(Tag("场景_内部微服务"))

        return RoutingTableEntry(
            category_id=category_id,
            stats={
                "freq": 1.0,
                "impact": 0.8,
                "trend": 0.0,
                "recover_cost": 1.0,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            local_map=lm,
            tags=tags,
        )

    def _infer_root_category(self, error_signature: str) -> str:
        """根据错误签名推断根分类。"""
        sig_lower = error_signature.lower()
        if any(k in sig_lower for k in ("http", "tcp", "ssl", "dns", "timeout", "connection", "rate")):
            return "network"
        if any(k in sig_lower for k in ("json", "parse", "xml", "graphql", "field")):
            return "data_parsing"
        if any(k in sig_lower for k in ("llm", "inference", "token", "model")):
            return "llm_inference"
        if any(k in sig_lower for k in ("memory", "cpu", "disk", "oom", "quota")):
            return "resource_exhaustion"
        if any(k in sig_lower for k in ("permission", "auth", "forbidden", "unauthorized")):
            return "permission"
        return "network"  # 默认归类

    # ═══════════════════════════════════════════════════════════════
    # 暂存队列消费
    # ═══════════════════════════════════════════════════════════════

    def consume_pending(self, batch_size: int = 10) -> FeedbackProcessingResult:
        """消费反馈暂存队列中的举证包。

        对每个举证包：
        1. 分析 location_guess 和上下文，确认归属分类
        2. 检查与现有节点的重叠率（< 70% 才允许创建）
        3. 创建新路由表节点
        4. 编译对应的专类 Skill

        Returns:
            FeedbackProcessingResult 汇总。
        """
        result = FeedbackProcessingResult()
        packages = self._queue.dequeue(limit=batch_size)

        for pkg in packages:
            try:
                entry = self._process_feedback(pkg)
                if entry is not None:
                    result.new_entries.append(entry)
                    skill = self._compiler.compile_from_entry(entry)
                    result.compiled_skills.append(skill)
            except Exception as exc:  # noqa: BLE001
                # dequeue 已标记 processed；异常时重新入队，避免举证包永久丢失
                try:
                    self._queue.enqueue(pkg)
                except Exception:
                    pass
                result.errors.append(f"处理举证包失败: {exc}")

        result.processed_count = len(packages)
        return result

    def _process_feedback(self, pkg: UnclassifiedFailurePackage) -> RoutingTableEntry | None:
        """处理单个举证包，创建新路由表节点。"""
        root = pkg.location_guess or "network"
        error_sig = pkg.error_stack.split("\n")[0][:60]  # 取第一行作为签名

        # 确保根分类合法
        if root not in ROOT_CATEGORIES:
            root = "network"

        category_id = f"{root}.{error_sig.lower().replace(' ', '_').replace(':', '_')}"

        # 重叠率检查
        existing = self._storage.get_routing_entry(category_id)
        if existing is not None:
            # 已有节点，更新统计
            existing.stats["freq"] = float(existing.stats.get("freq", 0)) + 1
            existing.stats["last_seen"] = datetime.now(timezone.utc).isoformat()
            existing.local_map.append_log(
                "update",
                f"反馈更新：置信度 {pkg.confidence}",
                "sub_agent",
            )
            self._storage.upsert_routing_entry(existing)
            return existing

        # 创建新节点
        lm = LocalMindMap(
            node_id=category_id,
            parent_path=f"root.{root}",
            focus_description=f"聚焦 {error_sig} 修复",
            boundary_rules=f"基于反馈举证自动生成（置信度 {pkg.confidence}）",
            logic_signature="待优化：基于主代理反馈举证生成",
        )
        lm.append_log("create", f"反馈举证生成（置信度 {pkg.confidence}）", "sub_agent")

        tags = {Tag("状态_实验性")}
        if pkg.confidence >= 0.8:
            tags.add(Tag("场景_第三方依赖"))
        else:
            tags.add(Tag("场景_内部微服务"))

        candidate_boundary = lm.boundary_rules
        candidate_signature = error_sig

        entry = RoutingTableEntry(
            category_id=category_id,
            stats={
                "freq": 1.0,
                "impact": pkg.confidence,
                "trend": 0.0,
                "recover_cost": len(pkg.attempted_strategies) + 1,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            local_map=lm,
            tags=tags,
        )

        # 重叠率校验（新建节点时执行）
        result = self._checker.check(
            candidate_category_id=category_id,
            candidate_signature=candidate_signature,
            candidate_boundary=candidate_boundary,
        )
        if not result.allows_creation:
            # 重叠被拒：不持久化节点，标记拒决原因由调用方决定是否合并且丢弃
            return None

        self._storage.upsert_routing_entry(entry)
        return entry

    # ═══════════════════════════════════════════════════════════════
    # 路由表维护
    # ═══════════════════════════════════════════════════════════════

    def maintain(
        self,
        split_threshold_top: int = 3,
        split_consecutive: int = 3,
        prune_threshold: float = 0.1,
        prune_bottom_pct: float = 0.1,
        quality_delta_min: float = 0.1,
    ) -> dict[str, Any]:
        """路由表维护：基于四维排序 + D1 知识增量质量评分触发分裂和剪枝。

        Args:
            split_threshold_top: 连续多少次进入 Top 触发分裂
            split_consecutive: 连续触发次数阈值
            prune_threshold: 剪枝得分阈值
            prune_bottom_pct: 剪枝底部百分比
            quality_delta_min: D1 知识增量最低门槛（低于此值的节点标记为低质量）

        Returns:
            维护操作统计（split/pruned/errors/quality_gated）。
        """
        stats: dict[str, Any] = {
            "split": 0,
            "pruned": [],
            "errors": [],
            "quality_gated": [],
        }

        # ── 质量评分门禁 ──
        # 对全部路由表节点执行 D1 知识增量评分，标记低于门槛的节点
        all_entries = self._storage.query_routing_entries()
        for entry in all_entries:
            score = self._quality_scorer.score(entry)
            if score.knowledge_delta < quality_delta_min:
                entry.local_map.append_log(
                    "quality_gated",
                    (
                        f"知识增量 {score.knowledge_delta:.0%} 低于门槛 "
                        f"{quality_delta_min:.0%}，质量等级: {score.quality_level}"
                    ),
                    "sub_agent",
                )
                self._storage.upsert_routing_entry(entry)
                stats["quality_gated"].append(
                    {
                        "category_id": score.category_id,
                        "knowledge_delta": score.knowledge_delta,
                        "quality_level": score.quality_level,
                        "signals": score.signals,
                    }
                )

        # 剪枝（低质量节点因得分低会自然排入底部）
        pruned = self._rt.prune_lowest(
            threshold=prune_threshold,
            bottom_pct=prune_bottom_pct,
            reason="定期维护：长期垫底 + 低质量自动标记",
            actor="sub_agent",
        )
        stats["pruned"] = pruned

        return stats

    # ═══════════════════════════════════════════════════════════════
    # Step 39：周期性重叠审计
    # ═══════════════════════════════════════════════════════════════

    def overlap_audit(self) -> list[dict[str, Any]]:
        """Step 39：对路由表所有同根分类节点执行两两重叠检测。

        审计结果不修改路由表，仅记录审计日志并返回高重叠节点对。
        供主代理或人工判断是否应合并。

        算法：
        - 按根分类分组
        - 同组内两两比较（O(n²/2)）
        - 重叠率 >= 阈值的节点对标记为高重叠，写入 maintenance_log

        Returns:
            高重叠节点对列表，每项包含 category_a, category_b, overlap, decision, merge_target
        """
        from src.overlap_checker import DECISION_MERGE, DECISION_UNCERTAIN

        all_entries = self._storage.query_routing_entries()
        # 按根分类分组
        by_root: dict[str, list[RoutingTableEntry]] = {}
        for entry in all_entries:
            root = entry.category_id.split(".")[0]
            by_root.setdefault(root, []).append(entry)

        high_overlap_pairs: list[dict[str, Any]] = []

        for root, entries in by_root.items():
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    a, b = entries[i], entries[j]
                    result = self._checker.check(
                        candidate_category_id=a.category_id,
                        candidate_signature=a.local_map.logic_signature,
                        candidate_boundary=a.local_map.boundary_rules,
                        root_category=root,
                        exclude_category_id=a.category_id,
                    )
                    if result.decision in (DECISION_MERGE, DECISION_UNCERTAIN):
                        high_overlap_pairs.append({
                            "category_a": a.category_id,
                            "category_b": b.category_id,
                            "overlap": result.max_overlap,
                            "decision": result.decision,
                            "merge_target": result.merge_target,
                        })
                        # 写入 maintenance_log
                        log_msg = (
                            f"重叠审计发现高重叠节点对："
                            f"'{a.category_id}' ↔ '{b.category_id}'，"
                            f"重叠率 {result.max_overlap:.4f}，"
                            f"决策 {result.decision}，"
                            f"建议合并至 '{result.merge_target}'"
                        )
                        a.local_map.append_log("overlap_audit", log_msg, "sub_agent")
                        b.local_map.append_log("overlap_audit", log_msg, "sub_agent")
                        self._storage.upsert_routing_entry(a)
                        self._storage.upsert_routing_entry(b)

        return high_overlap_pairs

    # ═══════════════════════════════════════════════════════════════
    # Skill 孵化
    # ═══════════════════════════════════════════════════════════════

    def compile_skills(
        self,
        top_k: int = 5,
        quality_delta_min: float = 0.1,
    ) -> list[SpecializedSkill]:
        """为得分最高的 Top K 路由表节点编译/更新 Skill。

        仅编译尚无关联 Skill 且通过 D1 质量评分门禁的节点。
        低于质量门槛的节点会被记录到维护日志并跳过编译。

        Args:
            top_k: 选择 Top K 高分节点
            quality_delta_min: D1 知识增量最低门槛

        Returns:
            新编译的 Skill 列表。
        """
        top_entries = self._rt.top_k(k=top_k)
        compiled: list[SpecializedSkill] = []
        skipped_quality: list[str] = []

        for breakdown in top_entries:
            entry = self._storage.get_routing_entry(breakdown.category_id)
            if entry is None:
                continue
            # 仅编译尚无 Skill 的节点
            if entry.primary_skill_id is not None:
                continue
            # D1 质量评分门禁
            score = self._quality_scorer.score(entry)
            if score.knowledge_delta < quality_delta_min:
                entry.local_map.append_log(
                    "skill_compile_skipped",
                    (
                        f"跳过 Skill 编译：知识增量 "
                        f"{score.knowledge_delta:.0%} < 门槛 {quality_delta_min:.0%}"
                    ),
                    "sub_agent",
                )
                self._storage.upsert_routing_entry(entry)
                skipped_quality.append(entry.category_id)
                continue
            skill = self._compiler.compile_from_entry(entry)
            compiled.append(skill)

        return compiled
