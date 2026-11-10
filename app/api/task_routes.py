"""处理任务提交、查询、取消接口（F1/F2/F5）。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.errors import WorkflowError
from ..core.video_validator import VideoValidator
from ..db.sqlite_client import SqliteClient
from .schemas import (CancelResponse, TaskCreateRequest,
                      TaskStatusResponse, TaskSubmitResponse)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _serialize(record: dict) -> dict:
    """把数据库行展开为 API 响应结构。"""
    db = SqliteClient  # 复用统一 JSON 入口
    return {
        "id": record["id"],
        "video_path": record["video_path"],
        "video_name": record["video_name"],
        "mode": record["mode"],
        "status": record["status"],
        "params": db.loads(record.get("params"), {}),
        "submitted_at": record["submitted_at"],
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "keyframe_count": record.get("keyframe_count", 0),
        "output_dir": record.get("output_dir"),
        "montage_path": record.get("montage_path"),
        "error_code": record.get("error_code"),
        "error_message": record.get("error_message"),
        "keyframes": db.loads(record.get("keyframes"), []),
        "video_metadata": db.loads(record.get("video_metadata"), None),
    }


@router.post("", response_model=TaskSubmitResponse,
             summary="批量提交本地视频处理任务")
def submit_tasks(payload: TaskCreateRequest, request: Request) -> dict:
    """批量校验并提交任务，损坏文件不会进入处理队列。"""
    state = request.app.state
    validator: VideoValidator = state.validator
    params = payload.model_dump(exclude={"video_paths"})
    submitted = []
    rejected = []
    seen = set()
    for raw_path in payload.video_paths:
        if raw_path in seen:
            rejected.append({"path": raw_path, "code": "DUPLICATE_PATH",
                             "message": "同一次提交中路径重复"})
            continue
        seen.add(raw_path)
        try:
            result = validator.validate(raw_path)
        except WorkflowError as exc:
            rejected.append({"path": raw_path, "code": exc.code,
                             "message": exc.message})
            continue
        task_id = state.queue.submit(
            video_path=str(result.path), params=params,
            video_name=result.path.name)
        submitted.append({"task_id": task_id,
                          "video_path": str(result.path),
                          "duration": result.meta.duration})
    return {"submitted": submitted, "rejected": rejected,
            "queue_depth": state.queue.queue_depth()}


@router.get("/{task_id}", response_model=TaskStatusResponse,
            summary="查询任务实时状态")
def get_task(task_id: str, request: Request) -> dict:
    """返回单个任务的完整状态与关键帧索引。"""
    record = request.app.state.queue.get_status(task_id)
    return _serialize(record)


@router.post("/{task_id}/cancel", response_model=CancelResponse,
             summary="取消排队中的任务")
def cancel_task(task_id: str, request: Request) -> dict:
    """仅排队中（pending）任务允许取消，处理中任务返回状态冲突。"""
    return request.app.state.queue.cancel(task_id)
