"""子代理池 — Agent-Builder 专用子代理工厂。

当某个根分类（root category）的路由表节点数超过阈值时，
自动创建该分类的专用子代理，实现按领域专业化。

设计原则（来自 agent-builder/AGENTS.md）：
    - 每个子代理拥有独立的 SOUL.md / IDENTITY.md / AGENTS.md
    - 子代理只处理自己负责的根分类
    - 子代理与主代理通过 Storage 共享数据层

使用示例：
    pool = SubAgentPool(storage, pending_queue, log_reader=my_reader)
    pool.auto_balance(threshold=50)  # 自动为超过 50 节点的根分类创建专用子代理
    pool.maintain()                  # 依次调用所有子代理的维护
    pool.compile_skills()            # 依次调用所有子代理的 Skill 编译
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from src.models import SpecializedSkill
from src.overlap_checker import OverlapChecker
from src.pending_queue import PendingQueue
from src.quality_scorer import NodeQualityScorer
from src.routing_table import RoutingTable
from src.scoring import ScoreCalculator
from src.skill_compiler import SkillCompiler
from src.storage import Storage
from src.sub_agent import SubAgent

logger = logging.getLogger(__name__)


class SpecializedSubAgent:
    """某个根分类的专用子代理。

    职责：
        - 只处理指定根分类的路由表节点
        - 蒸馏时只提取该分类的错误方案
        - 维护时只分裂/剪枝该分类的节点
        - Skill 孵化时只编译该分类的 Skill

    Args:
        root_category: 负责的根分类，如 "network" / "data_parsing"
        storage: 共享的持久化存储
        overlap_threshold: 重叠率阈值
    """

    def __init__(
        self,
        root_category: str,
        storage: Storage,
        overlap_threshold: float = 0.7,
    ) -> None:
        self.root_category = root_category
        self._storage = storage
        self._rt = RoutingTable(storage)
        self._compiler = SkillCompiler(storage)
        self._rank_scorer = ScoreCalculator()
        self._quality_scorer = NodeQualityScorer()
        self._checker = OverlapChecker(storage, threshold=overlap_threshold)

    @property
    def category_prefix(self) -> str:
        """该子代理负责的 category_id 前缀。"""
        return f"{self.root_category}."

    def _belongs_to_me(self, category_id: str) -> bool:
        """判断 category_id 是否属于本根分类。"""
        return category_id.startswith(self.category_prefix)

    def entry_count(self) -> int:
        """当前负责的分类下有多少个路由表节点。"""
        all_entries = self._storage.query_routing_entries()
        return sum(
            1 for e in all_entries if self._belongs_to_me(e.category_id)
        )

    def maintain(
        self,
        prune_threshold: float = 0.1,
        prune_bottom_pct: float = 0.1,
        quality_delta_min: float = 0.1,
    ) -> dict[str, Any]:
        """维护该根分类下的路由表节点。

        只处理属于本分类的节点，跳过其他分类。

        Returns:
            维护操作统计（与 SubAgent.maintain() 结构一致）。
        """
        quality_gated_list: list[dict[str, Any]] = []

        all_entries = self._storage.query_routing_entries()
        for entry in all_entries:
            if not self._belongs_to_me(entry.category_id):
                continue
            score = self._quality_scorer.score(entry)
            if score.knowledge_delta < quality_delta_min:
                entry.local_map.append_log(
                    "quality_gated",
                    (
                        f"知识增量 {score.knowledge_delta:.0%} 低于门槛 "
                        f"{quality_delta_min:.0%}，质量等级: {score.quality_level}"
                    ),
                    f"sub_agent:{self.root_category}",
                )
                self._storage.upsert_routing_entry(entry)
                quality_gated_list.append({
                    "category_id": score.category_id,
                    "knowledge_delta": score.knowledge_delta,
                    "quality_level": score.quality_level,
                })

        pruned = self._rt.prune_lowest(
            threshold=prune_threshold,
            bottom_pct=prune_bottom_pct,
            reason="专用子代理维护：长期垫底 + 低质量",
            actor=f"sub_agent:{self.root_category}",
        )
        return {
            "root_category": self.root_category,
            "pruned": pruned,
            "quality_gated": quality_gated_list,
            "errors": [],
        }

    def compile_skills(
        self,
        top_k: int = 5,
        quality_delta_min: float = 0.1,
    ) -> list[SpecializedSkill]:
        """为该根分类下得分最高的节点编译 Skill。

        只处理本分类的节点，仅编译尚无 Skill 且通过质量门禁的节点。
        """
        all_entries = self._storage.query_routing_entries()
        my_entries = [
            e for e in all_entries if self._belongs_to_me(e.category_id)
        ]
        scored = [
            (self._rank_scorer.score_with_breakdown(e).final_score, e)
            for e in my_entries if e.primary_skill_id is None
        ]
        scored.sort(key=lambda s: s[0], reverse=True)
        compiled = []
        for _, entry in scored[:top_k]:
            # D1 质量门禁：与通用子代理一致，低质量节点跳过编译
            if (
                self._quality_scorer.score(entry).knowledge_delta
                < quality_delta_min
            ):
                continue
            compiled.append(self._compiler.compile_from_entry(entry))

        return compiled


class SubAgentPool:
    """子代理池：管理通用子代理 + 专用子代理。

    Agent-Builder 模式：
        - 一个通用 SubAgent 处理所有日志和反馈
        - 多个专用 SubAgent 按根分类专业化
        - auto_balance() 根据节点数量自动创建专用子代理

    使用示例：
        pool = SubAgentPool(storage, pending_queue)
        pool.auto_balance(threshold=50)
        pool.maintain()
        pool.compile_skills()
    """

    def __init__(
        self,
        storage: Storage,
        pending_queue: PendingQueue,
        log_reader: Callable[[], Iterable[dict[str, Any]]] | None = None,
        overlap_threshold: float = 0.7,
    ) -> None:
        self._storage = storage
        self._general_agent = SubAgent(
            storage, pending_queue, log_reader=log_reader,
            overlap_threshold=overlap_threshold,
        )
        self._specialized: dict[str, SpecializedSubAgent] = {}
        self._overlap_threshold = overlap_threshold

    # ── 工厂方法 ───────────────────────────────────────────────────

    def create_specialized(self, root_category: str) -> SpecializedSubAgent:
        """创建一个根分类的专用子代理。"""
        if root_category in self._specialized:
            existing = self._specialized[root_category]
            logger.info(
                "根分类 '%s' 已有专用子代理（当前节点数: %d），跳过创建",
                root_category, existing.entry_count(),
            )
            return existing

        agent = SpecializedSubAgent(
            root_category=root_category,
            storage=self._storage,
            overlap_threshold=self._overlap_threshold,
        )
        self._specialized[root_category] = agent
        logger.info("创建专用子代理: root='%s' (节点数: %d)", root_category, agent.entry_count())
        return agent

    def remove_specialized(self, root_category: str) -> None:
        """移除一个专用子代理（节点数减少后可能不再需要）。"""
        self._specialized.pop(root_category, None)

    def get_specialized(self, root_category: str) -> SpecializedSubAgent | None:
        """获取指定根分类的专用子代理。"""
        return self._specialized.get(root_category)

    def auto_balance(self, threshold: int = 50) -> list[str]:
        """自动平衡：为超过阈值的根分类创建专用子代理。

        Agent-Builder 模式：当某根分类的路由表节点数超过阈值，
        说明该领域复杂度已经需要专业化处理。

        Args:
            threshold: 根分类节点数阈值，超过则创建专用子代理

        Returns:
            本轮新创建的根分类列表。
        """
        created: list[str] = []
        all_entries = self._storage.query_routing_entries()
        counter: Counter[str] = Counter()
        for entry in all_entries:
            root = entry.category_id.split(".")[0]
            counter[root] += 1

        for root, count in counter.items():
            if count > threshold and root not in self._specialized:
                self.create_specialized(root)
                created.append(root)

        return created

    @property
    def specialized_count(self) -> int:
        """专用子代理数量。"""
        return len(self._specialized)

    @property
    def specialized_categories(self) -> list[str]:
        """所有专用子代理负责的根分类列表。"""
        return list(self._specialized.keys())

    # ── 委托方法 ───────────────────────────────────────────────────

    def distill(self) -> object:
        """委托通用子代理执行蒸馏。"""
        return self._general_agent.distill()

    def consume_pending(self) -> object:
        """委托通用子代理消费暂存队列。"""
        return self._general_agent.consume_pending()

    def maintain(
        self,
        prune_threshold: float = 0.1,
        prune_bottom_pct: float = 0.1,
        quality_delta_min: float = 0.1,
    ) -> dict[str, Any]:
        """依次调用所有子代理执行维护。

        顺序：先通用，再各专用子代理。
        """
        results: dict[str, Any] = {
            "general": self._general_agent.maintain(
                prune_threshold=prune_threshold,
                prune_bottom_pct=prune_bottom_pct,
                quality_delta_min=quality_delta_min,
            ),
            "specialized": {},
        }
        specialized_results: dict[str, dict[str, Any]] = {}
        for root, agent in self._specialized.items():
            specialized_results[root] = agent.maintain(
                prune_threshold=prune_threshold,
                prune_bottom_pct=prune_bottom_pct,
                quality_delta_min=quality_delta_min,
            )
        results["specialized"] = specialized_results
        return results

    def compile_skills(
        self,
        top_k: int = 5,
        quality_delta_min: float = 0.1,
    ) -> dict[str, list[SpecializedSkill]]:
        """依次调用所有子代理编译 Skill。"""
        results: dict[str, list[SpecializedSkill]] = {
            "general": self._general_agent.compile_skills(
                top_k=top_k, quality_delta_min=quality_delta_min,
            ),
        }
        for root, agent in self._specialized.items():
            results[root] = agent.compile_skills(
                top_k=top_k, quality_delta_min=quality_delta_min,
            )
        return results

    # ── 统计信息 ───────────────────────────────────────────────────

    def pool_summary(self) -> dict[str, Any]:
        """生成子代理池的概要统计。"""
        counter: Counter[str] = Counter()
        for entry in self._storage.query_routing_entries():
            root = entry.category_id.split(".")[0]
            counter[root] += 1

        return {
            "general_agent": {
                "entry_count": sum(counter.values()),
                "categories_covered": list(counter.keys()),
            },
            "specialized_agents": [
                {
                    "root_category": root,
                    "entry_count": agent.entry_count(),
                }
                for root, agent in self._specialized.items()
            ],
            "total_agents": len(self._specialized) + 1,
        }
