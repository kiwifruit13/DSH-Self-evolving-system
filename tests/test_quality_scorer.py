"""NodeQualityScorer 单元测试 — Skill-Judge D1 知识增量评分。

覆盖四类节点：expert / adequate / poor / redundant
"""
from __future__ import annotations

from src.models import LocalMindMap, NodeQualityScore, RoutingTableEntry
from src.quality_scorer import NodeQualityScorer


def _make_entry(
    category_id: str,
    focus: str,
    boundary: str,
    logic: str = "",
) -> RoutingTableEntry:
    lm = LocalMindMap(
        node_id=category_id,
        parent_path=f"root.{category_id.split('.')[0]}",
        focus_description=focus,
        boundary_rules=boundary,
        logic_signature=logic,
    )
    return RoutingTableEntry(
        category_id=category_id,
        stats={"freq": 1.0, "impact": 0.5, "trend": 0.0, "recover_cost": 1.0},
        local_map=lm,
        tags=set(),
    )


def test_expert_node() -> None:
    """专家节点：具体错误码 + 策略 + 反模式 + 决策树 → knowledge_delta >= 0.5"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.http_429.retry",
        focus="处理 HTTP 429 限流错误",
        boundary=(
            "禁止使用 HTTP 连接重试；指数退避 2^n ms；"
            "当状态码 429 时降级到 5xx fallback；超时 30s"
        ),
    )
    score = scorer.score(entry)
    assert score.quality_level == "expert", (
        f"期望 expert，实际 {score.quality_level}，delta={score.knowledge_delta}"
    )
    assert score.knowledge_delta >= 0.5, (
        f"delta {score.knowledge_delta} 应 >= 0.5"
    )
    assert score.expert_score > score.redundant_score


def test_adequate_node() -> None:
    """充足节点：有知识增量但混有冗余 → adequate"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.timeout.retry",
        focus="处理网络超时",
        boundary="使用重试机制处理 HTTP 超时；建议设置 timeout 10s",
    )
    score = scorer.score(entry)
    # "timeout" 触发 expert，"处理"、"建议" 触发 activation
    assert score.expert_score > 0
    assert score.activation_score > 0
    assert score.quality_level in ("adequate", "poor"), (
        f"期望 adequate 或 poor，实际 {score.quality_level}"
    )


def test_poor_node() -> None:
    """低质量节点：仅有泛泛描述 + 少量信号 → poor/redundant"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.generic",
        focus="聚焦 network 错误修复",
        boundary="仅处理网络相关问题，不处理其他错误类型",
    )
    score = scorer.score(entry)
    assert score.quality_level in ("poor", "redundant"), (
        f"期望 poor 或 redundant，实际 {score.quality_level}"
    )


def test_redundant_node() -> None:
    """冗余节点：全为自动生成空话 → redundant"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.auto",
        focus="聚焦 HTTP 修复",
        boundary="基于反馈举证自动生成（置信度 0.8）",
        logic="待优化：基于主代理反馈举证生成",
    )
    score = scorer.score(entry)
    assert score.quality_level == "redundant", (
        f"期望 redundant，实际 {score.quality_level}"
    )
    assert score.knowledge_delta < 0.1
    assert score.redundant_score > 0


def test_empty_boundary() -> None:
    """空边界节点：无有效文本 → delta = 0.0"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.empty",
        focus="",
        boundary="",
        logic="",
    )
    score = scorer.score(entry)
    assert score.knowledge_delta == 0.0
    assert score.quality_level == "redundant"


def test_mixed_signals() -> None:
    """混合信号：同时含专家和冗余特征 → adequate/expert"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.mixed",
        focus="聚焦修复",
        boundary=(
            "仅处理 HTTP 超时；"
            "禁止使用连接重试；指数退避 2^n ms；"
            "如果状态码 503 则降级到 fallback"
        ),
    )
    score = scorer.score(entry)
    assert score.knowledge_delta > 0.0
    assert score.quality_level in ("adequate", "expert"), (
        f"混合信号应达 adequate+，实际 {score.quality_level}"
    )
    assert any("expert" in s for s in score.signals)
    assert any("redundant" in s for s in score.signals)


def test_is_low_quality() -> None:
    """is_low_quality() 判断"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.low",
        focus="聚焦修复",
        boundary="仅处理网络问题，不处理其他",
    )
    score = scorer.score(entry)
    assert scorer.is_low_quality(score, delta_min=0.1)
    assert not scorer.is_low_quality(score, delta_min=0.0)


def test_batch_scoring() -> None:
    """批量评分"""
    scorer = NodeQualityScorer()
    entries = [
        _make_entry("network.a", "聚焦", "仅处理"),
        _make_entry("network.b", "具体策略", "禁止重试；指数退避"),
        _make_entry("network.c", "空话", "自动生成的修复"),
    ]
    scores = scorer.score_batch(entries)
    assert len(scores) == 3
    assert scores[0].category_id == "network.a"
    assert scores[1].quality_level in ("expert", "adequate")


def test_serialization() -> None:
    """NodeQualityScore to_dict / from_dict 对称性"""
    scorer = NodeQualityScorer()
    entry = _make_entry(
        "network.test",
        focus="具体处理 HTTP 429 限流",
        boundary="禁止重试；指数退避；circuit breaker",
    )
    score = scorer.score(entry)
    d = score.to_dict()
    restored = NodeQualityScore.from_dict(d)
    assert restored.category_id == score.category_id
    assert restored.knowledge_delta == score.knowledge_delta
    assert restored.quality_level == score.quality_level
