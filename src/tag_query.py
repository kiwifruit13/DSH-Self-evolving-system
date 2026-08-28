"""标签复合查询构建器 — 支持 AND / OR / NOT 组合逻辑。

扩展了简单的 TagQuery（仅 AND），提供完整的布尔表达式查询能力。

查询语法（链式调用）：
    query = TagQueryBuilder() \\
        .group() \\
        .must(Tag("状态_稳定")) \\
        .must_not(Tag("场景_本地计算")) \\
        .end_group() \\
        .or_() \\
        .group() \\
        .must(Tag("状态_实验性")) \\
        .must(Tag("场景_第三方依赖")) \\
        .end_group()

查询语义：
    (状态_稳定 AND NOT 场景_本地计算) OR (状态_实验性 AND 场景_第三方依赖)

内部表示：
    query.to_dict() → {
        "should": [
            {"must": [{"tag": "状态_稳定"}, {"must_not": [{"tag": "场景_本地计算"}]}]},
            {"must": [{"tag": "状态_实验性"}, {"tag": "场景_第三方依赖"}]}
        ]
    }

使用示例：
    builder = TagQueryBuilder()
    query = builder.must(Tag("状态_稳定")).must_not(Tag("场景_本地计算")).build()
    results = storage.query_routing_entries(match_query=query)
"""
from __future__ import annotations

from typing import Any

from src.models import Tag


class TagQueryBuilder:
    """标签复合查询构建器。

    支持语义：
    - must(tag): AND — 必须包含此标签
    - must_not(tag): NOT — 必须不包含此标签
    - should(tag): OR — 至少包含以下之一
    - group() / end_group(): 分组，支持 (A AND B) OR (C AND D)
    - or_(): OR 分组分隔符
    """

    def __init__(self) -> None:
        self._current_group: list[dict[str, Any]] = []
        self._groups: list[list[dict[str, Any]]] = [self._current_group]
        self._mode: str = "must"  # "must" | "should" | "must_not"

    def must(self, tag: Tag) -> TagQueryBuilder:
        """必须包含此标签（AND）。"""
        self._current_group.append({"must": [{"tag": tag.value}]})
        return self

    def must_not(self, tag: Tag) -> TagQueryBuilder:
        """必须不包含此标签（NOT）。"""
        self._current_group.append({"must_not": [{"tag": tag.value}]})
        return self

    def should(self, tag: Tag) -> TagQueryBuilder:
        """至少包含以下之一（OR，同一组内）。"""
        self._current_group.append({"should": [{"tag": tag.value}]})
        return self

    def group(self) -> TagQueryBuilder:
        """开始一个新的 AND 组。"""
        self._current_group = []
        self._groups.append(self._current_group)
        return self

    def end_group(self) -> TagQueryBuilder:
        """结束当前组；后续条件进入一个新的、被追踪的组。

        修复：原先直接重置 _current_group 而未将其纳入 _groups，
        导致 end_group()/or_() 之后追加的 must() 等条件被静默丢弃。
        """
        self._current_group = []
        self._groups.append(self._current_group)
        return self

    def or_(self) -> TagQueryBuilder:
        """OR 分组分隔符：结束当前组并开启新的 OR 组。"""
        return self.end_group()

    def build(self) -> dict[str, Any]:
        """构建查询表达式。

        返回格式：
            {
                "must": [...],          # 顶层 AND 条件
                "must_not": [...],       # 顶层 NOT 条件
                "should": [...],         # 顶层 OR 条件
                "groups": [[...]],       # 分组（每组内部为 AND，组间为 OR）
            }
        """
        # 过滤空组
        non_empty_groups = [g for g in self._groups if g]

        if not non_empty_groups:
            return {}

        if len(non_empty_groups) == 1:
            # 单组：直接展开为 must/must_not/should
            flat: dict[str, list[dict[str, Any]]] = {"must": [], "must_not": [], "should": []}
            for item in non_empty_groups[0]:
                for key, val in item.items():
                    flat.setdefault(key, []).extend(val)
            return flat

        # 多组：返回分组结构
        return {"groups": non_empty_groups}

    def to_dict(self) -> dict[str, Any]:
        """同 build()，返回可序列化的查询表达式。"""
        return self.build()

    @property
    def all_tags(self) -> list[str]:
        """返回查询中出现的所有标签字符串（去重，保留顺序）。"""
        seen: list[str] = []
        for group in self._groups:
            for item in group:
                for clause in item.values():
                    for c in clause:
                        if isinstance(c, dict) and "tag" in c and c["tag"] not in seen:
                            seen.append(c["tag"])
        return seen


def evaluate_query(
    entry_tags: set[Tag],
    query: dict[str, Any],
) -> bool:
    """评估一个路由表条目的标签是否匹配查询表达式。

    这是一个纯函数，不依赖存储。

    Args:
        entry_tags: 路由表条目的标签集合
        query: TagQueryBuilder.build() 返回的查询表达式

    Returns:
        True 表示匹配。
    """
    tag_values = {t.value for t in entry_tags}

    if not query:
        return True

    if "groups" in query:
        # 多组：组间 OR，组内 AND
        return any(
            _evaluate_group(group, tag_values)
            for group in query["groups"]
        )

    # 单组：展开为 must / must_not / should
    must_tags = {c["tag"] for c in query.get("must", [])}
    must_not_tags = {c["tag"] for c in query.get("must_not", [])}
    should_tags = {c["tag"] for c in query.get("should", [])}

    # must: 全部匹配
    if must_tags and not must_tags.issubset(tag_values):
        return False

    # must_not: 全部不匹配
    if must_not_tags and must_not_tags & tag_values:
        return False

    # should: 至少匹配一个
    if should_tags and not (should_tags & tag_values):
        return False

    return True


def _evaluate_group(
    group: list[dict[str, Any]],
    tag_values: set[str],
) -> bool:
    """评估单个组的标签匹配（组内 AND 语义）。

    组内每个条件独立评估，全部通过才返回 True。
    """
    for item in group:
        for clause_type, clauses in item.items():
            for clause in clauses:
                tag_val = clause.get("tag", "")
                if clause_type == "must" and tag_val not in tag_values:
                    return False
                if clause_type == "must_not" and tag_val in tag_values:
                    return False
                # should 在组内作为 OR 处理
                if clause_type == "should":
                    # 在组内 should 与其他条件 AND，与同组其他 should OR
                    pass
    # should 在组内的处理
    should_tags = set()
    for item in group:
        for clause_type, clauses in item.items():
            if clause_type == "should":
                for c in clauses:
                    should_tags.add(c.get("tag", ""))
    if should_tags and not (should_tags & tag_values):
        return False

    return True
