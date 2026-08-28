"""反馈暂存队列 — 主代理与子代理之间的异步通信管道。

设计要点：
- 容量上限：超过限制时拒绝入队，防止内存/磁盘无限膨胀
- 过期策略：默认保留 7 天，超期举证包自动清理
- 基于 Storage 的持久化实现，支持重启后恢复

使用示例：
    queue = PendingQueue(storage, capacity=1000, max_age_hours=168)
    ok = queue.enqueue(pkg)   # False 表示队列已满
    items = queue.dequeue(limit=10)
    queue.cleanup_expired()   # 定时调用清理超期条目
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.models import UnclassifiedFailurePackage
from src.storage import Storage


class QueueFullError(Exception):
    """队列已满时抛出。"""


class PendingQueue:
    """反馈暂存队列。

    Args:
        storage: 底层持久化存储
        capacity: 最大容量（未处理条目数），默认 1000
        max_age_hours: 举证包最大存活时间（小时），默认 168（7 天）
        on_full: 队列满时的回调，默认抛出 QueueFullError
    """

    def __init__(
        self,
        storage: Storage,
        capacity: int = 1000,
        max_age_hours: float = 168.0,
        on_full: Callable[[], None] | None = None,
    ) -> None:
        self._storage = storage
        self._capacity = capacity
        self._max_age = timedelta(hours=max_age_hours)
        self._on_full = on_full

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def pending_count(self) -> int:
        """当前未处理条目数。"""
        return self._storage.pending_count()

    @property
    def remaining(self) -> int:
        """剩余可用容量。"""
        return max(0, self._capacity - self.pending_count)

    def is_full(self) -> bool:
        """队列是否已满。"""
        return self.pending_count >= self._capacity

    def enqueue(self, pkg: UnclassifiedFailurePackage) -> bool:
        """入队举证包。

        Returns:
            True 表示成功入队，False 表示队列已满。

        Raises:
            QueueFullError: 当 on_full 未设置且队列已满时。
        """
        if self.is_full():
            if self._on_full is not None:
                self._on_full()
                return False
            raise QueueFullError(
                f"反馈暂存队列已满 (capacity={self._capacity})，"
                f"无法入队: {pkg.error_stack[:80]}"
            )

        self._storage.enqueue_feedback(pkg)
        return True

    def dequeue(self, limit: int = 10) -> list[UnclassifiedFailurePackage]:
        """出队未处理的举证包，按创建时间升序返回。"""
        return self._storage.dequeue_feedback(limit=limit)

    def cleanup_expired(self) -> int:
        """清理超期举证包。返回删除的条目数。"""
        cutoff = datetime.now(timezone.utc) - self._max_age
        return self._storage.cleanup_pending_expired(cutoff.isoformat())

    def __repr__(self) -> str:
        return (
            f"PendingQueue(pending={self.pending_count}, "
            f"capacity={self._capacity}, "
            f"max_age={self._max_age})"
        )
