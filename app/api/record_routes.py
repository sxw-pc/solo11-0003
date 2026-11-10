"""历史处理记录查询、删除接口（F6）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..db.table_schema import ALL_STATUSES
from ..utils import file_helper
from ..utils.time_helper import is_within_range
from .schemas import (DeleteRecordsRequest, DeleteResponse,
                      RecordListResponse)
from .task_routes import _serialize

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("", response_model=RecordListResponse,
            summary="多条件分页查询历史记录")
def list_records(
        request: Request,
        start_time: Optional[str] = Query(None, description="起始时间 ISO"),
        end_time: Optional[str] = Query(None, description="结束时间 ISO"),
        status: Optional[str] = Query(None,
                                      description="任务状态过滤"),
        video_name: Optional[str] = Query(None,
                                          description="视频文件名模糊匹配"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100)) -> dict:
    """按时间范围、状态、文件名模糊查询，结果按提交时间倒序。"""
    if status and status not in ALL_STATUSES:
        from ..core.errors import WorkflowError

        raise WorkflowError(f"非法状态过滤值: {status}",
                            code="PARAM_INVALID", http_status=422)
    rows = request.app.state.db.query(
        "SELECT * FROM tasks ORDER BY submitted_at DESC")
    filtered = []
    for row in rows:
        if start_time or end_time:
            if not is_within_range(row["submitted_at"], start_time,
                                   end_time):
                continue
        if status and row["status"] != status:
            continue
        if video_name and video_name.lower() not in \
                row["video_name"].lower():
            continue
        filtered.append(row)
    total = len(filtered)
    start = (page - 1) * page_size
    page_rows = filtered[start:start + page_size]
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_serialize(row) for row in page_rows]}


@router.delete("", response_model=DeleteResponse,
               summary="批量删除历史记录")
def delete_records(payload: DeleteRecordsRequest,
                   request: Request) -> dict:
    """删除数据库记录，可选同步删除本地输出文件（F6/F8）。"""
    db = request.app.state.db
    not_found = []
    files_removed = 0
    existing_ids = []
    for task_id in payload.ids:
        record = db.fetch_one("tasks", "id", task_id)
        if record is None:
            not_found.append(task_id)
            continue
        existing_ids.append(task_id)
        if payload.delete_files:
            files_removed += _remove_task_files(record)
    deleted = db.delete("tasks", "id", existing_ids)
    return {"deleted": deleted, "files_removed": files_removed,
            "not_found": not_found}


def _remove_task_files(record: dict) -> int:
    """删除单条任务关联的输出目录，返回清理条目数（0 或 1）。"""
    output_dir = record.get("output_dir")
    if not output_dir:
        return 0
    target = Path(output_dir)
    if not target.exists():
        return 0
    return 1 if file_helper.remove_path(target) else 0
