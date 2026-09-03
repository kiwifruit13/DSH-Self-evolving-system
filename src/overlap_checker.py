"""重叠率校验器 — 子代理新建分类前的"门禁"。

目的：
当子代理准备从反馈举证中创建新路由表节点时，必须校验新节点
与现有路由表节点的重叠率。若重叠率 >= 阈值（默认 70%），则拒绝创建，
要求子代理合并或复用已有节点，防止路由表膨胀和分类漂移。

重叠率计算维度（v2 修复后）：
1. 错误签名相似度（Levenshtein 距离归一化）——权重 0.55
2. 边界规则重叠度（子集检测 + Jaccard）——权重 0.45

注意：
- 根分类维度已从公式中移除，改为硬性过滤：不同根分类默认不重叠
- 包含关系检测优先于 Jaccard（子集/超集直接计算）
- 中文停用词不参与 Jaccard 计算

Step 38：决策枚举 + 合并建议
- ACCEPT：允许创建（重叠率低于阈值 70%）
- SPLIT：边界重叠，允许创建但建议人工审核
- MERGE：拒绝创建，建议合并到指定已有节点
- UNCERTAIN：高度重叠（>=0.95），无法区分，建议人工确认

使用示例：
    checker = OverlapChecker(storage)
    result = checker.check("network.http_500", "修复 HTTP 500 错误", "仅处理 HTTP 500")
    if result.decision == "ACCEPT":
        # 允许创建
    elif result.decision == "MERGE":
        # 合并到 result.merge_target
"""
from __future__ import annotations

from typing import Any

from src.models import RoutingTableEntry
from src.storage import Storage

# Step 38：决策枚举
DECISION_ACCEPT = "ACCEPT"
DECISION_SPLIT = "SPLIT"
DECISION_MERGE = "MERGE"
DECISION_UNCERTAIN = "UNCERTAIN"
"""
ACCEPT：重叠率 < threshold * 0.7，明确区分，允许创建
SPLIT：重叠率 ∈ [threshold * 0.7, threshold)，建议人工审核
MERGE：重叠率 >= threshold 且 < 0.95，建议合并到已有节点
UNCERTAIN：重叠率 >= 0.95，无法区分，需人工确认
"""

# 中文停用词表——不参与 Jaccard 计算
_STOP_WORDS = frozenset({
    "仅", "处理", "不", "进行", "相关", "类", "已", "尝试",
    "过", "的", "了", "和", "或", "与", "在", "为", "将",
    "只", "等", "有", "无", "被", "对", "其", "所", "该",
    "所有", "任何", "一些", "每个", "这种", "这些", "那些",
})

# 不同根分类的阈值配置
_DEFAULT_THRESHOLD_BY_ROOT: dict[str, float] = {
    "network": 0.65,
    "data_parsing": 0.80,
    "llm_inference": 0.60,
    "resource_exhaustion": 0.75,
    "permission": 0.75,
}
_GENERIC_THRESHOLD = 0.70


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离。"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def _signature_similarity(sig1: str, sig2: str) -> float:
    """错误签名相似度：[0, 1]，1 表示完全相同。"""
    if not sig1 or not sig2:
        return 0.0
    s1, s2 = sig1.lower(), sig2.lower()
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    # 第七批 F-3：完全相同直接短路。overlap_audit 的 O(n²) 对里存在大量
    # 同签名节点（同源举证批量入库），此前每对都要跑一遍 O(L²) 的
    # Levenshtein，是审计耗时的主要来源之一。
    if s1 == s2:
        return 1.0
    distance = _levenshtein_distance(s1, s2)
    return 1.0 - (distance / max_len)


def _extract_boundary_words(boundary: str) -> set[str]:
    """提取边界规则中的关键词集合（去除停用词和短词）。"""
    raw_words = boundary.split()
    words: set[str] = set()
    punctuation = "，。、；：！？\"'\"()（）[]【】"
    for w in raw_words:
        cleaned = w.lower().translate(str.maketrans("", "", punctuation))
        if len(cleaned) < 2:
            continue
        if cleaned in _STOP_WORDS:
            continue
        words.add(cleaned)
    return words


def _boundary_overlap_from_words(words1: set[str], words2: set[str]) -> float:
    """由**已提取**的边界词集合计算重叠度（纯集合运算，无文本解析）。

    第七批 F-3：overlap_audit 的 O(n²) 次比较中，若每次都调用
    `_extract_boundary_words` 重新分词，同一节点的边界文本会被重复解析
    O(n) 次。拆出本函数后，调用方可对每个节点**只分词一次**再两两比较。
    """
    if not words1 or not words2:
        return 0.0

    # 包含关系优先检测
    if words1.issubset(words2):
        return len(words1) / len(words2)
    if words2.issubset(words1):
        return len(words2) / len(words1)

    intersection = words1 & words2
    union = words1 | words2

    return len(intersection) / len(union)


def _boundary_overlap(entry1: RoutingTableEntry, entry2: RoutingTableEntry) -> float:
    """边界规则重叠度。

    算法（v2 修复后）：
    1. 先检测包含关系：如果一方是另一方的子集/超集，
       返回 len(子集) / len(全集)，直接判定高重叠
    2. 否则使用 Jaccard 系数（已去除停用词和短词）

    批量比较场景（如 overlap_audit）请预先调用 `_extract_boundary_words`
    并改用 `_boundary_overlap_from_words`，避免重复分词。
    """
    return _boundary_overlap_from_words(
        _extract_boundary_words(entry1.local_map.boundary_rules),
        _extract_boundary_words(entry2.local_map.boundary_rules),
    )


def _make_temp_entry(
    original: RoutingTableEntry, boundary: str, logic_signature: str
) -> RoutingTableEntry:
    """创建临时条目用于边界重叠度计算。"""
    from src.models import LocalMindMap

    lm = LocalMindMap(
        node_id=original.category_id + ".temp",
        parent_path=original.local_map.parent_path,
        focus_description=original.local_map.focus_description,
        boundary_rules=boundary,
        logic_signature=logic_signature,
    )
    return RoutingTableEntry(
        category_id=original.category_id,
        stats=original.stats,
        local_map=lm,
        tags=original.tags,
    )


def _ancestor_ids(category_id: str) -> set[str]:
    """由层级 ID 派生祖先链（不含自身）。

    "a.b.c" → {"a", "a.b"}；"network" → set()。

    BUG-50 修复配套：祖先与后代天然语义重叠（子节点继承父节点的签名与
    边界），祖先参与比较会让任何 depth≥3 的分裂在默认路径下必被拒。
    """
    parts = category_id.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts))}


class OverlapCheckResult:
    """重叠率检查结果。"""

    def __init__(
        self,
        candidate_id: str,
        candidate_signature: str,
        candidate_boundary: str,
        threshold: float = 0.7,
        max_overlap: float = 0.0,
        max_overlap_with: str | None = None,
        all_scores: list[dict[str, Any]] | None = None,
        decision: str = DECISION_ACCEPT,
        merge_target: str | None = None,
    ) -> None:
        self.candidate_id = candidate_id
        self.candidate_signature = candidate_signature
        self.candidate_boundary = candidate_boundary
        self._threshold = threshold
        self.max_overlap = max_overlap
        self.max_overlap_with = max_overlap_with
        self.all_scores = all_scores or []
        self.decision = decision
        self.merge_target = merge_target

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def allows_creation(self) -> bool:
        """重叠率是否低于阈值，允许创建新节点。"""
        return self.max_overlap < self._threshold

    @property
    def should_merge(self) -> bool:
        """是否应合并到已有节点。"""
        return self.decision == DECISION_MERGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "max_overlap": round(self.max_overlap, 4),
            "max_overlap_with": self.max_overlap_with,
            "allows_creation": self.allows_creation,
            "decision": self.decision,
            "merge_target": self.merge_target,
        }


class OverlapChecker:
    """重叠率校验器。

    Args:
        storage: 底层持久化存储
        threshold: 重叠率阈值，默认 0.7（70%）
        signature_weight: 签名相似度权重，默认 0.55
        boundary_weight: 边界重叠度权重，默认 0.45

    注意：root_weight 已移除，根分类改为硬性过滤维度。
    """

    def __init__(
        self,
        storage: Storage,
        threshold: float = 0.7,
        signature_weight: float = 0.55,
        boundary_weight: float = 0.45,
    ) -> None:
        self._storage = storage
        self._threshold = threshold
        self._sig_w = signature_weight
        self._bound_w = boundary_weight
        # BUG-34 修复：残留的空 _cache dict 已删除。缓存层（版本纪元 +
        # TTL + LRU）按 8/31 重构决策整体移除，check() 每次真实计算，
        # 不存在陈旧结论问题，也就无需任何失效机制。

    @property
    def threshold(self) -> float:
        return self._threshold

    def check(
        self,
        candidate_category_id: str,
        candidate_signature: str,
        candidate_boundary: str,
        root_category: str | None = None,
        exclude_category_id: str | None = None,
        exclude_ids: set[str] | None = None,
    ) -> OverlapCheckResult:
        """检查候选新节点与现有路由表节点的重叠率。

        Args:
            candidate_category_id: 候选新节点的 category_id
            candidate_signature: 候选新节点的错误签名（逻辑描述）
            candidate_boundary: 候选新节点的边界规则描述
            root_category: 可选的根分类过滤，仅在同根分类内计算重叠
            exclude_category_id: 可选，比较时排除的 category_id。
                对"已存在于路由表"的节点自身重跑检查（如 overlap_audit）时，
                应传入该节点的 category_id，避免 self-overlap=1.0 的假高重叠。
            exclude_ids: 可选，比较时排除的 category_id 集合。
                候选节点的**祖先链会自动排除**（BUG-50），无需调用方传入；
                本参数用于祖先链之外的额外排除项。

        Returns:
            OverlapCheckResult，包含最大重叠率和是否允许创建。

        根分类策略：
            - 如果指定了 root_category，只检查同根分类节点
            - 如果 root_category 为 None，从 candidate_category_id 中提取
            - 不同根分类的节点默认不参与重叠计算（不相互阻挡）
        """
        if root_category is None:
            root_category = candidate_category_id.split(".")[0]

        # 按根分类选择阈值
        effective_threshold = get_threshold_for_root(root_category, default=self._threshold)

        # 根分类下推到 SQL，避免对全表做 Python 层根分类过滤。
        # 旧实现拉全表后再按 category_id.split('.')[0] 过滤——节点越多浪费越大，
        # 且 Storage 已经有 root_category 下推的索引路径（BUG-16 修复），直接复用。
        existing = self._storage.query_routing_entries(root_category=root_category)
        filtered = list(existing)

        # 可选：排除指定 category_id。用于对"已存在于路由表"的节点重跑
        # 检查时（如 overlap_audit），避免命中 self-overlap=1.0 的假高重叠。
        if exclude_category_id is not None:
            filtered = [
                entry
                for entry in filtered
                if entry.category_id != exclude_category_id
            ]
        # BUG-01 修复：排除祖先链，避免父子天然重叠导致分裂必然被拒
        # BUG-50 修复：此前依赖**调用方**在 exclude_ids 里传全祖先链，而
        # `RoutingTable.split()` 只传了直接父节点 —— depth≥3 的分裂在默认
        # 路径（不传显式边界，子承父的签名/边界）下必然被祖父节点拒掉
        # （实测重叠率 0.775 > network 阈值 0.65）。候选 ID 本身已编码完整
        # 层级路径，祖先链可自足派生，改为在检查内部统一排除，任何调用方
        # 都不会再漏传。
        excluded = set(exclude_ids) if exclude_ids else set()
        excluded |= _ancestor_ids(candidate_category_id)
        if excluded:
            filtered = [
                entry for entry in filtered if entry.category_id not in excluded
            ]

        # 如果同根分类没有已有节点，直接允许创建
        if not filtered:
            return OverlapCheckResult(
                candidate_id=candidate_category_id,
                candidate_signature=candidate_signature,
                candidate_boundary=candidate_boundary,
                threshold=effective_threshold,
                max_overlap=0.0,
                max_overlap_with=None,
                all_scores=[],
                decision=DECISION_ACCEPT,
                merge_target=None,
            )

        all_scores: list[dict[str, Any]] = []
        max_overlap = 0.0
        max_overlap_with: str | None = None

        for entry in filtered:
            # 签名相似度：使用 candidate_signature 对比 entry 的 logic_signature
            sig_sim = _signature_similarity(
                candidate_signature, entry.local_map.logic_signature
            )

            # 边界重叠度（子集检测 + Jaccard）
            tmp_entry = _make_temp_entry(
                entry, candidate_boundary, candidate_signature
            )
            bound_overlap = _boundary_overlap(entry, tmp_entry)

            total = self._sig_w * sig_sim + self._bound_w * bound_overlap

            all_scores.append({
                "category_id": entry.category_id,
                "sig_similarity": round(sig_sim, 4),
                "boundary_overlap": round(bound_overlap, 4),
                "total_overlap": round(total, 4),
            })

            if total > max_overlap:
                max_overlap = total
                max_overlap_with = entry.category_id

        decision = self._decide(max_overlap, effective_threshold)
        merge_target = max_overlap_with if decision in (DECISION_MERGE, DECISION_UNCERTAIN) else None

        result = OverlapCheckResult(
            candidate_id=candidate_category_id,
            candidate_signature=candidate_signature,
            candidate_boundary=candidate_boundary,
            threshold=effective_threshold,
            max_overlap=max_overlap,
            max_overlap_with=max_overlap_with,
            all_scores=all_scores,
            decision=decision,
            merge_target=merge_target,
        )

        return result

    def check_pair(
        self,
        entry_a: RoutingTableEntry,
        entry_b: RoutingTableEntry,
        words_a: set[str] | None = None,
        words_b: set[str] | None = None,
    ) -> float:
        """计算**成对**重叠率（O(1)），不做全表扫描。

        第七批 F-3：overlap_audit 原先对每一对 (a, b) 都调用 `check()`，
        而 `check()` 是「候选 vs 全表，取最大值」的 O(n) 语义 —— 于是
        O(n²) 对 × O(n) 扫描 = **O(n³)**。语义上也不对（这正是 BUG-03
        「重叠率张冠李戴」的根源）。

        本方法只做 a 与 b 两个节点之间的重叠计算，复杂度 O(1)，
        使 audit 整体降为真正的 O(n²)，且报告的确实是这一对的值。

        Args:
            entry_a: 节点 A
            entry_b: 节点 B
            words_a: A 的边界词集合（预先由 `_extract_boundary_words` 提取）。
                批量比较时传入可避免重复分词，从 O(n²) 次分词降为 O(n) 次。
            words_b: B 的边界词集合，同上

        Returns:
            [0, 1] 范围内的重叠率（签名相似度与边界重叠度的加权和）
        """
        sig_sim = _signature_similarity(
            entry_a.local_map.logic_signature,
            entry_b.local_map.logic_signature,
        )
        if words_a is None:
            words_a = _extract_boundary_words(entry_a.local_map.boundary_rules)
        if words_b is None:
            words_b = _extract_boundary_words(entry_b.local_map.boundary_rules)
        bound_overlap = _boundary_overlap_from_words(words_a, words_b)
        return self._sig_w * sig_sim + self._bound_w * bound_overlap

    def _decide(self, max_overlap: float, threshold: float) -> str:
        """Step 38：根据最大重叠率和阈值判断决策。

        阈值分档：
        - [0, threshold*0.7)  → ACCEPT（明确区分）
        - [threshold*0.7, threshold) → SPLIT（边界，建议审核）
        - [threshold, 0.95)   → MERGE（应合并）
        - [0.95, 1.0]         → UNCERTAIN（无法区分）
        """
        split_boundary = threshold * 0.7
        if max_overlap < split_boundary:
            return DECISION_ACCEPT
        elif max_overlap < threshold:
            return DECISION_SPLIT
        elif max_overlap < 0.95:
            return DECISION_MERGE
        else:
            return DECISION_UNCERTAIN


def get_threshold_for_root(
    root_category: str, default: float = _GENERIC_THRESHOLD
) -> float:
    """获取指定根分类的重叠率阈值。

    不同根分类可能需要不同的严格程度：
    - network / llm_inference: 差异度大，更严格
    - data_parsing: 差异度小，更宽松

    Args:
        root_category: 根分类名称
        default: 未配置时的默认阈值

    Returns:
        该根分类对应的阈值
    """
    return _DEFAULT_THRESHOLD_BY_ROOT.get(root_category, default)
