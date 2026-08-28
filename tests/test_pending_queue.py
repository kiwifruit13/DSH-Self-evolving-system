"""反馈暂存队列单元测试 — 容量 / 过期 / 入队出队。"""
from pathlib import Path

import pytest

from src.models import UnclassifiedFailurePackage
from src.pending_queue import PendingQueue, QueueFullError
from src.storage import Storage


@pytest.fixture
def storage(tmp_db_path: Path) -> Storage:
    db = Storage(str(tmp_db_path))
    db.init()
    return db


@pytest.fixture
def queue(storage: Storage) -> PendingQueue:
    return PendingQueue(storage, capacity=5, max_age_hours=168)


def _make_pkg(i: int = 1) -> UnclassifiedFailurePackage:
    return UnclassifiedFailurePackage(
        error_stack=f"Error {i}",
        context_snapshot={"idx": i},
        confidence=0.5,
    )


# ══════════════════════════════════════════════════════════════════
# 基本入队出队
# ══════════════════════════════════════════════════════════════════

class TestPendingQueueBasics:
    def test_enqueue_and_dequeue(self, queue: PendingQueue) -> None:
        assert queue.pending_count == 0
        assert queue.enqueue(_make_pkg(1)) is True
        assert queue.pending_count == 1

        items = queue.dequeue(limit=1)
        assert len(items) == 1
        assert items[0].error_stack == "Error 1"
        assert queue.pending_count == 0

    def test_enqueue_returns_false_when_full_with_callback(self, storage: Storage) -> None:
        called = {"n": 0}

        def on_full_cb() -> None:
            called["n"] += 1

        queue = PendingQueue(storage, capacity=2, on_full=on_full_cb)
        assert queue.enqueue(_make_pkg(1)) is True
        assert queue.enqueue(_make_pkg(2)) is True
        assert queue.is_full() is True
        assert queue.enqueue(_make_pkg(3)) is False
        assert called["n"] == 1

    def test_enqueue_raises_when_full_no_callback(self, storage: Storage) -> None:
        queue = PendingQueue(storage, capacity=2)
        queue.enqueue(_make_pkg(1))
        queue.enqueue(_make_pkg(2))
        with pytest.raises(QueueFullError, match="已满"):
            queue.enqueue(_make_pkg(3))

    def test_remaining(self, storage: Storage) -> None:
        queue = PendingQueue(storage, capacity=5)
        assert queue.remaining == 5
        queue.enqueue(_make_pkg(1))
        assert queue.remaining == 4
        queue.enqueue(_make_pkg(2))
        queue.enqueue(_make_pkg(3))
        assert queue.remaining == 2

    def test_dequeue_fifo_order(self, queue: PendingQueue) -> None:
        queue.enqueue(_make_pkg(1))
        queue.enqueue(_make_pkg(2))
        queue.enqueue(_make_pkg(3))

        items = queue.dequeue(limit=10)
        assert len(items) == 3
        assert items[0].error_stack == "Error 1"
        assert items[1].error_stack == "Error 2"
        assert items[2].error_stack == "Error 3"

    def test_dequeue_respects_limit(self, queue: PendingQueue) -> None:
        for i in range(5):
            queue.enqueue(_make_pkg(i + 1))

        items = queue.dequeue(limit=2)
        assert len(items) == 2
        assert queue.pending_count == 3

    def test_dequeue_empty(self, queue: PendingQueue) -> None:
        assert queue.dequeue() == []

    def test_repr(self, queue: PendingQueue) -> None:
        r = repr(queue)
        assert "PendingQueue" in r
        assert "pending=0" in r
