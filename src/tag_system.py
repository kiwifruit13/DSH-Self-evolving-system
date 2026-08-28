"""标签系统 — 遗传、变异、查询。

核心职责：
- 遗传：子节点继承父节点所有标签
- 变异：子节点可覆盖或移除遗传标签
- 查询：构建多标签 AND 查询条件

标签系统本身不持有状态，所有操作均为纯函数。
"""
from __future__ import annotations

from collections.abc import Iterable

from src.models import Tag, TagPrefix


class TagQuery:
    """构建多标签 AND 查询条件。

    使用示例：
        query = TagQuery() \\
            .require(Tag("状态_稳定")) \\
            .require(Tag("场景_第三方依赖"))

        # 转为标签集合，供 storage.query_routing_entries(tags=query.build()) 使用
        tags = query.build()
    """

    def __init__(self) -> None:
        self._required: list[Tag] = []

    def require(self, tag: Tag) -> TagQuery:
        """添加一个必须匹配的标签（AND 语义）。"""
        self._required.append(tag)
        return self

    def require_prefix(self, prefix: TagPrefix) -> TagQuery:
        """添加一个按前缀的查询：任意一个该前缀的标签即可匹配。

        注意：这是 OR 语义的前缀匹配，用于场景如"任意 状态_ 标签"。
        实际使用时会展开为所有已知该前缀的标签。
        """
        from src.models import _VALID_TAG_VALUES
        for body in _VALID_TAG_VALUES[prefix]:
            self._required.append(Tag(f"{prefix.value}{body}"))
        return self

    def build(self) -> set[Tag]:
        """返回查询所需的标签集合。"""
        return set(self._required)

    @property
    def tags(self) -> list[Tag]:
        """返回已添加的标签列表（保留添加顺序）。"""
        return list(self._required)


def inherit_tags(
    parent_tags: Iterable[Tag],
    overrides: Iterable[Tag] | None = None,
    removals: Iterable[Tag] | None = None,
) -> set[Tag]:
    """子节点标签继承。

    Args:
        parent_tags: 父节点的全部标签
        overrides: 子节点要覆盖的标签（替代父节点同前缀的标签）
        removals: 子节点要移除的标签

    示例：
        parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}

        # 继承 + 覆盖状态为"稳定"
        child = inherit_tags(parent, overrides={Tag("状态_稳定")})
        # -> {Tag("状态_稳定"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}

        # 继承 + 移除高延迟
        child = inherit_tags(parent, removals={Tag("代价_高延迟")})
        # -> {Tag("状态_实验性"), Tag("场景_第三方依赖")}
    """
    result = set(parent_tags)

    # 应用移除
    if removals:
        result -= set(removals)

    # 应用覆盖：移除同前缀的旧标签，加入新标签
    if overrides:
        for override_tag in overrides:
            prefix = override_tag.prefix
            # 移除同前缀的已有标签
            result = {t for t in result if t.prefix != prefix}
            result.add(override_tag)

    return result


def merge_tags(*tag_sets: Iterable[Tag]) -> set[Tag]:
    """合并多个标签集合，自动去重同前缀标签（取第一个出现的）。

    用于多来源标签合并时的优先级处理。
    """
    result: set[Tag] = set()
    seen_prefixes: set[TagPrefix] = set()

    for tag_set in tag_sets:
        for tag in tag_set:
            if tag.prefix in seen_prefixes:
                continue
            seen_prefixes.add(tag.prefix)
            result.add(tag)

    return result


def filter_tags_by_prefix(
    tags: Iterable[Tag], prefix: TagPrefix
) -> set[Tag]:
    """从标签集合中筛选出指定前缀的标签。"""
    return {t for t in tags if t.prefix == prefix}


def tags_to_strings(tags: Iterable[Tag]) -> list[str]:
    """将标签集合转为排序后的字符串列表。"""
    return sorted(t.value for t in tags)
