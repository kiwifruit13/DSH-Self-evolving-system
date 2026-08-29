"""四维排序计算器 — 路由表节点优先级评估。

公式（来自 AGENTS_01.md）：

    综合优先级 = Freq × 0.25 + Impact × 0.35 + Trend × 0.20 + Recover_Cost × 0.20
    衰减因子 = 2^(-days_since_last_seen / 7)
    最终得分 = 综合优先级 × 衰减因子

权重含义：
- Freq (25%)：过去 N 天的命中频率，越高越重要
- Impact (35%)：修复后的恢复成功率，影响最大
- Trend (20%)：近期增长趋势，防止漏掉"即将爆发"的问题
- Recover_Cost (20%)：恢复代价，代价越低越优先（反向）

Step 43：四维相关性说明
- Freq 与 Trend 存在内在相关（频率高通常伴随趋势增长），
  但两者度量不同维度：Freq 是历史总量，Trend 是变化率。
  保留双维度可区分"高频稳定"与"中频增长"两类问题。
- Impact 与 Recover_Cost 独立：高影响可能伴随低恢复代价（简单问题），
  也可能伴随高代价（复杂问题），两者不可相互替代。

使用示例：
    calc = ScoreCalculator()
    score = calc.compute_final_score(
        stats={"freq": 50, "impact": 0.85, "trend": 0.3, "recover_cost": 2},
        days_since_last_seen=3,
    )

进阶功能：
- 数据驱动归一化（Step 78）：calibrate() 从实际数据学习 freq_max/cost_max
- 权重反馈回路（Step 77）：reweight() 根据数据方差自适应调整权重
- 数据量感知（Step 80）：当 sample_count < threshold 时自动降低影响
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any

from src.models import RoutingTableEntry


@dataclass
class ScoreConfig:
    """排序计算器的可调参数。"""
    freq_weight: float = 0.25
    impact_weight: float = 0.35
    trend_weight: float = 0.20
    cost_weight: float = 0.20

    half_life_days: float = 7.0
    """时间衰减半衰期（天）：得分每过此天数减半。"""

    freq_window_days: int = 30
    """频率统计窗口（天）。"""

    # 归一化参数（Step 78：数据驱动后可由 calibrate() 更新）
    freq_max: float = 1000.0
    """频率归一化的参考最大值。"""
    cost_max: float = 10.0
    """恢复代价归一化的参考最大值（次/分钟）。"""

    # Step 80：数据量感知
    sample_count_threshold: int = 5
    """数据量感知阈值：sample_count 低于此值时降低影响权重。"""
    sample_confidence_floor: float = 0.3
    """低数据量时的影响得分下限（避免极端归零）。"""

    # Step 77：权重反馈
    reweight_alpha: float = 0.1
    """权重反馈学习率（0=不更新，1=完全覆盖为方差权重）。"""


@dataclass
class ScoreBreakdown:
    """单节点得分明细，用于调试和日志。"""
    category_id: str
    freq_normalized: float
    impact_normalized: float
    trend_normalized: float
    cost_normalized: float
    priority: float
    decay_factor: float
    final_score: float
    days_since_last_seen: float
    # Step 80：数据量感知
    sample_count: int = 0
    impact_confidence: float = 1.0
    sample_penalty: float = 0.0
    # Step 45：排序置信度
    confidence: float = 1.0
    """排序置信度：1/(综合标准差 + ε)。高置信度表示各维度得分稳定一致。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "freq_normalized": round(self.freq_normalized, 4),
            "impact_normalized": round(self.impact_normalized, 4),
            "trend_normalized": round(self.trend_normalized, 4),
            "cost_normalized": round(self.cost_normalized, 4),
            "priority": round(self.priority, 4),
            "decay_factor": round(self.decay_factor, 4),
            "final_score": round(self.final_score, 4),
            "days_since_last_seen": self.days_since_last_seen,
            "sample_count": self.sample_count,
            "impact_confidence": round(self.impact_confidence, 4),
            "sample_penalty": round(self.sample_penalty, 4),
            # BUG-18 修复：纳入 confidence 字段
            "confidence": round(self.confidence, 4),
        }


class ScoreCalculator:
    """四维排序计算器。

    所有 normalize_* 方法均为纯函数，便于测试和调试。
    """

    def __init__(self, config: ScoreConfig | None = None) -> None:
        self.config = config or ScoreConfig()

    # ═══════════════════════════════════════════════════════════════
    # 归一化函数
    # ═══════════════════════════════════════════════════════════════

    def normalize_freq(self, freq: float) -> float:
        """频率归一化：线性映射到 [0, 1]。

        freq=0      → 0.0
        freq<=max   → freq / max
        freq>max    → 1.0
        """
        if freq <= 0:
            return 0.0
        return min(1.0, freq / self.config.freq_max)

    def normalize_impact(self, impact: float) -> float:
        """Impact 已在 [0, 1] 范围内，钳制即可。"""
        return max(0.0, min(1.0, impact))

    def normalize_trend(self, trend: float) -> float:
        """趋势从 [-1, 1] 映射到 [0, 1]。

        trend=-1 → 0.0（急剧下降）
        trend=0  → 0.5（平稳）
        trend=+1 → 1.0（急剧上升）
        """
        return max(0.0, min(1.0, (trend + 1.0) / 2.0))

    def normalize_cost(self, cost: float) -> float:
        """恢复代价归一化：代价越低得分越高（反向 sigmoid）。

        cost=0   → 1.0（零代价，最优）
        cost=max → 0.5
        cost→∞   → 0.0
        """
        if cost <= 0:
            return 1.0
        ratio = cost / self.config.cost_max
        return 1.0 / (1.0 + ratio)

    # ═══════════════════════════════════════════════════════════════
    # 核心计算
    # ═══════════════════════════════════════════════════════════════

    def compute_priority(self, stats: dict[str, float]) -> float:
        """计算四维综合优先级（不含时间衰减）。

        Args:
            stats: 包含 freq / impact / trend / recover_cost 的字典。

        Returns:
            [0, 1] 范围内的综合优先级。
        """
        freq_n = self.normalize_freq(stats.get("freq", 0.0))
        impact_n = self.normalize_impact(stats.get("impact", 0.0))
        trend_n = self.normalize_trend(stats.get("trend", 0.0))
        cost_n = self.normalize_cost(stats.get("recover_cost", 0.0))

        return (
            freq_n * self.config.freq_weight
            + impact_n * self.config.impact_weight
            + trend_n * self.config.trend_weight
            + cost_n * self.config.cost_weight
        )

    def decay_factor(self, days_since_last_seen: float) -> float:
        """时间衰减因子。

        days=0     → 1.0
        days=7     → 0.5
        days=14    → 0.25
        days→∞     → 0.0
        """
        if days_since_last_seen <= 0:
            return 1.0
        return float(2.0 ** (-days_since_last_seen / self.config.half_life_days))

    # ═══════════════════════════════════════════════════════════════
    # Step 45：排序置信度计算
    # ═══════════════════════════════════════════════════════════════

    def _score_std(
        self,
        freq_n: float,
        impact_n: float,
        trend_n: float,
        cost_n: float,
    ) -> float:
        """计算四维归一化得分的标准差。

        confidence = 1 / (std + ε)。std 越大（各维度分歧越大）置信度越低。
        """
        values: list[float] = [freq_n, impact_n, trend_n, cost_n]
        mean = float(sum(values)) / len(values)
        variance = float(sum((v - mean) ** 2 for v in values)) / len(values)
        return float(variance ** 0.5)

    def compute_final_score(
        self,
        stats: dict[str, float],
        days_since_last_seen: float = 0.0,
    ) -> float:
        """计算最终得分（含时间衰减）。

        Returns:
            [0, 1] 范围内的最终得分。
        """
        priority = self.compute_priority(stats)
        decay = self.decay_factor(days_since_last_seen)
        return priority * decay

    def score_with_breakdown(
        self,
        entry: RoutingTableEntry,
        days_since_last_seen: float | None = None,
    ) -> ScoreBreakdown:
        """计算单节点得分并返回完整明细。

        Step 79：自动从 entry.stats["last_seen"] 读取时间戳计算衰减。
        Step 80：从 entry.stats["sample_count"] 读取样本数调整影响。
        Step 78：使用 calibrate() 学习后的归一化参数。

        Args:
            entry: 待评分的路由表条目
            days_since_last_seen: 显式天数（覆盖自动计算），None 时自动读取 last_seen

        Returns:
            ScoreBreakdown 完整明细
        """
        # Step 79：自动读取 last_seen（优先）或回退到参数
        if days_since_last_seen is None:
            days_since_last_seen = self._compute_days_from_entry(entry)

        # Step 78：使用数据驱动的归一化参数
        freq_n = self.normalize_freq(float(entry.stats.get("freq", 0.0)))
        impact_raw = float(entry.stats.get("impact", 0.0))
        trend_n = self.normalize_trend(float(entry.stats.get("trend", 0.0)))
        cost_n = self.normalize_cost(float(entry.stats.get("recover_cost", 0.0)))

        # Step 80：数据量感知调整影响得分
        sample_count = int(entry.stats.get("sample_count", 0))
        impact_n, impact_confidence, sample_penalty = self.sample_aware_impact(
            impact_raw, sample_count
        )

        priority = (
            freq_n * self.config.freq_weight
            + impact_n * self.config.impact_weight
            + trend_n * self.config.trend_weight
            + cost_n * self.config.cost_weight
        )
        decay = self.decay_factor(days_since_last_seen)
        final_score = priority * decay

        # Step 45：排序置信度 = 1 / (四维得分标准差 + ε)
        std = self._score_std(freq_n, impact_n, trend_n, cost_n)
        confidence = 1.0 / (std + 1e-10)

        return ScoreBreakdown(
            category_id=entry.category_id,
            freq_normalized=freq_n,
            impact_normalized=impact_n,
            trend_normalized=trend_n,
            cost_normalized=cost_n,
            priority=priority,
            decay_factor=decay,
            final_score=final_score,
            days_since_last_seen=days_since_last_seen,
            sample_count=sample_count,
            impact_confidence=impact_confidence,
            sample_penalty=sample_penalty,
            confidence=confidence,
        )

    def _compute_days_from_entry(self, entry: RoutingTableEntry) -> float:
        """从 entry.stats["last_seen"] 计算天数（Step 79）。"""
        last_seen_str = entry.stats.get("last_seen", "")
        if not last_seen_str or not isinstance(last_seen_str, str):
            return 0.0
        try:
            last_seen = datetime.fromisoformat(last_seen_str)
            # BUG-05 修复：tz-naive 时间戳统一视为 UTC
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - last_seen
            return max(0.0, delta.total_seconds() / 86400)
        except (ValueError, TypeError):
            return 0.0

    # ═══════════════════════════════════════════════════════════════
    # Step 78：数据驱动归一化
    # ═══════════════════════════════════════════════════════════════

    def calibrate(self, stats_list: list[dict[str, Any]]) -> dict[str, float]:
        """从实际数据统计归一化参考值（freq_max / cost_max）。

        策略：使用 P95（95 百分位）作为参考最大值，避免极端值影响归一化。

        Args:
            stats_list: 历史或全量路由表条目的 stats 列表

        Returns:
            更新后的参考值 {"freq_max": float, "cost_max": float}
        """
        if not stats_list:
            return {"freq_max": self.config.freq_max, "cost_max": self.config.cost_max}

        freqs = sorted([float(s.get("freq", 0)) for s in stats_list if "freq" in s])
        costs = sorted([float(s.get("recover_cost", 0)) for s in stats_list if "recover_cost" in s])

        def percentile(data: list[float], p: float) -> float:
            """BUG-21 修复：使用线性插值计算百分位，小样本不退化为 max。"""
            if not data:
                return 0.0
            if len(data) == 1:
                return data[0]
            # 线性插值：idx = p/100 * (n-1)
            idx = p / 100.0 * (len(data) - 1)
            lower = int(idx)
            upper = min(lower + 1, len(data) - 1)
            frac = idx - lower
            return data[lower] * (1 - frac) + data[upper] * frac

        new_freq_max = percentile(freqs, 95)
        new_cost_max = percentile(costs, 95)

        # BUG-21 修复：不再限制只能单调增大，允许路由表萎缩后参考值回落
        # 保持最小值下限，防止数据稀疏时参考值过小
        self.config.freq_max = max(new_freq_max, 100.0)
        self.config.cost_max = max(new_cost_max, 1.0)

        return {"freq_max": self.config.freq_max, "cost_max": self.config.cost_max}

    # ═══════════════════════════════════════════════════════════════
    # Step 77：权重反馈回路
    # ═══════════════════════════════════════════════════════════════

    def reweight(self, stats_list: list[dict[str, Any]]) -> dict[str, float]:
        """根据数据方差自适应调整四维权重。

        策略：计算各维度的归一化方差，方差越大说明该维度区分度越高，
        权重应相对增加。使用 reweight_alpha 做软更新（不直接覆盖）。

        Args:
            stats_list: 历史或全量路由表条目的 stats 列表

        Returns:
            更新后的权重 {"freq": float, "impact": float, "trend": float, "cost": float}
        """
        if len(stats_list) < 2:
            return {
                "freq": self.config.freq_weight,
                "impact": self.config.impact_weight,
                "trend": self.config.trend_weight,
                "cost": self.config.cost_weight,
            }

        # 计算各维度归一化后的方差
        freq_vals = [self.normalize_freq(float(s.get("freq", 0))) for s in stats_list]
        impact_vals = [self.normalize_impact(float(s.get("impact", 0))) for s in stats_list]
        trend_vals = [self.normalize_trend(float(s.get("trend", 0))) for s in stats_list]
        cost_vals = [self.normalize_cost(float(s.get("recover_cost", 0))) for s in stats_list]

        var_freq = pstdev(freq_vals) ** 2
        var_impact = pstdev(impact_vals) ** 2
        var_trend = pstdev(trend_vals) ** 2
        var_cost = pstdev(cost_vals) ** 2
        total_var = var_freq + var_impact + var_trend + var_cost

        if total_var < 1e-12:
            # 方差全为 0，保持原权重
            return {
                "freq": self.config.freq_weight,
                "impact": self.config.impact_weight,
                "trend": self.config.trend_weight,
                "cost": self.config.cost_weight,
            }

        # 目标权重 = 各维度方差占比
        target = {
            "freq": var_freq / total_var,
            "impact": var_impact / total_var,
            "trend": var_trend / total_var,
            "cost": var_cost / total_var,
        }

        # 软更新：α * target + (1 - α) * current
        alpha = self.config.reweight_alpha
        self.config.freq_weight = (1 - alpha) * self.config.freq_weight + alpha * target["freq"]
        self.config.impact_weight = (1 - alpha) * self.config.impact_weight + alpha * target["impact"]
        self.config.trend_weight = (1 - alpha) * self.config.trend_weight + alpha * target["trend"]
        self.config.cost_weight = (1 - alpha) * self.config.cost_weight + alpha * target["cost"]

        return {
            "freq": self.config.freq_weight,
            "impact": self.config.impact_weight,
            "trend": self.config.trend_weight,
            "cost": self.config.cost_weight,
        }

    # ═══════════════════════════════════════════════════════════════
    # Step 80：数据量感知
    # ═══════════════════════════════════════════════════════════════

    def sample_aware_impact(
        self,
        impact: float,
        sample_count: int,
    ) -> tuple[float, float, float]:
        """数据量感知的影响得分调整。

        当 sample_count < sample_count_threshold 时，降低影响权重的置信度。
        策略：向 impact * confidence_floor 收缩（而非向固定值收缩），
        确保低 impact 节点不会因低样本数被膨胀。

        公式：
            confidence = min(1.0, sample_count / threshold)
            adjusted = impact * (confidence + (1 - confidence) * confidence_floor)

        Args:
            impact: 原始影响得分 [0, 1]
            sample_count: 该节点的历史样本数量

        Returns:
            (调整后的影响得分, 置信度, 惩罚值)
        """
        threshold = self.config.sample_count_threshold
        if sample_count >= threshold:
            return impact, 1.0, 0.0

        confidence = min(1.0, sample_count / threshold)
        penalty = max(0.0, 1.0 - confidence)

        # 向 impact * confidence_floor 收缩（低 impact 保持低，高 impact 适度膨胀）
        adjusted = impact * (confidence + penalty * self.config.sample_confidence_floor)

        return adjusted, confidence, penalty
    # ═══════════════════════════════════════════════════════════════

    def rank(
        self,
        entries: list[RoutingTableEntry],
        days_since_last_seen: float | None = None,
        reverse: bool = True,
    ) -> list[ScoreBreakdown]:
        """对路由表条目列表排序，返回得分明细列表。

        Args:
            entries: 待排序的路由表条目
            days_since_last_seen: 统一的时间衰减参数（也可对每个条目单独传值）
            reverse: True 降序（高分在前），False 升序

        Returns:
            按最终得分排序的 ScoreBreakdown 列表。
        """
        breakdowns = [
            self.score_with_breakdown(e, days_since_last_seen)
            for e in entries
        ]
        breakdowns.sort(key=lambda b: b.final_score, reverse=reverse)
        return breakdowns

    def top_k(
        self,
        entries: list[RoutingTableEntry],
        k: int,
        days_since_last_seen: float | None = None,
    ) -> list[ScoreBreakdown]:
        """返回得分最高的 K 个条目的得分明细。"""
        all_scores = self.rank(entries, days_since_last_seen)
        return all_scores[:k]
