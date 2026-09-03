"""SQLite 存储层 — 路由表、暂存队列、Skill 库的统一持久化。

设计要点：
- 使用 JSON 列存储复杂对象（LocalMindMap / stats / Skill DAG）
- 标签存储为逗号分隔字符串（SQLite 无原生数组）
- 所有写入操作返回写入行数，便于调用方验证
- 时间字段统一使用 ISO 8601 字符串
"""
from __future__ import annotations

import functools
import json
import sqlite3
import threading
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar, cast

from src.models import (
    LocalMindMap,
    RoutingTableEntry,
    SpecializedSkill,
    Tag,
    UnclassifiedFailurePackage,
)

_F = TypeVar("_F", bound=Callable[..., Any])


def _serialized(func: _F) -> _F:
    """把 Storage 的公开方法串行化，保证跨线程安全。

    第七批 F-6：sqlite3 连接默认 `check_same_thread=True`，跨线程使用会抛
    `ProgrammingError: SQLite objects created in a thread can only be used
    in that same thread`。而 BUG-11 的并发修复已经在 `dequeue_feedback`
    用了 `BEGIN IMMEDIATE`，说明设计上预期并发 —— 但连接层并不支撑。

    这里采用「单连接 + 可重入锁」而非「每线程一连接」：
    - 单连接配合 WAL 足以支撑本项目「一个 serve.py 子进程」的并发量；
    - 锁保证同一时刻只有一个线程操作该连接，避免游标交错；
    - RLock 可重入，因此已持锁的方法内部再调用 `_transaction()` 不会死锁。
    """

    @functools.wraps(func)
    def wrapper(self: Storage, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return func(self, *args, **kwargs)

    return cast(_F, wrapper)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS routing_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id TEXT UNIQUE NOT NULL,
    stats JSON NOT NULL,
    local_map JSON NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    primary_skill_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data JSON NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    processed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT UNIQUE NOT NULL,
    data JSON NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Storage:
    """SQLite 存储引擎。管理连接、建表、CRUD 操作。

    使用示例：
        db = Storage("path/to/data.db")
        db.init()
        db.upsert_routing_entry(entry)
        entries = db.query_routing_entries()
        db.close()
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        # 第七批 F-6：跨线程串行化锁（详见 _serialized 装饰器说明）
        self._lock = threading.RLock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 第七批 F-6：check_same_thread=False 允许跨线程使用同一连接，
            # 并发安全由 self._lock 保证（而非依赖 sqlite3 的线程检查）
            self._conn = sqlite3.connect(
                str(self._path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @_serialized
    def init(self) -> None:
        """初始化所有表结构。幂等：多次调用安全。"""
        conn = self._get_conn()
        conn.executescript(CREATE_TABLE_SQL)
        conn.commit()

    @_serialized
    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # BUG-34 修复：write_version / data_version 已删除。缓存层整体移除后
    # （check() 每次真实计算），不存在需要失效的缓存，失效信号无消费者——
    # 保留只会造成"信号在产生、无人消费"的死机制假象。

    # ═══════════════════════════════════════════════════════════════
    # 路由表 CRUD
    # ═══════════════════════════════════════════════════════════════

    @_serialized
    def upsert_routing_entry(self, entry: RoutingTableEntry) -> int:
        """插入或更新路由表条目，返回影响行数。"""
        now = _now_iso()
        tags_str = ",".join(sorted(t.value for t in entry.tags))
        with self._transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO routing_table
                    (category_id, stats, local_map, tags,
                     primary_skill_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?,?)
                ON CONFLICT(category_id) DO UPDATE SET
                    stats = excluded.stats,
                    local_map = excluded.local_map,
                    tags = excluded.tags,
                    primary_skill_id = excluded.primary_skill_id,
                    updated_at = excluded.updated_at
                """,
                (
                    entry.category_id,
                    json.dumps(entry.stats),
                    json.dumps(entry.local_map.to_dict()),
                    tags_str,
                    entry.primary_skill_id,
                    now,
                    now,
                ),
            )
            # 返回本次语句影响的行数，而非连接级累计计数
            return cur.rowcount

    @_serialized
    def get_routing_entry(self, category_id: str) -> RoutingTableEntry | None:
        """按 category_id 精确查询路由表条目。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM routing_table WHERE category_id = ?", (category_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    @_serialized
    def query_routing_entries(
        self,
        root_category: str | None = None,
        tags: set[Tag] | None = None,
        parent_path: str | None = None,
    ) -> list[RoutingTableEntry]:
        """查询路由表条目，支持根分类过滤、标签过滤和父路径过滤。

        - root_category: 过滤第一级分类（如 "network"）
        - tags: 仅返回包含所有指定标签的条目（AND 语义）
        - parent_path: 精确匹配 local_map.parent_path（SQL LIKE 前缀加速）
        """
        conn = self._get_conn()
        sql = "SELECT * FROM routing_table WHERE 1=1"
        params: list[Any] = []

        if root_category:
            # BUG-16 修复：使用 OR 包含根节点自身（category_id = root）
            sql += " AND (category_id = ? OR category_id LIKE ? ESCAPE '\\')"
            params.append(root_category)
            params.append(f"{_escape_like(root_category)}.%")

        if parent_path:
            sql += " AND json_extract(local_map, '$.parent_path') = ?"
            params.append(parent_path)

        # BUG-17 修复：空标签集返回空结果（而非全部）
        if tags is not None:
            if not tags:
                return []
            for tag in sorted(tags, key=lambda t: t.value):
                sql += " AND tags LIKE ? ESCAPE '\\'"
                params.append(f"%{_escape_like(tag.value)}%")

        rows = conn.execute(sql, params).fetchall()
        return [_row_to_entry(row) for row in rows]

    @_serialized
    def delete_routing_entry(self, category_id: str) -> bool:
        """删除路由表条目，返回是否删除成功。"""
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM routing_table WHERE category_id = ?", (category_id,)
            )
            # BUG-49 修复：回归缺陷。清理 write_version 死代码时误将外层
            # return 缩进进了 `if cur.rowcount > 0` 块内 —— 未命中删除时
            # 函数落空返回 None，与 `-> bool` 契约不符（调用方 `is False`
            # 判断失效）。删除分支本就无副作用，直接返回行数判定结果。
            return cur.rowcount > 0

    @_serialized
    def has_child_nodes(self, category_id: str) -> bool:
        """检查是否存在以 category_id 为 parent_path 的子节点。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM routing_table"
            " WHERE json_extract(local_map, '$.parent_path') = ?",
            (category_id,),
        ).fetchone()
        return int(row["cnt"]) > 0

    @_serialized
    def count_routing_entries(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) AS cnt FROM routing_table").fetchone()
        return int(row["cnt"])

    # ═══════════════════════════════════════════════════════════════
    # 反馈暂存队列 CRUD
    # ═══════════════════════════════════════════════════════════════

    @_serialized
    def enqueue_feedback(self, pkg: UnclassifiedFailurePackage) -> int:
        """向暂存队列写入举证包，返回新行的 id。"""
        now = _now_iso()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO pending_queue (data, created_at) VALUES (?, ?)",
            (json.dumps(pkg.to_dict()), now),
        )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    @_serialized
    def dequeue_feedback(self, limit: int = 10) -> list[UnclassifiedFailurePackage]:
        """取出未处理的举证包（按创建时间排序），标记为已处理。

        BUG-11 修复：使用 BEGIN IMMEDIATE 获取写锁，SELECT 在事务内执行，
        消除 SELECT 与 UPDATE 之间的竞态窗口。
        """
        now = _now_iso()
        conn = self._get_conn()
        # BUG-11 修复：BEGIN IMMEDIATE 获取写锁，防止并发重复消费
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT id, data FROM pending_queue
                WHERE processed = 0
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if not rows:
                conn.commit()
                return []
            ids = [row["id"] for row in rows]
            # 使用纯字符串拼接构建 IN 子句（placeholders 不含外部输入）
            in_clause = ",".join(["?"] * len(ids))
            conn.execute(
                "UPDATE pending_queue SET processed = 1, processed_at = ?"
                " WHERE id IN (" + in_clause + ")",
                [now, *ids],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # 事务外反序列化；损坏条目跳过，不阻断整批处理
        result: list[UnclassifiedFailurePackage] = []
        for row in rows:
            try:
                result.append(
                    UnclassifiedFailurePackage.from_dict(json.loads(row["data"]))
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return result

    @_serialized
    def pending_count(self) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pending_queue WHERE processed = 0"
        ).fetchone()
        return int(row["cnt"])

    @_serialized
    def cleanup_pending_expired(self, cutoff_iso: str) -> int:
        """清理超期举证包。返回删除的条目数。

        供 PendingQueue 使用，避免直接访问私有连接方法。
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_queue WHERE processed = 0 AND created_at < ?",
                (cutoff_iso,),
            )
            return cursor.rowcount

    # ═══════════════════════════════════════════════════════════════
    # Skill 库 CRUD
    # ═══════════════════════════════════════════════════════════════

    @_serialized
    def upsert_skill(self, skill: SpecializedSkill) -> int:
        now = _now_iso()
        with self._transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO skills (skill_id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (skill.skill_id, json.dumps(skill.to_dict()), now, now),
            )
            return cur.rowcount

    @_serialized
    def get_skill(self, skill_id: str) -> SpecializedSkill | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
        ).fetchone()
        if row is None:
            return None
        return SpecializedSkill.from_dict(json.loads(row["data"]))

    # BUG-43 修复：补加 @_serialized。get_skills 是后加的批量优化接口，
    # 此前是全部公开方法中唯一在锁外直接操作连接的——跨线程场景下可与
    # 持锁的写入线程交错，触发 ProgrammingError / 撕裂读（F-6 覆盖缺口）。
    @_serialized
    def get_skills(self, skill_ids: Iterable[str]) -> dict[str, SpecializedSkill]:
        """批量获取 Skill，返回 {skill_id: SpecializedSkill}。

        替代对 N 个条目各调一次 get_skill 的 N+1 模式——后者在主代理
        模糊查询路径上随路由表规模线性放大，是主代理秒级响应的主要成本之一。

        输入中重复或为空的 skill_id 会被去重，避免拼出过大的 SQL。
        未命中的 id 不会出现在返回字典里，调用方需自行处理「没找到」语义。
        """
        ids = {sid for sid in skill_ids if sid}
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._get_conn().execute(
            f"SELECT skill_id, data FROM skills WHERE skill_id IN ({placeholders})",
            list(ids),
        ).fetchall()
        return {
            row["skill_id"]: SpecializedSkill.from_dict(json.loads(row["data"]))
            for row in rows
        }

    # ═══════════════════════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════════════════════

    def __enter__(self) -> Storage:
        self.init()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ══════════════════════════════════════════════════════════════════
# 内部辅助函数
# ══════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _escape_like(value: str) -> str:
    """转义 SQL LIKE 模式中的特殊字符（% _ 与转义符本身），防止误匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_entry(row: sqlite3.Row) -> RoutingTableEntry:
    tags: set[Tag] = set()
    if row["tags"]:
        for t in row["tags"].split(","):
            tag = Tag.coerce(t.strip())
            if tag is not None:
                tags.add(tag)
    return RoutingTableEntry(
        category_id=row["category_id"],
        stats=json.loads(row["stats"]),
        local_map=LocalMindMap.from_dict(json.loads(row["local_map"])),
        tags=tags,
        primary_skill_id=row["primary_skill_id"],
    )
