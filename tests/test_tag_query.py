"""标签复合查询构建器单元测试 — AND/OR/NOT + 评估引擎。"""

from src.models import Tag
from src.tag_query import TagQueryBuilder, evaluate_query

# ══════════════════════════════════════════════════════════════════
# TagQueryBuilder 基础
# ══════════════════════════════════════════════════════════════════

class TestTagQueryBuilderBasics:
    def test_empty_query(self) -> None:
        q = TagQueryBuilder().build()
        assert q == {}

    def test_single_must(self) -> None:
        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        assert q == {"must": [{"tag": "状态_稳定"}], "must_not": [], "should": []}

    def test_must_and_must_not(self) -> None:
        q = (
            TagQueryBuilder()
            .must(Tag("状态_稳定"))
            .must_not(Tag("场景_本地计算"))
            .build()
        )
        assert len(q["must"]) == 1
        assert len(q["must_not"]) == 1
        assert q["must"][0]["tag"] == "状态_稳定"
        assert q["must_not"][0]["tag"] == "场景_本地计算"

    def test_should(self) -> None:
        q = TagQueryBuilder().should(Tag("代价_低消耗")).build()
        assert q["should"][0]["tag"] == "代价_低消耗"

    def test_all_tags(self) -> None:
        builder = (
            TagQueryBuilder()
            .must(Tag("状态_稳定"))
            .must_not(Tag("场景_本地计算"))
        )
        assert builder.all_tags == ["状态_稳定", "场景_本地计算"]


# ══════════════════════════════════════════════════════════════════
# 分组查询
# ══════════════════════════════════════════════════════════════════

class TestTagQueryBuilderGroups:
    def test_single_group_flat(self) -> None:
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .must_not(Tag("场景_本地计算"))
            .end_group()
            .build()
        )
        # 单组应扁平化
        assert "groups" not in q
        assert len(q["must"]) == 1
        assert len(q["must_not"]) == 1

    def test_multiple_groups(self) -> None:
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .end_group()
            .build()
        )
        assert "groups" in q
        assert len(q["groups"]) == 2

    def test_chained_groups(self) -> None:
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .must(Tag("场景_第三方依赖"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .must_not(Tag("场景_本地计算"))
            .end_group()
            .build()
        )
        assert "groups" in q
        assert len(q["groups"]) == 2
        assert q["groups"][0][0]["must"][0]["tag"] == "状态_稳定"

    def test_empty_groups_filtered(self) -> None:
        q = (
            TagQueryBuilder()
            .group()
            .end_group()
            .or_()
            .group()
            .end_group()
            .build()
        )
        assert q == {}


# ══════════════════════════════════════════════════════════════════
# 评估引擎
# ══════════════════════════════════════════════════════════════════

class TestEvaluateQuery:
    def test_empty_query_matches_all(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        assert evaluate_query(tags, {}) is True

    def test_must_match(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        assert evaluate_query(tags, q) is True

    def test_must_no_match(self) -> None:
        tags = {Tag("状态_实验性"), Tag("场景_第三方依赖")}
        q = TagQueryBuilder().must(Tag("状态_稳定")).build()
        assert evaluate_query(tags, q) is False

    def test_must_not_match(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        q = TagQueryBuilder().must_not(Tag("场景_本地计算")).build()
        assert evaluate_query(tags, q) is True

    def test_must_not_no_match(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_本地计算")}
        q = TagQueryBuilder().must_not(Tag("场景_本地计算")).build()
        assert evaluate_query(tags, q) is False

    def test_should_match(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        q = TagQueryBuilder().should(Tag("状态_稳定")).should(Tag("场景_本地计算")).build()
        assert evaluate_query(tags, q) is True

    def test_should_no_match(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        q = TagQueryBuilder().should(Tag("代价_低消耗")).should(Tag("代价_高延迟")).build()
        assert evaluate_query(tags, q) is False

    def test_must_and_must_not_combined(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_第三方依赖"), Tag("代价_低消耗")}
        q = (
            TagQueryBuilder()
            .must(Tag("状态_稳定"))
            .must_not(Tag("场景_本地计算"))
            .build()
        )
        assert evaluate_query(tags, q) is True

    def test_must_and_must_not_combined_fail(self) -> None:
        tags = {Tag("状态_稳定"), Tag("场景_本地计算")}
        q = (
            TagQueryBuilder()
            .must(Tag("状态_稳定"))
            .must_not(Tag("场景_本地计算"))
            .build()
        )
        assert evaluate_query(tags, q) is False

    def test_groups_or_match_first(self) -> None:
        tags = {Tag("状态_稳定")}
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .end_group()
            .build()
        )
        assert evaluate_query(tags, q) is True

    def test_groups_or_match_second(self) -> None:
        tags = {Tag("状态_实验性")}
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .end_group()
            .build()
        )
        assert evaluate_query(tags, q) is True

    def test_groups_or_match_neither(self) -> None:
        tags = {Tag("代价_低消耗")}
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .end_group()
            .build()
        )
        assert evaluate_query(tags, q) is False

    def test_complex_expression(self) -> None:
        """复杂表达式：(A AND B) OR (C AND NOT D)"""
        q = (
            TagQueryBuilder()
            .group()
            .must(Tag("状态_稳定"))
            .must(Tag("场景_第三方依赖"))
            .end_group()
            .or_()
            .group()
            .must(Tag("状态_实验性"))
            .must_not(Tag("场景_本地计算"))
            .end_group()
            .build()
        )

        # (A AND B) 命中
        tags_ab = {Tag("状态_稳定"), Tag("场景_第三方依赖")}
        assert evaluate_query(tags_ab, q) is True

        # (C AND NOT D) 命中
        tags_cd = {Tag("状态_实验性"), Tag("场景_第三方依赖")}
        assert evaluate_query(tags_cd, q) is True

        # 都不命中
        tags_none = {Tag("代价_低消耗")}
        assert evaluate_query(tags_none, q) is False

        # D 存在 → NOT 不通过
        tags_d = {Tag("状态_实验性"), Tag("场景_本地计算")}
        assert evaluate_query(tags_d, q) is False
