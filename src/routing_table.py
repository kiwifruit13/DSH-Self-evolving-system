"""路由表模块 — 规避洞察路由表的核心操作层。

职责：
- CRUD：基于 Storage 的路由表条目增删改查
- 排序：基于 ScoreCalculator 的四维排序 + 时间衰减
- 分裂（Split）：高频父节点自动下钻，生成子节点
- 剪枝（Prune）：长期垫底节点自动合并

所有写操作都会在 local_map.maintenance_log 中追加记录。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    Tag,
)
from src.overlap_checker import OverlapChecker
from src.scoring import ScoreBreakdown, ScoreCalculator, ScoreConfig
from src.storage import Storage
from src.tag_system import inherit_tags

MAX_SPLIT_DEPTH = 3
"""允许的最大分裂深度（category_id 的点数）"""

EMPTY_STATS: dict[str, float] = {
    "freq": 0.0,
    "impact": 0.0,
    "trend": 0.0,
    "recover_cost": 0.0,
}
"""子节点初始统计值——从零积累"""


class SplitRejectedError(ValueError):
    """子节点分裂被重叠校验拒绝。"""

    def __init__(
        self,
        message: str,
        max_overlap: float,
        max_overlap_with: str | None,
    ) -> None:
        super().__init__(message)
        self.max_overlap = max_overlap
        self.max_overlap_with = max_overlap_with


class MergePlan:
    """剪枝合并计划。

    每个待剪枝节点关联到其父节点，合并时将子节点的 stats
    加到父节点，tags 取并集，然后删除子节点。
    """

    def __init__(self, target_id: str, parent_id: str) -> None:
        self.target_id = target_id
        self.parent_id = parent_id


class RoutingTable:
    """路由表操作层。

    Args:
        storage: 底层持久化存储
        score_config: 排序计算器配置，默认使用标准权重
        overlap_checker: 重叠校验器，用于分裂时的语义门禁
    """

    def __init__(
        self,
        storage: Storage,
        score_config: ScoreConfig | None = None,
        overlap_checker: OverlapChecker | None = None,
    ) -> None:
        self._storage = storage
        self._scorer = ScoreCalculator(score_config)
        self._overlap_checker = overlap_checker or OverlapChecker(storage)

    # ═══════════════════════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════════════════════

    def insert(self, entry: RoutingTableEntry) -> RoutingTableEntry:
        """插入路由表条目。若条目已存在则抛出 ValueError。

        注意：此方法保证互斥——不存在才插入，已存在报错。
        需要幂等写入请使用 update()。
        """
        existing = self._storage.get_routing_entry(entry.category_id)
        if existing is not None:
            raise ValueError(
                f"路由表条目 '{entry.category_id}' 已存在，"
                "请使用 update() 或 create_node()"
            )
        self._storage.upsert_routing_entry(entry)
        return entry

    def get(self, category_id: str) -> RoutingTableEntry | None:
        """按 category_id 精确查询。"""
        return self._storage.get_routing_entry(category_id)

    def update(self, entry: RoutingTableEntry) -> RoutingTableEntry:
        """更新路由表条目（幂等：不存在则创建，已存在则覆盖）。"""
        self._storage.upsert_routing_entry(entry)
        return entry

    def delete(self, category_id: str) -> bool:
        """删除路由表条目。

        删除前检查是否存在子节点（parent_path = category_id），
        若有则抛出 ValueError 防止产生孤立引用。
        """
        if self._storage.has_child_nodes(category_id):
            raise ValueError(
                f"无法删除 '{category_id}'：存在以它为父节点的子节点，"
                "请先处理子节点或改用 merge_into_parent()"
            )
        return self._storage.delete_routing_entry(category_id)

    def delete_force(self, category_id: str) -> bool:
        """强制删除：先删除全部子孙节点再删除自身。

        与 delete() 的区别：delete() 遇到子节点时抛出异常，
        而 delete_force() 递归删除整棵子树。

        实现用显式栈做后序遍历（先删叶子、后删自身），并一次拉取全表
        构建 parent_path → children 邻接，避免深树触发 Python 递归
        深度限制，也避免每层重复全表扫描。

        注意：删除的节点不会写入 pending_queue（区别于 prune）。

        Args:
            category_id: 要删除的节点

        Returns:
            True 表示子树（含自身）删除成功
        """
        # 一次拉取全部节点，构建 parent_path -> children 邻接，避免逐层全表扫描
        all_entries = self._storage.query_routing_entries()
        children_of: dict[str, list[str]] = {}
        for e in all_entries:
            children_of.setdefault(e.local_map.parent_path, []).append(e.category_id)

        to_delete: list[str] = []
        stack = [category_id]
        while stack:
            cur = stack.pop()
            to_delete.append(cur)
            for child in children_of.get(cur, []):
                stack.append(child)
        # 先删叶子（逆 DFS 序），父节点最后删，避免中途出现悬空子引用
        for node_id in reversed(to_delete):
            self._storage.delete_routing_entry(node_id)
        return True

    def orphan_audit(self) -> list[dict[str, Any]]:
        """导航地图完整性体检：扫描引用断裂（孤儿/悬空）。

        这是「规避洞察路由表 = 导航地图」第一约束的自我检验：
        - `orphan_parent`：节点的 parent_path 指向不存在的父节点
          （`root.xxx` 虚拟根除外，那是自动生成节点的合法占位）
        - `orphan_skill`：节点关联的 primary_skill_id 在 Skill 库中不存在

        Returns:
            孤子项清单，每项含 type / category_id / referenced_id / note。
            仅只读体检，不修改路由表。
        """
        entries = self._storage.query_routing_entries()
        existing_ids = {e.category_id for e in entries}
        orphans: list[dict[str, Any]] = []
        for e in entries:
            pp = e.local_map.parent_path
            if pp and not pp.startswith("root.") and pp not in existing_ids:
                orphans.append({
                    "type": "orphan_parent",
                    "category_id": e.category_id,
                    "referenced_id": pp,
                    "note": f"父节点 '{pp}' 不存在",
                })
            psid = e.primary_skill_id
            if psid and self._storage.get_skill(psid) is None:
                orphans.append({
                    "type": "orphan_skill",
                    "category_id": e.category_id,
                    "referenced_id": psid,
                    "note": f"关联 Skill '{psid}' 不存在",
                })
        return orphans

    def count(self) -> int:
        """路由表条目总数。"""
        return self._storage.count_routing_entries()

    # ═══════════════════════════════════════════════════════════════
    # 统一创建入口
    # ═══════════════════════════════════════════════════════════════

    def create_node(
        self,
        entry: RoutingTableEntry,
        validate_overlap: bool = True,
        candidate_signature: str = "",
        candidate_boundary: str = "",
    ) -> RoutingTableEntry:
        """统一创建路由表节点入口。

        包含完整的安全检查链：
        1. 互斥检查（存在则报错）
        2. 重叠校验（可选，默认开启）
        3. 写入存储层

        Args:
            entry: 待创建的 RoutingTableEntry
            validate_overlap: 是否执行重叠校验
            candidate_signature: 候选节点的错误签名（用于重叠计算）
            candidate_boundary: 候选节点的边界规则（用于重叠计算）

        Returns:
            成功创建的 RoutingTableEntry

        Raises:
            ValueError: 条目已存在或重叠率超过阈值
            SplitRejectedError: 重叠校验拒绝
        """
        # 1. 互斥检查
        existing = self._storage.get_routing_entry(entry.category_id)
        if existing is not None:
            raise ValueError(f"路由表条目 '{entry.category_id}' 已存在")

        # 2. 重叠校验
        if validate_overlap and candidate_boundary:
            result = self._overlap_checker.check(
                entry.category_id,
                candidate_signature,
                candidate_boundary,
            )
            if not result.allows_creation:
                raise SplitRejectedError(
                    message=(
                        f"创建节点 '{entry.category_id}' 被拒绝："
                        f"与 '{result.max_overlap_with}' 重叠率"
                        f" {result.max_overlap:.4f} 超过阈值"
                        f" {result.threshold}"
                    ),
                    max_overlap=result.max_overlap,
                    max_overlap_with=result.max_overlap_with,
                )

        # 3. 写入存储
        self._storage.upsert_routing_entry(entry)
        return entry

    # ═══════════════════════════════════════════════════════════════
    # Step 47：批量操作接口
    # ═══════════════════════════════════════════════════════════════

    def bulk_upsert(
        self,
        entries: list[RoutingTableEntry],
    ) -> list[RoutingTableEntry]:
        """Step 47：批量 upsert，一次性写入多条。

        与 create_node() 不同：跳过重叠校验（upsert 语义为幂等更新）。
        适合迁移/导入场景。

        Args:
            entries: 要写入的条目列表

        Returns:
            成功写入的条目列表
        """
        results: list[RoutingTableEntry] = []
        for entry in entries:
            self._storage.upsert_routing_entry(entry)
            results.append(entry)
        return results

    def bulk_create(
        self,
        entries: list[RoutingTableEntry],
        validate_overlap: bool = True,
        signatures: dict[str, str] | None = None,
        boundaries: dict[str, str] | None = None,
    ) -> list[RoutingTableEntry]:
        """Step 47：批量创建，支持逐项重叠校验。

        逐个调用 create_node()，遇到第一个失败时停止并抛出异常。
        已成功写入的条目保持原状（非事务性回滚）。

        Args:
            entries: 要创建的条目列表
            validate_overlap: 是否执行重叠校验（默认 True）
            signatures: 可选的签名映射 {category_id: signature}
            boundaries: 可选的边界映射 {category_id: boundary}

        Returns:
            成功创建的全部条目列表
        """
        results: list[RoutingTableEntry] = []
        for entry in entries:
            sig = (signatures or {}).get(entry.category_id, "")
            bnd = (boundaries or {}).get(entry.category_id, "")
            created = self.create_node(
                entry,
                validate_overlap=validate_overlap,
                candidate_signature=sig,
                candidate_boundary=bnd,
            )
            results.append(created)
        return results

    # ═══════════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════════

    def query(
        self,
        root_category: str | None = None,
        tags: set[Tag] | None = None,
        parent_path: str | None = None,
    ) -> list[RoutingTableEntry]:
        """查询路由表条目，支持根分类/标签/父路径过滤（AND 语义）。

        parent_path 直接下推到 SQL WHERE 子句，避免全表扫描后 Python 层过滤。
        """
        return self._storage.query_routing_entries(
            root_category=root_category, tags=tags, parent_path=parent_path
        )

    def query_by_expression(
        self,
        query_expr: dict[str, Any],
        root_category: str | None = None,
        parent_path: str | None = None,
    ) -> list[RoutingTableEntry]:
        """使用复合标签表达式查询（AND/OR/NOT/分组）。

        Args:
            query_expr: TagQueryBuilder.build() 返回的查询表达式
            root_category: 可选的根分类过滤
            parent_path: 可选的父路径过滤（SQL 层下推，避免全表扫描）

        Returns:
            满足查询表达式的路由表条目列表（按四维排序降序）。
        """
        from src.tag_query import evaluate_query

        candidates = self._storage.query_routing_entries(
            root_category=root_category, parent_path=parent_path
        )

        matched = [
            entry
            for entry in candidates
            if evaluate_query(entry.tags, query_expr)
        ]

        # 直接在 matched 列表上计算得分，不依赖外部 pre-rank
        scores = {
            entry.category_id: self._scorer.score_with_breakdown(entry).final_score
            for entry in matched
        }
        matched.sort(key=lambda e: scores[e.category_id], reverse=True)

        return matched

    def query_all(self) -> list[RoutingTableEntry]:
        """获取全部路由表条目。"""
        return self.query()

    # ═══════════════════════════════════════════════════════════════
    # 排序
    # ═══════════════════════════════════════════════════════════════

    def rank(
        self,
        days_since_last_seen: float = 0.0,
        root_category: str | None = None,
        rank_by: str = "overall",
        inactive_days: float = 0.0,
    ) -> list[ScoreBreakdown]:
        """对路由表条目排序。

        Step 44：多目标排序接口。

        Step 46：节点活跃度标记。
        当 inactive_days > 0 时，超过该天数未出现的节点被标记为非活跃，
        默认从排序结果中排除（除非 include_inactive=True）。

        rank_by 取值：
        - "overall"（默认）：按最终综合得分降序
        - "cost"：按恢复代价升序（cost 越低越优先）
        - "impact"：按影响得分降序
        - "freq"：按频率降序
        - "trend"：按趋势降序
        - "recency"：按时间衰减因子降序（最近出现越优先）

        Args:
            days_since_last_seen: 全局回退衰减参数（当 last_seen 不可用时兜底）
            root_category: 可选的根分类过滤
            rank_by: 排序维度（默认 "overall"）
            inactive_days: 非活跃天数阈值，超过此天数未出现的节点排除（默认 0=不排除）

        Returns:
            按指定维度排序的 ScoreBreakdown 列表。
        """
        if rank_by not in ("overall", "cost", "impact", "freq", "trend", "recency"):
            raise ValueError(f"不支持的排序维度: {rank_by}，可选: overall/cost/impact/freq/trend/recency")

        entries = self.query(root_category=root_category)
        breakdowns: list[ScoreBreakdown] = []
        now = datetime.now(timezone.utc)
        for entry in entries:
            # Step 46：非活跃节点过滤
            if inactive_days > 0:
                last_seen_str = entry.stats.get("last_seen", "")
                if isinstance(last_seen_str, str) and last_seen_str:
                    try:
                        last_seen = datetime.fromisoformat(last_seen_str)
                        days_since = (now - last_seen).total_seconds() / 86400
                        if days_since > inactive_days:
                            continue
                    except (ValueError, TypeError):
                        pass

            bd = self._scorer.score_with_breakdown(entry, days_since_last_seen=None)
            if bd.days_since_last_seen == 0.0 and days_since_last_seen > 0:
                bd = self._scorer.score_with_breakdown(entry, days_since_last_seen=days_since_last_seen)
            breakdowns.append(bd)

        sort_key = {
            "overall": lambda b: b.final_score,
            "cost": lambda b: b.cost_normalized,  # 已反向归一化：越高=代价越低
            "impact": lambda b: b.impact_normalized,
            "freq": lambda b: b.freq_normalized,
            "trend": lambda b: b.trend_normalized,
            "recency": lambda b: b.decay_factor,
        }[rank_by]
        breakdowns.sort(key=sort_key, reverse=True)
        return breakdowns

    def _compute_days_since_last_seen(
        self,
        entry: RoutingTableEntry,
        now: datetime,
        fallback: float = 0.0,
    ) -> float:
        """计算条目距今的天数。

        从 stats["last_seen"] ISO 时间戳读取，无法解析时回退到 fallback。
        """
        last_seen_str = entry.stats.get("last_seen", "")
        if not last_seen_str:
            return fallback
        if not isinstance(last_seen_str, str):
            return fallback
        try:
            last_seen = datetime.fromisoformat(last_seen_str)
            delta = now - last_seen
            days = delta.total_seconds() / 86400
            return max(0.0, days)
        except (ValueError, TypeError):
            return fallback

    def top_k(
        self,
        k: int,
        days_since_last_seen: float = 0.0,
        root_category: str | None = None,
    ) -> list[ScoreBreakdown]:
        """返回得分最高的 K 个条目（使用 rank() 的 per-entry 衰减）。"""
        return self.rank(days_since_last_seen=days_since_last_seen, root_category=root_category)[:k]

    def score_entry(
        self, entry: RoutingTableEntry, days_since_last_seen: float = 0.0
    ) -> ScoreBreakdown:
        """计算单个条目的得分明细（使用 per-entry 衰减）。"""
        days = self._compute_days_since_last_seen(entry, datetime.now(timezone.utc), days_since_last_seen)
        return self._scorer.score_with_breakdown(entry, days)

    # ═══════════════════════════════════════════════════════════════
    # 分裂（Split）
    # ═══════════════════════════════════════════════════════════════

    def split(
        self,
        parent_category_id: str,
        child_name: str,
        reason: str,
        actor: str = "sub_agent",
        child_boundary_rules: str | None = None,
        child_logic_signature: str | None = None,
        child_overrides: set[Tag] | None = None,
        child_removals: set[Tag] | None = None,
    ) -> RoutingTableEntry:
        """从父节点分裂出一个子节点。

        分裂流程包含完整的安全检查：
        1. 父节点必须存在
        2. 子节点 category_id 不可与已有节点冲突
        3. 树深度不得超过 MAX_SPLIT_DEPTH
        4. 子节点与已有节点（含同级兄弟）语义不可重叠
        5. 子节点 stats 从零开始积累

        Args:
            parent_category_id: 父节点 ID，如 "network.timeout"
            child_name: 子节点名称片段，如 "connect" → "network.timeout.connect"
            reason: 分裂原因（写入 maintenance_log）
            actor: 操作者（"human" / "sub_agent"）
            child_boundary_rules: 子节点的边界规则，默认从父节点继承
            child_logic_signature: 子节点的逻辑签名，默认从父节点继承
            child_overrides: 子节点要覆盖的标签
            child_removals: 子节点要移除的标签

        Returns:
            新创建的子节点 RoutingTableEntry。

        Raises:
            ValueError: 父节点不存在 / 子节点已存在 / 超过深度限制
            SplitRejectedError: 重叠校验拒绝
        """
        parent = self._storage.get_routing_entry(parent_category_id)
        if parent is None:
            raise ValueError(f"父节点 '{parent_category_id}' 不存在，无法分裂")

        child_category_id = f"{parent_category_id}.{child_name}"

        # 检查子节点是否已存在
        existing = self._storage.get_routing_entry(child_category_id)
        if existing is not None:
            raise ValueError(f"子节点 '{child_category_id}' 已存在")

        # 检查深度限制
        if len(child_category_id.split(".")) > MAX_SPLIT_DEPTH:
            raise ValueError(
                f"子节点 '{child_category_id}' 超过最大深度 {MAX_SPLIT_DEPTH}"
            )

        # 遗传标签 + 变异
        child_tags = inherit_tags(
            parent.tags,
            overrides=child_overrides,
            removals=child_removals,
        )

        child_boundary = child_boundary_rules or parent.local_map.boundary_rules
        child_logic = child_logic_signature or parent.local_map.logic_signature

        # 构建子节点的 LocalMindMap
        child_lm = LocalMindMap(
            node_id=child_category_id,
            parent_path=parent_category_id,
            focus_description=(
                f"处理 {parent.local_map.focus_description} 中的"
                f" {child_name} 子类问题"
            ),
            boundary_rules=child_boundary,
            logic_signature=child_logic,
        )
        child_lm.append_log(
            "create", f"从父节点 '{parent_category_id}' 分裂", actor
        )

        child_entry = RoutingTableEntry(
            category_id=child_category_id,
            # Step 42：分裂后 stats 重分配
            # 子节点继承父节点 freq 的 30%（保守估计），防止从零积累导致初期排序偏差
            stats=self._redistribute_stats(parent, child_proportion=0.3),
            local_map=child_lm,
            tags=child_tags,
        )

        # Step 83：同级兄弟重叠检测（在任何持久化之前执行，避免留下孤立子节点）
        self._check_sibling_overlap(parent, child_entry, child_logic, child_boundary)

        # 重叠校验 + 写入存储：兄弟校验通过后才允许持久化
        self.create_node(
            child_entry,
            validate_overlap=True,
            candidate_signature=child_logic,
            candidate_boundary=child_boundary,
        )

        # 在父节点 maintenance_log 中记述
        parent.local_map.append_log("split", reason, actor)
        self._storage.upsert_routing_entry(parent)

        # 更新父节点 stats（Step 42：按子节点占比减少父节点 freq）
        self._reduce_parent_stats(parent, child_proportion=0.3)
        self._storage.upsert_routing_entry(parent)

        return child_entry

    # ═══════════════════════════════════════════════════════════════
    # Step 42：分裂后 stats 重分配
    # ═══════════════════════════════════════════════════════════════

    def _redistribute_stats(
        self,
        parent: RoutingTableEntry,
        child_proportion: float = 0.3,
    ) -> dict[str, float | str]:
        """Step 42：从父节点 stats 中按占比分配给子节点。

        假设子节点覆盖父节点的历史场景的比例为 child_proportion（默认 30%）。
        父节点保留 (1 - proportion) 的 freq，子节点继承 proportion 的 freq。
        impact / trend / recover_cost 保持不变（子节点继承父节点的经验）。

        注意：sample_count 和 last_seen 从父节点继承。

        Args:
            parent: 父节点 RoutingTableEntry
            child_proportion: 子节点继承的 freq 占比 [0, 1]

        Returns:
            子节点的初始 stats
        """
        parent_freq = float(parent.stats.get("freq", 0.0))
        child_freq = max(0.0, parent_freq * child_proportion)

        child_stats: dict[str, float | str] = {
            "freq": child_freq,
            "impact": float(parent.stats.get("impact", 0.0)),
            "trend": 0.0,  # 新节点趋势从零开始
            "recover_cost": float(parent.stats.get("recover_cost", 1.0)),
            "sample_count": float(parent.stats.get("sample_count", 0)),
        }

        # last_seen 继承父节点的时间戳（如果存在）
        last_seen = parent.stats.get("last_seen", "")
        if isinstance(last_seen, str) and last_seen:
            child_stats["last_seen"] = last_seen

        return child_stats

    def _reduce_parent_stats(
        self,
        parent: RoutingTableEntry,
        child_proportion: float = 0.3,
    ) -> None:
        """从父节点 stats 中减去分配给子节点的比例。"""
        parent_freq = float(parent.stats.get("freq", 0.0))
        parent.stats["freq"] = max(0.0, parent_freq * (1.0 - child_proportion))

    # ═══════════════════════════════════════════════════════════════
    # 同级兄弟重叠检测（Step 83）
    # ═══════════════════════════════════════════════════════════════

    def _check_sibling_overlap(
        self,
        parent: RoutingTableEntry,
        candidate: RoutingTableEntry,
        candidate_signature: str,
        candidate_boundary: str,
    ) -> None:
        """检查候选子节点与父节点的已有兄弟子节点是否重叠。

        比 create_node() 的全量重叠检查更精准：
        - 只检查父节点的直接子节点（同父）
        - 不同根分类互不阻挡（与 create_node 一致）
        - 提供专门针对同级兄弟的错误信息

        Args:
            parent: 父节点
            candidate: 待分裂的候选子节点
            candidate_signature: 候选节点的逻辑签名
            candidate_boundary: 候选节点的边界规则

        Raises:
            SplitRejectedError: 与同级兄弟重叠率超过阈值
        """
        siblings = self.query(parent_path=parent.category_id)
        if not siblings:
            return

        max_overlap = 0.0
        max_overlap_with: str | None = None

        for sibling in siblings:
            # 跳过自身
            if sibling.category_id == candidate.category_id:
                continue
            # 不同根分类互不阻挡
            if sibling.category_id.split(".")[0] != parent.category_id.split(".")[0]:
                continue
            # 计算签名相似度
            from src.overlap_checker import (
                _boundary_overlap,
                _make_temp_entry,
                _signature_similarity,
            )

            sig_sim = _signature_similarity(
                candidate_signature, sibling.local_map.logic_signature
            )
            tmp = _make_temp_entry(
                sibling, candidate_boundary, candidate_signature
            )
            bound_overlap = _boundary_overlap(sibling, tmp)
            total = self._overlap_checker._sig_w * sig_sim + self._overlap_checker._bound_w * bound_overlap

            if total > max_overlap:
                max_overlap = total
                max_overlap_with = sibling.category_id

        if max_overlap > self._overlap_checker._threshold:
            raise SplitRejectedError(
                message=(
                    f"分裂 '{candidate.category_id}' 被拒绝："
                    f"与同级兄弟 '{max_overlap_with}' 重叠率"
                    f" {max_overlap:.4f} 超过阈值"
                    f" {self._overlap_checker._threshold}"
                ),
                max_overlap=max_overlap,
                max_overlap_with=max_overlap_with,
            )

    # ═══════════════════════════════════════════════════════════════
    # 剪枝（Prune / Merge）
    # ═══════════════════════════════════════════════════════════════

    def merge_into_parent(
        self,
        child_category_id: str,
        reason: str = "剪枝合并到父节点",
        actor: str = "sub_agent",
    ) -> MergePlan:
        """将子节点合并到其父节点。

        合并规则：
        - 子节点的 stats 累加到父节点
        - tags 取并集
        - 父节点 maintenance_log 追加 "merged_from" 记录
        - 子节点被硬删除

        Args:
            child_category_id: 待合并的子节点 ID
            reason: 合并原因
            actor: 操作者

        Returns:
            MergePlan 对象，记录合并目标
        """
        child = self._storage.get_routing_entry(child_category_id)
        if child is None:
            raise ValueError(f"节点 '{child_category_id}' 不存在")

        parent_id = child.local_map.parent_path
        if not parent_id:
            raise ValueError(
                f"节点 '{child_category_id}' 没有父节点，无法合并"
            )

        parent = self._storage.get_routing_entry(parent_id)
        if parent is None:
            raise ValueError(f"父节点 '{parent_id}' 不存在")

        plan = MergePlan(target_id=child_category_id, parent_id=parent_id)

        # 若子节点仍有子节点，先将其重挂到父节点，避免删除后产生孤立引用
        for grandchild in self._storage.query_routing_entries(
            parent_path=child_category_id
        ):
            grandchild.local_map.parent_path = parent_id
            grandchild.local_map.append_log(
                "reparent",
                f"父节点 '{child_category_id}' 合并后重挂到 '{parent_id}'",
                actor,
            )
            self._storage.upsert_routing_entry(grandchild)

        # stats 合并：freq 累加（累计值），其余归一化指标取二者较大值，避免溢出
        for k in ("freq",):
            if k in parent.stats or k in child.stats:
                parent.stats[k] = float(parent.stats.get(k, 0.0)) + float(
                    child.stats.get(k, 0.0)
                )
        for k in ("impact", "trend", "recover_cost"):
            if k in parent.stats or k in child.stats:
                parent.stats[k] = max(
                    float(parent.stats.get(k, 0.0)),
                    float(child.stats.get(k, 0.0)),
                )

        # tags 取并集
        parent.tags = parent.tags | child.tags

        # 父节点记述
        parent.local_map.append_log(
            "merged_from",
            f"合并了子节点 '{child_category_id}': {reason}",
            actor,
        )
        self._storage.upsert_routing_entry(parent)

        # 子节点记述 + 删除
        child.local_map.append_log("merged", f"被合并到父节点 '{parent_id}'", actor)
        self._storage.delete_routing_entry(child_category_id)

        return plan

    def prune_lowest(
        self,
        threshold: float = 0.1,
        bottom_pct: float = 0.1,
        reason: str = "长期垫底自动合并",
        actor: str = "sub_agent",
        execute: bool = True,
    ) -> list[MergePlan]:
        """自动剪枝：将得分排名末尾 bottom_pct 的节点标记或合并。

        执行两步操作：
        1. 识别待剪枝节点（得分 <= threshold 的末尾节点）
        2. 可选地将它们合并到父节点（execute=True）

        Args:
            threshold: 得分阈值，低于此值的节点将被标记
            bottom_pct: 末尾百分比（如 0.1 = 末 10%）
            reason: 剪枝原因
            actor: 操作者
            execute: 是否执行合并（False 时仅返回计划）

        Returns:
            MergePlan 列表，每个计划对应一个待合并节点。
            如果 execute=True，合并操作已执行。
        """
        all_scores = self.rank()
        if not all_scores:
            return []

        total = len(all_scores)
        bottom_n = max(1, int(total * bottom_pct))
        bottom_scores = all_scores[-bottom_n:]

        plans: list[MergePlan] = []
        for score in bottom_scores:
            if score.final_score > threshold:
                continue

            child = self._storage.get_routing_entry(score.category_id)
            if child is None:
                continue

            parent_id = child.local_map.parent_path
            if not parent_id:
                continue

            plan = MergePlan(
                target_id=score.category_id, parent_id=parent_id
            )

            # 在 maintenance_log 中记述
            child.local_map.append_log("prune_pending", reason, actor)
            self._storage.upsert_routing_entry(child)

            plans.append(plan)

        # 执行合并
        if execute:
            for plan in plans:
                try:
                    self.merge_into_parent(plan.target_id, reason, actor)
                except ValueError:
                    # 合并失败时跳过，不影响其他节点
                    pass

        return plans
