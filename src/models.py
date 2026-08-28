"""六大核心数据模型 — 受控自进化 AI Agent 框架的数据基石。

设计原则：
- 每个模型携带 `LocalMindMap`（局部思维导图），记录边界与逻辑签名
- 标签必须带前缀（状态_ / 代价_ / 场景_），禁止裸标签
- 所有时间字段使用 datetime (ISO 8601)
- 所有 ID 使用字符串，格式由调用方约定
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ══════════════════════════════════════════════════════════════════
# 标签前缀枚举
# ══════════════════════════════════════════════════════════════════

class TagPrefix(str, Enum):
    """三类强制前缀。所有 Tag.value 必须以这三种之一开头。"""
    STATUS = "状态_"
    COST = "代价_"
    SCENARIO = "场景_"


# ══════════════════════════════════════════════════════════════════
# 1. 局部思维导图（LocalMindMap）— 执念核心
# ══════════════════════════════════════════════════════════════════

@dataclass
class MaintenanceLog:
    """单条维护日志条目。"""
    timestamp: datetime
    action: str  # create / update / split / merge / delete
    reason: str
    actor: str   # human / sub_agent / main_agent

    def to_dict(self) -> dict[str, Any]:
        ts = self.timestamp
        if isinstance(ts, str):
            ts_str = ts
        else:
            ts_str = ts.isoformat()
        return {
            "timestamp": ts_str,
            "action": self.action,
            "reason": self.reason,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaintenanceLog:
        """从字典恢复维护日志条目。"""
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            timestamp=ts,
            action=data["action"],
            reason=data["reason"],
            actor=data["actor"],
        )


@dataclass
class LocalMindMap:
    """局部思维导图：每个路由表节点和 Skill 步骤的元数据。

    这是框架的"执念核心"——强制每个节点记述：
    - 自己聚焦解决什么（focus_description）
    - 绝对不管什么（boundary_rules）——防越界的关键
    - 逻辑签名（logic_signature）——自然语言描述行为
    - 血缘关系（node_id + parent_path）
    - 完整变更史（maintenance_log）
    """
    node_id: str
    parent_path: str
    focus_description: str
    boundary_rules: str
    logic_signature: str
    maintenance_log: list[MaintenanceLog] = field(default_factory=list)

    def append_log(self, action: str, reason: str, actor: str) -> None:
        """追加一条维护日志。"""
        self.maintenance_log.append(
            MaintenanceLog(
                timestamp=datetime.now(timezone.utc),
                action=action,
                reason=reason,
                actor=actor,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_path": self.parent_path,
            "focus_description": self.focus_description,
            "boundary_rules": self.boundary_rules,
            "logic_signature": self.logic_signature,
            "maintenance_log": [log.to_dict() for log in self.maintenance_log],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalMindMap:
        logs = [
            MaintenanceLog.from_dict(log) for log in data.get("maintenance_log", [])
        ]
        return cls(
            node_id=data["node_id"],
            parent_path=data["parent_path"],
            focus_description=data["focus_description"],
            boundary_rules=data["boundary_rules"],
            logic_signature=data["logic_signature"],
            maintenance_log=logs,
        )


# ══════════════════════════════════════════════════════════════════
# 2. 标签（Tag）— 带前缀校验
# ══════════════════════════════════════════════════════════════════

# 合法的标签值白名单（可扩展）
_VALID_TAG_VALUES: dict[TagPrefix, set[str]] = {
    TagPrefix.STATUS: {"稳定", "实验性", "废弃"},
    TagPrefix.COST: {"高延迟", "低消耗", "中消耗"},
    TagPrefix.SCENARIO: {"第三方依赖", "内部微服务", "本地计算"},
}


@dataclass(frozen=True)
class Tag:
    """带前缀的标签。value 必须形如 '状态_稳定'、'代价_低消耗' 等。

    使用 frozen dataclass 保证不可变性。
    """
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("标签值不能为空")
        # 校验前缀
        prefix = next((p for p in TagPrefix if self.value.startswith(p.value)), None)
        if prefix is None:
            raise ValueError(
                f"标签 '{self.value}' 缺少合法前缀（状态_ / 代价_ / 场景_）"
            )
        # 提取标签本体
        tag_body = self.value[len(prefix.value):]
        allowed = _VALID_TAG_VALUES[prefix]
        if tag_body not in allowed:
            raise ValueError(
                f"标签 '{self.value}' 本体不在允许列表 {allowed} 中，"
                f"如需新增请先更新 _VALID_TAG_VALUES"
            )

    @property
    def prefix(self) -> TagPrefix:
        """返回标签前缀枚举。"""
        for p in TagPrefix:
            if self.value.startswith(p.value):
                return p
        raise RuntimeError("不可达：Tag.__post_init__ 已校验前缀")

    @property
    def body(self) -> str:
        """返回标签本体（去掉前缀）。"""
        return self.value[len(self.prefix.value):]

    @classmethod
    def coerce(cls, value: str) -> Tag | None:
        """宽容反序列化：前缀合法即保留，本体不在白名单也不抛错。

        区别于严格构造 `Tag(value)`（新数据写入使用，受控强校验）：
        - `coerce` 用于从历史/外部数据还原，容忍 `_VALID_TAG_VALUES` 之外的
          本体（需求演进可能淘汰旧标签值），保留原文不丢。
        - 若 value 为空或无合法前缀（裸标签），返回 None，由调用方跳过，
          不构造出无法归类于 TagPrefix 的 Tag。
        """
        if not value or not any(value.startswith(p.value) for p in TagPrefix):
            return None
        obj = object.__new__(cls)
        object.__setattr__(obj, "value", value)
        return obj

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Tag):
            return self.value == other.value
        return NotImplemented


# ══════════════════════════════════════════════════════════════════
# 3. 路由表节点（RoutingTableEntry）
# ══════════════════════════════════════════════════════════════════

# 人类锁定的根分类骨架 — 不可被算法改写
ROOT_CATEGORIES: frozenset[str] = frozenset({
    "network",
    "data_parsing",
    "llm_inference",
    "resource_exhaustion",
    "permission",
})


@dataclass
class RoutingTableEntry:
    """路由表条目 — 规避洞察路由表的核心数据单元。

    category_id 使用点号分隔的层级命名，如 'network.rate_limit.429'。
    第一级必须属于 ROOT_CATEGORIES（人类锁定层）。
    """
    category_id: str
    stats: dict[str, float | str]  # freq / impact / trend / recover_cost / last_seen
    local_map: LocalMindMap
    tags: set[Tag] = field(default_factory=set)
    primary_skill_id: str | None = None

    def __post_init__(self) -> None:
        # 校验根分类
        root = self.category_id.split(".")[0]
        if root not in ROOT_CATEGORIES:
            raise ValueError(
                f"category_id '{self.category_id}' 根分类 '{root}' "
                f"不在人类锁定的根分类 {sorted(ROOT_CATEGORIES)} 中"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "stats": self.stats,
            "local_map": self.local_map.to_dict(),
            "tags": [tag.value for tag in self.tags],
            "primary_skill_id": self.primary_skill_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingTableEntry:
        entry = cls(
            category_id=data["category_id"],
            stats=data["stats"],
            local_map=LocalMindMap.from_dict(data["local_map"]),
            tags={t for v in data.get("tags", []) if (t := Tag.coerce(v)) is not None},
            primary_skill_id=data.get("primary_skill_id"),
        )
        return entry


# ══════════════════════════════════════════════════════════════════
# 4. Skill 步骤与工作流（Skill DAG）
# ══════════════════════════════════════════════════════════════════

@dataclass
class SkillStep:
    """Skill 中的单一步骤，携带步骤局部地图。"""
    step_id: str
    action: str
    local_map: LocalMindMap
    precondition: str | None = None
    postcondition: str | None = None
    retry_policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "local_map": self.local_map.to_dict(),
            "precondition": self.precondition,
            "postcondition": self.postcondition,
            "retry_policy": self.retry_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillStep:
        return cls(
            step_id=data["step_id"],
            action=data["action"],
            local_map=LocalMindMap.from_dict(data["local_map"]),
            precondition=data.get("precondition"),
            postcondition=data.get("postcondition"),
            retry_policy=data.get("retry_policy"),
        )


@dataclass
class SpecializedSkill:
    """专类 Skill 工作流（DAG）。overview_map 继承自路由表节点。

    pattern: Skill 结构模式（来自 Skill-Builder 模板模式适配）。
    可选值："tool" / "domain" / "workflow" / "memory" / "generic"。
    不同模式对应不同的步骤结构和行为特征。

    tools: Skill 运行时工具集。从路由表节点的边界规则中推断，
    描述该 Skill 执行时需要哪些工具（如 "http_client" / "retry" / "memory"）。
    这是 Agent-Builder "Skill 运行时化" 的一部分。

    context_keys: Skill 执行时需要从主代理上下文读取的键名列表，
    用于上下文压缩和按需注入。
    """

    skill_id: str
    name: str
    pattern: str = "generic"
    overview_map: LocalMindMap = field(
        default_factory=lambda: LocalMindMap(
            node_id="", parent_path="",
            focus_description="", boundary_rules="", logic_signature="",
        )
    )
    steps: list[SkillStep] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    context_keys: list[str] = field(default_factory=list)
    tags: set[Tag] = field(default_factory=set)

    def add_step(self, step: SkillStep) -> None:
        """向 Skill 追加一个执行步骤。"""
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "pattern": self.pattern,
            "overview_map": self.overview_map.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "tools": list(self.tools),
            "context_keys": list(self.context_keys),
            "tags": [tag.value for tag in self.tags],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecializedSkill:
        return cls(
            skill_id=data["skill_id"],
            name=data["name"],
            pattern=data.get("pattern", "generic"),
            overview_map=LocalMindMap.from_dict(data["overview_map"]),
            steps=[SkillStep.from_dict(s) for s in data.get("steps", [])],
            tools=data.get("tools", []),
            context_keys=data.get("context_keys", []),
            tags={t for v in data.get("tags", []) if (t := Tag.coerce(v)) is not None},
        )


# ══════════════════════════════════════════════════════════════════
# 5. 未知错误举证包（UnclassifiedFailurePackage）
# ══════════════════════════════════════════════════════════════════

@dataclass
class UnclassifiedFailurePackage:
    """主代理遇到未知错误时生成的举证包，异步写入反馈暂存队列。"""
    error_stack: str
    context_snapshot: dict[str, Any]
    attempted_strategies: list[str] = field(default_factory=list)
    location_guess: str = ""
    confidence: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 必须在 [0, 1] 之间，实际值: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_stack": self.error_stack,
            "context_snapshot": self.context_snapshot,
            "attempted_strategies": self.attempted_strategies,
            "location_guess": self.location_guess,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnclassifiedFailurePackage:
        return cls(
            error_stack=data["error_stack"],
            context_snapshot=data.get("context_snapshot", {}),
            attempted_strategies=data.get("attempted_strategies", []),
            location_guess=data.get("location_guess", ""),
            confidence=data.get("confidence", 0.0),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


# ══════════════════════════════════════════════════════════════════
# 7. 节点质量评分（NodeQualityScore）— Skill-Judge D1 知识增量维度
# ══════════════════════════════════════════════════════════════════

@dataclass
class NodeQualityScore:
    """路由表节点质量评分（Skill-Judge D1 知识增量维度）。

    基于 Skill-Judge 白皮书的核心公式：
        知识增量 = E / (E + A + R)
        E = Expert 知识（具体策略、决策树、反模式、边界案例）
        A = Activation 知识（通用提醒、已知概念标注）
        R = Redundant 知识（"处理X"、"修复X"、"检查X"等空话）

    质量等级判定：
        delta >= 0.5 → "expert"（保留）
        delta >= 0.3 → "adequate"（可接受）
        delta >= 0.1 → "poor"（标记待改进）
        delta < 0.1  → "redundant"（加入剪枝候选）
    """

    category_id: str
    expert_score: float
    activation_score: float
    redundant_score: float
    knowledge_delta: float
    quality_level: str
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "expert_score": round(self.expert_score, 4),
            "activation_score": round(self.activation_score, 4),
            "redundant_score": round(self.redundant_score, 4),
            "knowledge_delta": round(self.knowledge_delta, 4),
            "quality_level": self.quality_level,
            "signals": list(self.signals),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeQualityScore:
        return cls(
            category_id=data["category_id"],
            expert_score=data.get("expert_score", 0.0),
            activation_score=data.get("activation_score", 0.0),
            redundant_score=data.get("redundant_score", 0.0),
            knowledge_delta=data.get("knowledge_delta", 0.0),
            quality_level=data.get("quality_level", "redundant"),
            signals=data.get("signals", []),
        )
