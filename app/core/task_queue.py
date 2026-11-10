"""单机串行任务队列（F5）。

实现方式：单后台工作线程 + SQLite 持久化。默认串行处理避免抢占本地
资源；任务状态全部落库，服务重启后调用 :meth:`TaskQueue.restore` 即可
自动恢复未完成任务。同时在 :class:`fastapi.BackgroundTasks` 语义下
保持轻量，不引入任何第三方消息中间件。
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..db.sqlite_client import SqliteClient
from ..db.table_schema import (RECOVERABLE_STATUSES, STATUS_PENDING,
                               STATUS_RUNNING)
from ..utils.time_helper import now_iso
from .errors import TaskNotFoundError, TaskStateError
from .task_processor import TaskProcessor

LOGGER = logging.getLogger("monitor_workshop.queue")


class TaskQueue:
    """内存投递 + 数据库持久化的串行任务队列。"""

    def __init__(self, db: SqliteClient, processor: TaskProcessor):
        """
        :param db: 任务持久化客户端。
        :param processor: 单任务流水线处理器。
        """
        self.db = db
        self.processor = processor
        self._pending: "queue.Queue[str]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """启动单工作线程，重复调用安全。"""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_loop, name="task-worker", daemon=True)
            self._worker.start()

    def shutdown(self, wait: bool = True, timeout: float = 10.0) -> None:
        """通知工作线程退出；排队任务保留在数据库中等待下次恢复。"""
        self._stop_event.set()
        # 投递哨兵唤醒可能处于阻塞等待的工作线程
        self._pending.put(None)
        if wait and self._worker:
            self._worker.join(timeout=timeout)

    def restore(self) -> int:
        """服务重启后恢复未完成任务。

        先把遗留的 running 重置为 pending，再把全部 pending 任务重新
        投递到内存队列，返回恢复的任务数量。
        """
        reset = self.db.reset_running_tasks()
        rows = self.db.list_tasks_by_status([STATUS_PENDING])
        for row in rows:
            self._pending.put(row["id"])
        if rows:
            LOGGER.info("恢复未完成任务 %d 个（重置 running %d 个）",
                        len(rows), reset)
        return len(rows)

    # ---------- 任务投递与控制 ----------

    def submit(self, video_path: str, params: Dict[str, Any],
               video_name: Optional[str] = None) -> str:
        """创建任务记录并入队，返回任务 ID。"""
        task_id = uuid.uuid4().hex
        record = {
            "id": task_id,
            "video_path": video_path,
            "video_name": video_name or Path(video_path).name,
            "mode": params.get("mode", "interval"),
            "params": self.db.dumps(params),
            "status": STATUS_PENDING,
            "submitted_at": now_iso(),
        }
        self.db.insert("tasks", record)
        self._pending.put(task_id)
        return task_id

    def cancel(self, task_id: str) -> dict:
        """取消任务：仅排队中的任务可立即取消。

        处理中的任务会被标记取消请求，由流水线在安全节点检查（本版本
        流水线较短，处理中任务不强制中断）。
        """
        record = self.db.fetch_one("tasks", "id", task_id)
        if record is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if record["status"] == STATUS_PENDING:
            self.db.update("tasks", "id", task_id,
                           {"cancel_requested": 1})
            return {"task_id": task_id, "cancel_requested": True,
                    "note": "任务将在出队时取消"}
        if record["status"] == STATUS_RUNNING:
            raise TaskStateError("任务已开始处理，无法取消")
        raise TaskStateError(
            f"任务当前状态 {record['status']}，无需取消")

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """查询任务实时状态，不存在时抛出标准化异常。"""
        record = self.db.fetch_one("tasks", "id", task_id)
        if record is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        return record

    def queue_depth(self) -> int:
        """当前内存队列中等待处理的任务数量。"""
        return self._pending.qsize()

    # ---------- 工作线程 ----------

    def _run_loop(self) -> None:
        """串行消费队列，单个任务异常不影响后续任务。"""
        while not self._stop_event.is_set():
            task_id = self._pending.get()
            if task_id is None:
                break
            try:
                self.processor.process(task_id)
            except Exception:  # noqa: BLE001 - 错误已落库，仅记录日志
                LOGGER.exception("任务 %s 处理失败", task_id)
            finally:
                self._pending.task_done()

    def recoverable_statuses(self) -> tuple:
        """暴露可恢复状态集合，便于 API 自检展示。"""
        return RECOVERABLE_STATUSES
