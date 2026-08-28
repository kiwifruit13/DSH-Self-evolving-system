"""路由表节点质量评分器 — Skill-Judge D1 知识增量维度。

核心公式（来自 skill-judge/SKILL.md）：
    知识增量 = E / (E + A + R)
    E = Expert 知识（具体策略、决策树、反模式、边界案例）
    A = Activation 知识（通用提醒、已知概念标注）
    R = Redundant 知识（"处理X"、"修复X"、"检查X"等空话）

设计理念：
    - 一个路由表节点如果全是泛泛而谈的"聚焦X修复"，知识增量 = 0，应被剪枝
    - 一个节点包含具体错误码、策略描述、反模式、决策树，知识增量 >= 0.5，应被保留
    - 所有模式均基于正则匹配，无需外部依赖

使用示例：
    scorer = NodeQualityScorer()
    score = scorer.score(entry)
    if score.quality_level == "redundant":
        # 加入剪枝候选
"""
from __future__ import annotations

import re
from re import Pattern

from src.models import NodeQualityScore, RoutingTableEntry


class NodeQualityScorer:
    """路由表节点质量评分器（Skill-Judge D1 知识增量）。

    检测三类知识信号并计算知识增量比。
    所有模式均为常量，不依赖外部输入。
    """

    # ── 冗余模式（Negative Signals）─────────────────────────────────
    # 自动生成的空话特征，知识增量 = 0
    REDUNDANT_PATTERNS: list[Pattern[str]] = [
        re.compile(r"^仅处理"),
        re.compile(r"^聚焦.*修复$"),
        re.compile(r"^待优化"),
        re.compile(r"^基于反馈举证"),
        re.compile(r"^自动.*生成"),
        re.compile(r"不处理其他"),
        re.compile(r"^仅检查"),
        re.compile(r"^待改进"),
    ]

    # ── 专家模式（Positive Signals — Expert）───────────────────────
    # 高质量知识增量特征
    EXPERT_PATTERNS: list[Pattern[str]] = [
        re.compile(r"\d{3}"),  # 具体错误码（429, 500 等）
        re.compile(r"指数退避|backoff", re.IGNORECASE),
        re.compile(r"(禁止|永不|不要|NEVER|不要|必须)", re.IGNORECASE),
        re.compile(r"(如果|除非|当.*时|边界|否则)", re.IGNORECASE),
        re.compile(r"(fallback|回退|降级|兜底)", re.IGNORECASE),
        re.compile(r"(retry|max_retry|重试.*次)", re.IGNORECASE),
        re.compile(r"(timeout|超时.*秒|deadline)", re.IGNORECASE),
        re.compile(r"(circuit.?breaker|熔断)", re.IGNORECASE),
        re.compile(r"(限流|rate.?limit|throttl)", re.IGNORECASE),
    ]

    # ── 激活模式（Activation Signals）───────────────────────────────
    # 通用提醒——模型可能知道但不会主动用
    ACTIVATION_PATTERNS: list[Pattern[str]] = [
        re.compile(r"(处理|修复|检查|分析)", re.IGNORECASE),
        re.compile(r"(建议|推荐|考虑)"),
        re.compile(r"(注意|警告|提醒)"),
    ]

    # ── 质量等级阈值 ────────────────────────────────────────────────

    LEVEL_EXPERT = 0.5
    LEVEL_ADEQUATE = 0.3
    LEVEL_POOR = 0.1

    def score(self, entry: RoutingTableEntry) -> NodeQualityScore:
        """对路由表节点执行 D1 知识增量评分。

        Args:
            entry: 路由表条目

        Returns:
            NodeQualityScore 质量评分结果
        """
        # 收集待检测的文本
        texts = self._collect_texts(entry)

        expert_signals: list[str] = []
        activation_signals: list[str] = []
        redundant_signals: list[str] = []

        for text in texts:
            for pat in self.REDUNDANT_PATTERNS:
                if pat.search(text):
                    redundant_signals.append(f"redundant:{pat.pattern}")
            for pat in self.EXPERT_PATTERNS:
                if pat.search(text):
                    expert_signals.append(f"expert:{pat.pattern}")
            for pat in self.ACTIVATION_PATTERNS:
                if pat.search(text):
                    activation_signals.append(f"activation:{pat.pattern}")

        # 去重信号（同一模式在同一文本中多次命中只计一次）
        expert_signals = list(dict.fromkeys(expert_signals))
        activation_signals = list(dict.fromkeys(activation_signals))
        redundant_signals = list(dict.fromkeys(redundant_signals))

        expert_count = len(expert_signals)
        activation_count = len(activation_signals)
        redundant_count = len(redundant_signals)

        # 知识增量比
        denominator = expert_count + activation_count + redundant_count
        delta = expert_count / denominator if denominator > 0 else 0.0

        # 质量等级
        if delta >= self.LEVEL_EXPERT:
            level = "expert"
        elif delta >= self.LEVEL_ADEQUATE:
            level = "adequate"
        elif delta >= self.LEVEL_POOR:
            level = "poor"
        else:
            level = "redundant"

        all_signals = (
            expert_signals + activation_signals + redundant_signals
        )

        return NodeQualityScore(
            category_id=entry.category_id,
            expert_score=round(expert_count / max(1, denominator), 4),
            activation_score=round(activation_count / max(1, denominator), 4),
            redundant_score=round(redundant_count / max(1, denominator), 4),
            knowledge_delta=round(delta, 4),
            quality_level=level,
            signals=all_signals,
        )

    def score_batch(
        self, entries: list[RoutingTableEntry]
    ) -> list[NodeQualityScore]:
        """批量评分。"""
        return [self.score(entry) for entry in entries]

    def is_low_quality(
        self, score: NodeQualityScore, delta_min: float = 0.1
    ) -> bool:
        """判断节点是否为低质量（知识增量低于门槛）。"""
        return score.knowledge_delta < delta_min

    # ── 内部辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _collect_texts(entry: RoutingTableEntry) -> list[str]:
        """从 RoutingTableEntry 的 local_map 提取待评分文本。"""
        lm = entry.local_map
        texts = []
        if lm.focus_description:
            texts.append(lm.focus_description)
        if lm.boundary_rules:
            texts.append(lm.boundary_rules)
        if lm.logic_signature:
            texts.append(lm.logic_signature)
        # maintenance_log 中的 reason 也可能包含知识信息
        for log in lm.maintenance_log:
            if log.reason:
                texts.append(log.reason)
        return texts
