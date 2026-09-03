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

import logging
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
    sanitize_signature,
)
from src.pending_queue import PendingQueue
from src.quality_scorer import NodeQualityScorer
from src.routing_table import MAX_SPLIT_DEPTH, RoutingTable, SplitRejectedError
from src.scoring import ScoreCalculator
from src.skill_compiler import SkillCompiler
from src.storage import Storage

logger = logging.getLogger(__name__)

# BUG-32 同构修复：同一举证包最大消费尝试次数，超过后转入死信（不再重入队）
MAX_CONSUME_ATTEMPTS = 3

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
    subtype: str = ""
    """错误子类型，如 "read" / "connect"。

    Gherkin F1 场景2 要求分裂判据基于「子分类占比」，因此蒸馏必须保留
    比 category_id 更细的一层维度：同一 category_id 下可能混合多种子类型，
    只有某子类型占主导时才值得下钻。
    """


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
    ) -> None:
        self._storage = storage
        self._queue = pending_queue
        # 所有重叠判断与写路径都通过 self._rt 走——保证 OverlapChecker 实例唯一，
        # 统一重叠判断入口（缓存层已移除，check() 每次真实计算）。
        self._rt = RoutingTable(storage)
        self._compiler = SkillCompiler(storage)
        self._rank_scorer = ScoreCalculator()
        self._quality_scorer = NodeQualityScorer()
        self._log_reader = log_reader
        # BUG-32 同构修复：消费失败重试计数，键为包内容指纹（有界防泄漏）
        self._consume_attempts: dict[tuple[str, str, str], int] = {}

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
                    # 重叠率校验（新建节点时执行）—— 通过 self._rt 共用同一 checker 实例
                    check_result = self._rt.check_overlap(
                        candidate_category_id=entry.category_id,
                        candidate_signature=entry.local_map.logic_signature,
                        candidate_boundary=entry.local_map.boundary_rules,
                    )
                    if check_result.allows_creation:
                        # 走 RoutingTable.update，与分裂/合并路径保持同一写入口
                        self._rt.update(entry)
                        result.new_entries.append(entry)
                    else:
                        # 重叠被拒：不持久化节点（记录原因到 result.errors）
                        result.errors.append(
                            f"跳过节点 '{entry.category_id}'：与 "
                            f"'{check_result.max_overlap_with}' 重叠率 "
                            f"{check_result.max_overlap:.2%} 超过阈值 "
                            f"{check_result.threshold:.0%}，未写入路由表"
                        )
                else:
                    # 更新统计值
                    existing.stats["freq"] = float(existing.stats.get("freq", 0)) + 1
                    # sample_count 为分裂判据的样本充足性依据，必须随每次观测递增
                    existing.stats["sample_count"] = float(
                        existing.stats.get("sample_count", 0)
                    ) + 1
                    existing.stats["last_seen"] = datetime.now(timezone.utc).isoformat()
                    # 累积子类型观测，供分裂判据识别主导子类型
                    existing.record_subtype(fix.subtype)
                    existing.local_map.append_log(
                        "update",
                        f"蒸馏更新：来自 session {session_id}",
                        "sub_agent",
                    )
                    self._rt.update(existing)
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

        # 子类型维度：优先取日志显式标注，缺失时回退到修复动作。
        # 回退是必需的——若允许 subtype 为空，直方图将永远为空，分裂判据
        # 会重演 sample_count 恒为 0 的失效模式（判据写了但永不触发）。
        subtype = ""
        for key in ("subtype", "error_subtype", "sub_category", "detail"):
            value = error_event.get(key)
            if isinstance(value, str) and value.strip():
                subtype = value
                break
        if not subtype:
            subtype = fix_action

        return DistilledFix(
            error_signature=error_sig,
            fix_action=fix_action,
            impact_scope=impact,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            subtype=subtype,
        )

    def _create_entry_from_fix(self, fix: DistilledFix, session_id: str) -> RoutingTableEntry:
        """从蒸馏出的修复方案创建路由表条目。"""
        # 推断根分类
        root = self._infer_root_category(fix.error_signature)
        # BUG-20/40 修复：统一清洗规约（sanitize_signature），折叠全部
        # 非法字符，避免 ID 注入产生多段 category_id
        clean_sig = sanitize_signature(fix.error_signature)
        category_id = f"{root}.{clean_sig}"

        lm = LocalMindMap(
            node_id=category_id,
            parent_path="",
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

        entry = RoutingTableEntry(
            category_id=category_id,
            stats={
                "freq": 1.0,
                "impact": 0.8,
                "trend": 0.0,
                "recover_cost": 1.0,
                # sample_count：分裂判据的样本充足性依据。
                # 缺失此字段会导致 maintain() 的 sample_count 门槛恒不成立。
                "sample_count": 1.0,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            local_map=lm,
            tags=tags,
        )
        entry.record_subtype(fix.subtype)
        return entry

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
                # BUG-32 同构修复：同一举证包连续 MAX_CONSUME_ATTEMPTS 次失败
                # 后转入死信（不再重新入队），消除确定性失败毒丸循环；
                # 重入队本身失败时记录错误，不再静默丢弃。
                key = (
                    pkg.error_stack[:80],
                    pkg.location_guess,
                    pkg.timestamp.isoformat(),
                )
                self._consume_attempts[key] = self._consume_attempts.get(key, 0) + 1
                if self._consume_attempts[key] >= MAX_CONSUME_ATTEMPTS:
                    self._consume_attempts.pop(key, None)
                    result.errors.append(
                        f"举证包 '{pkg.error_stack[:40]}' 连续 "
                        f"{MAX_CONSUME_ATTEMPTS} 次处理失败，转入死信（不再重试）: {exc}"
                    )
                    logger.warning(
                        "举证包连续 %d 次消费失败，已转入死信: %s",
                        MAX_CONSUME_ATTEMPTS, exc,
                    )
                    continue
                # dequeue 已标记 processed；可重试异常时重新入队，避免举证包永久丢失
                try:
                    self._queue.enqueue(pkg)
                except Exception as enqueue_exc:  # noqa: BLE001
                    result.errors.append(
                        f"举证包 '{pkg.error_stack[:40]}' 重入队失败（已丢弃）: {enqueue_exc}"
                    )
                result.errors.append(f"处理举证包失败: {exc}")

        result.processed_count = len(packages)
        return result

    def _process_feedback(self, pkg: UnclassifiedFailurePackage) -> RoutingTableEntry | None:
        """处理单个举证包，创建新路由表节点。"""
        root = pkg.location_guess or "network"
        error_sig = pkg.error_stack.split("\n")[0][:60].strip()  # 取第一行作为签名

        # 确保根分类合法
        if root not in ROOT_CATEGORIES:
            root = "network"

        # BUG-40/41 修复：统一清洗规约 + 空签名兜底（error_stack 为空时
        # 此前会产出 "network." 垃圾节点，现归入 unclassified 聚合节点）
        category_id = f"{root}.{sanitize_signature(error_sig)}"

        # 重叠率检查
        existing = self._storage.get_routing_entry(category_id)
        if existing is not None:
            # 已有节点，更新统计
            existing.stats["freq"] = float(existing.stats.get("freq", 0)) + 1
            existing.stats["sample_count"] = float(
                existing.stats.get("sample_count", 0)
            ) + 1
            existing.stats["last_seen"] = datetime.now(timezone.utc).isoformat()
            # 不记录子类型：UnclassifiedFailurePackage 未携带细分子类型字段，
            # 强行以签名或策略填充会污染直方图，使主导占比失真。
            # 子类型维度仅由蒸馏路径（日志中确实存在该字段）驱动。
            existing.local_map.append_log(
                "update",
                f"反馈更新：置信度 {pkg.confidence}",
                "sub_agent",
            )
            self._rt.update(existing)
            return existing

        # 创建新节点
        lm = LocalMindMap(
            node_id=category_id,
            parent_path="",
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
                "sample_count": 1.0,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            local_map=lm,
            tags=tags,
        )

        # 重叠率校验（新建节点时执行）—— 通过 self._rt 共用同一 checker 实例
        result = self._rt.check_overlap(
            candidate_category_id=category_id,
            candidate_signature=candidate_signature,
            candidate_boundary=candidate_boundary,
        )
        if not result.allows_creation:
            # 重叠被拒：不持久化节点，标记拒决原因由调用方决定是否合并且丢弃
            return None

        self._rt.update(entry)
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
        split_min_samples: int = 5,
        split_dominant_share: float = 0.7,
    ) -> dict[str, Any]:
        """路由表维护：基于四维排序 + D1 知识增量质量评分触发分裂和剪枝。

        分裂判据严格对齐 Gherkin.md F1 场景2：
        1. 节点综合优先级**连续** split_consecutive 次进入 Top split_threshold_top
           （连续性由 stats["top_streak"] 跨轮维护，落选即归零）
        2. **且**某一子类型在观测样本中占比超过 split_dominant_share（契约值 0.70）
        3. **且**样本数不少于 split_min_samples（占比需有统计意义）

        三个条件同时满足才下钻出以该主导子类型命名的子节点。

        剪枝：无父节点的孤立节点（自动建节点产物）直接跳过——强行删除会丢
        数据，强行合并无父可归。路由表膨胀问题由更上游策略解决。

        Args:
            split_threshold_top: 分裂候选名次阈值（Top N）
            split_consecutive: 连续进入 Top N 的次数阈值
            prune_threshold: 剪枝得分阈值
            prune_bottom_pct: 剪枝底部百分比
            quality_delta_min: D1 知识增量最低门槛（低于此值的节点标记为低质量）
            split_min_samples: 分裂所需的最小观测样本数
            split_dominant_share: 主导子类型占比阈值（Gherkin 契约值 0.70）

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
                # BUG-08 修复：日志去重——同一节点本轮只记一次 quality_gated
                already_gated = any(
                    log.action == "quality_gated"
                    for log in entry.local_map.maintenance_log
                )
                if not already_gated:
                    entry.local_map.append_log(
                        "quality_gated",
                        (
                            f"知识增量 {score.knowledge_delta:.0%} 低于门槛 "
                            f"{quality_delta_min:.0%}，质量等级: {score.quality_level}"
                        ),
                        "sub_agent",
                    )
                    self._rt.update(entry)
                stats["quality_gated"].append(
                    {
                        "category_id": score.category_id,
                        "knowledge_delta": score.knowledge_delta,
                        "quality_level": score.quality_level,
                        "signals": score.signals,
                    }
                )

        # ── Gherkin F1 场景2：分裂触发 ──
        if split_threshold_top > 0 and split_consecutive > 0:
            top_ids = {b.category_id for b in self._rt.top_k(k=split_threshold_top)}

            # 第一步：全表更新连续计数。
            # 必须遍历全部节点，不能只遍历 Top N —— 否则落选节点的 streak
            # 永不归零，「连续」会退化成「累计」，判据失效。
            # 仅在计数实际变化时落库，避免每轮对全表无意义写入。
            for entry in all_entries:
                old_streak = int(float(entry.stats.get("top_streak", 0) or 0))
                new_streak = old_streak + 1 if entry.category_id in top_ids else 0
                if new_streak != old_streak:
                    entry.stats["top_streak"] = float(new_streak)
                    self._rt.update(entry)

            # 第二步：对满足全部契约条件的节点执行分裂
            for entry in all_entries:
                if entry.category_id not in top_ids:
                    continue

                streak = int(float(entry.stats.get("top_streak", 0) or 0))
                if streak < split_consecutive:
                    continue

                sample_count = int(float(entry.stats.get("sample_count", 0) or 0))
                if sample_count < split_min_samples:
                    continue

                # 深度护栏改用常量，消除与 routing_table 的重复阈值定义
                if len(entry.category_id.split(".")) >= MAX_SPLIT_DEPTH:
                    continue

                dominant = entry.dominant_subtype()
                if dominant is None or dominant[1] < split_dominant_share:
                    continue
                child_name, share = dominant

                # 同名子节点已存在则跳过，不依赖 split 抛异常来控制流程
                if self._storage.get_routing_entry(
                    f"{entry.category_id}.{child_name}"
                ) is not None:
                    continue

                try:
                    self._rt.split(
                        entry.category_id,
                        child_name,
                        reason=(
                            f"维护自动分裂：连续 {streak} 次进入 Top "
                            f"{split_threshold_top}，子类型 '{child_name}' 观测占比 "
                            f"{share:.0%} 超过阈值 {split_dominant_share:.0%}"
                            f"（样本数 {sample_count}）"
                        ),
                        actor="sub_agent",
                    )
                except (ValueError, SplitRejectedError) as e:
                    stats["errors"].append(f"分裂 '{entry.category_id}' 失败: {e}")
                    continue

                # 第三步：分裂后重置状态。
                # 不重置会就同一子类型无限分裂：主导占比不会因分裂而自动下降，
                # 且 split 遇到已存在子节点会直接抛错。
                #
                # 注意：RoutingTable.split() 内部会重新读取父节点对象并 upsert
                # （写入 maintenance_log 与 stats 重分配），此处的 entry 是分裂
                # 前的过期副本，直接 upsert 会覆盖掉那些写入。必须重新读取。
                fresh = self._storage.get_routing_entry(entry.category_id)
                if fresh is not None:
                    fresh.stats["top_streak"] = 0.0
                    fresh.clear_subtype(child_name)
                    self._rt.update(fresh)
                stats["split"] += 1

        # 剪枝（低质量节点因得分低会自然排入底部）
        # F-2（第四轮 BUG-31 恢复）：orphan_strategy=delete，无父自动节点
        # 直接淘汰（有子节点时仍受保护），剪枝闭环对全部节点生效。
        pruned = self._rt.prune_lowest(
            threshold=prune_threshold,
            bottom_pct=prune_bottom_pct,
            reason="定期维护：长期垫底 + 低质量自动标记",
            actor="sub_agent",
            orphan_strategy="delete",
        )
        stats["pruned"] = pruned

        # 重叠审计（C3 契约接线）：总览.md 强调局部地图完整性是核心执念，
        # orphan_audit / overlap_audit 是其守卫。每轮 maintain 末尾执行一次，
        # 把高重叠节点对写入 maintenance_log 即可——prune/merge 已在另一分支处理。
        try:
            audit_pairs = self.overlap_audit()
            stats["overlap_audit_pairs"] = len(audit_pairs)
            stats["overlap_audit"] = audit_pairs
        except Exception as e:  # noqa: BLE001
            # 审计失败不应阻塞维护主流程
            logger.warning("overlap_audit 失败: %s", e)
            stats["overlap_audit_pairs"] = 0
            stats["overlap_audit"] = []

        return stats

    # ═══════════════════════════════════════════════════════════════
    # Step 39：周期性重叠审计
    # ═══════════════════════════════════════════════════════════════

    def overlap_audit(self) -> list[dict[str, Any]]:
        """Step 39：对路由表所有同根分类节点执行两两重叠检测。

        审计结果不修改路由表结构，仅记录审计日志并返回高重叠节点对。
        供主代理或人工判断是否应合并。

        算法（第七批 F-3 重写）：
        - 按根分类分组
        - 同组内两两比较，使用 `OverlapChecker.check_pair()` 的 **O(1) 成对**
          语义，整体复杂度 O(n²/2)
        - 重叠率达到阈值的节点对写入 maintenance_log —— **日志先缓存、
          审计结束后统一落库**，避免写库刷新缓存 / 放大 local_map

        为什么不再用 `check()`：
            `check()` 是「候选 vs 全表，取最大值」的 O(n) 语义，对 O(n²)
            个节点对调用它会退化成 **O(n³)**；且它返回的是"a 与全表最相似
            节点"的重叠率，不是 a 与 b 的（这正是 BUG-03 张冠李戴的根源）。
            此前靠 `max_overlap_with == b` 过滤来打补丁，既慢又漏报。

        Returns:
            高重叠节点对列表，每项包含 category_a, category_b, overlap,
            decision, merge_target
        """
        # 重叠审计所需的边界词提取与分根阈值仍需 overlap_checker 私有函数
        # 但决策常量已通过 self._rt.DECISION_* 暴露，避免上层 import 内部常量
        from src.overlap_checker import _extract_boundary_words, get_threshold_for_root

        all_entries = self._storage.query_routing_entries()
        # 按根分类分组
        by_root: dict[str, list[RoutingTableEntry]] = {}
        for entry in all_entries:
            root = entry.category_id.split(".")[0]
            by_root.setdefault(root, []).append(entry)

        # 第七批 F-3：每个节点的边界词只提取一次（O(n)），供 O(n²) 次
        # 两两比较复用，避免同一段边界文本被重复分词 O(n) 次
        words_cache: dict[str, set[str]] = {
            e.category_id: _extract_boundary_words(e.local_map.boundary_rules)
            for e in all_entries
        }

        high_overlap_pairs: list[dict[str, Any]] = []
        # 待落库的节点：category_id -> entry（去重后统一写，减少写放大）
        dirty: dict[str, RoutingTableEntry] = {}

        for root, entries in by_root.items():
            threshold = get_threshold_for_root(root, default=self._rt.threshold)
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    a, b = entries[i], entries[j]
                    # 真正的成对重叠率（O(1)），不再做全表取 max
                    overlap = self._rt.check_pair(
                        a, b,
                        words_a=words_cache[a.category_id],
                        words_b=words_cache[b.category_id],
                    )
                    decision = self._rt.decide(overlap, threshold)
                    if decision not in (self._rt.DECISION_MERGE, self._rt.DECISION_UNCERTAIN):
                        continue

                    merge_target = b.category_id
                    high_overlap_pairs.append({
                        "category_a": a.category_id,
                        "category_b": b.category_id,
                        "overlap": overlap,
                        "decision": decision,
                        "merge_target": merge_target,
                    })

                    log_msg = (
                        f"重叠审计发现高重叠节点对："
                        f"'{a.category_id}' ↔ '{b.category_id}'，"
                        f"重叠率 {overlap:.4f}，"
                        f"决策 {decision}，"
                        f"建议合并至 '{merge_target}'"
                    )
                    a.local_map.append_log("overlap_audit", log_msg, "sub_agent")
                    b.local_map.append_log("overlap_audit", log_msg, "sub_agent")
                    dirty[a.category_id] = a
                    dirty[b.category_id] = b

        # 审计结束后统一落库（而非每判定一对就写两次）
        for entry in dirty.values():
            self._rt.update(entry)

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
                self._rt.update(entry)
                skipped_quality.append(entry.category_id)
                continue
            skill = self._compiler.compile_from_entry(entry)
            compiled.append(skill)

        return compiled
