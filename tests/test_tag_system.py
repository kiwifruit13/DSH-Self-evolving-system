"""标签系统单元测试 — 遗传 / 变异 / 查询 / 合并。"""

from src.models import Tag, TagPrefix
from src.tag_system import (
    TagQuery,
    filter_tags_by_prefix,
    inherit_tags,
    merge_tags,
    tags_to_strings,
)

# ══════════════════════════════════════════════════════════════════
# 遗传与变异
# ══════════════════════════════════════════════════════════════════

class TestInheritTags:
    def test_simple_inheritance(self) -> None:
        parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        child = inherit_tags(parent)
        assert child == parent

    def test_override(self) -> None:
        parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        child = inherit_tags(parent, overrides={Tag("状态_稳定")})
        assert Tag("状态_稳定") in child
        assert Tag("状态_实验性") not in child
        assert Tag("代价_高延迟") in child

    def test_removal(self) -> None:
        parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        child = inherit_tags(parent, removals={Tag("代价_高延迟")})
        assert Tag("代价_高延迟") not in child
        assert Tag("状态_实验性") in child

    def test_override_and_removal(self) -> None:
        parent = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        child = inherit_tags(
            parent,
            overrides={Tag("状态_稳定")},
            removals={Tag("代价_高延迟")},
        )
        assert Tag("状态_稳定") in child
        assert Tag("代价_高延迟") not in child
        assert Tag("场景_第三方依赖") in child

    def test_multiple_override_same_prefix(self) -> None:
        """同一前缀多次覆盖：最后一个胜出。"""
        parent = {Tag("状态_实验性"), Tag("代价_高延迟")}
        child = inherit_tags(
            parent,
            overrides={Tag("状态_稳定"), Tag("状态_废弃")},
        )
        # 同一前缀只能有一个
        assert Tag("状态_实验性") not in child
        assert Tag("状态_稳定") in child or Tag("状态_废弃") in child
        assert sum(1 for t in child if t.prefix == TagPrefix.STATUS) == 1


# ══════════════════════════════════════════════════════════════════
# 合并
# ══════════════════════════════════════════════════════════════════

class TestMergeTags:
    def test_merge_two_sets(self) -> None:
        a = {Tag("状态_实验性"), Tag("代价_高延迟")}
        b = {Tag("场景_第三方依赖"), Tag("状态_稳定")}
        merged = merge_tags(a, b)
        assert len(merged) == 3
        # 同前缀取第一个出现的
        assert Tag("状态_实验性") in merged
        assert Tag("状态_稳定") not in merged

    def test_merge_empty(self) -> None:
        assert merge_tags({}) == set()

    def test_merge_single(self) -> None:
        tags = {Tag("状态_稳定"), Tag("代价_低消耗")}
        assert merge_tags(tags) == tags


# ══════════════════════════════════════════════════════════════════
# 前缀过滤
# ══════════════════════════════════════════════════════════════════

class TestFilterTagsByPrefix:
    def test_filter_status(self) -> None:
        tags = {Tag("状态_实验性"), Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        result = filter_tags_by_prefix(tags, TagPrefix.STATUS)
        assert result == {Tag("状态_实验性")}

    def test_filter_empty(self) -> None:
        tags = {Tag("代价_高延迟"), Tag("场景_第三方依赖")}
        result = filter_tags_by_prefix(tags, TagPrefix.STATUS)
        assert result == set()


# ══════════════════════════════════════════════════════════════════
# TagQuery
# ══════════════════════════════════════════════════════════════════

class TestTagQuery:
    def test_build_empty(self) -> None:
        query = TagQuery()
        assert query.build() == set()

    def test_build_with_requires(self) -> None:
        query = TagQuery().require(Tag("状态_稳定")).require(Tag("场景_第三方依赖"))
        tags = query.build()
        assert Tag("状态_稳定") in tags
        assert Tag("场景_第三方依赖") in tags
        assert len(tags) == 2

    def test_build_with_prefix(self) -> None:
        query = TagQuery().require_prefix(TagPrefix.STATUS)
        tags = query.build()
        assert Tag("状态_稳定") in tags
        assert Tag("状态_实验性") in tags
        assert Tag("状态_废弃") in tags

    def test_tags_property_preserves_order(self) -> None:
        query = TagQuery().require(Tag("状态_稳定")).require(Tag("代价_低消耗"))
        assert query.tags == [Tag("状态_稳定"), Tag("代价_低消耗")]


# ══════════════════════════════════════════════════════════════════
# 字符串转换
# ══════════════════════════════════════════════════════════════════

class TestTagsToStrings:
    def test_sorted_output(self) -> None:
        tags = {Tag("代价_低消耗"), Tag("状态_稳定"), Tag("场景_第三方依赖")}
        strings = tags_to_strings(tags)
        assert strings == sorted(strings)
        assert "代价_低消耗" in strings
        assert "状态_稳定" in strings
        assert "场景_第三方依赖" in strings
