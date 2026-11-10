"""Pydantic 请求/响应数据模型（Pydantic v2）。

所有参数边界在此集中校验，路由层只负责业务编排。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 抽帧模式与灵敏度字面量类型
FrameMode = Literal["interval", "smart"]
Sensitivity = Literal["high", "medium", "low"]
Orientation = Literal["vertical", "horizontal"]


class TaskCreateRequest(BaseModel):
    """批量任务提交请求（F1/F2）。"""

    video_paths: List[str] = Field(
        ..., min_length=1, max_length=20,
        description="本地监控视频绝对路径列表，支持批量提交")
    mode: FrameMode = Field("interval", description="抽帧模式")
    interval_seconds: int = Field(
        10, ge=1, le=300, description="固定间隔抽帧秒数，1-300 可配")
    sensitivity: Sensitivity = Field("medium", description="智能模式灵敏度")
    filter_invalid: bool = Field(True, description="是否过滤全黑/全白帧")
    thumb_width: int = Field(320, ge=160, le=1920,
                             description="缩略图宽度，160-1920")
    orientation: Orientation = Field("vertical", description="拼接方向")
    keep_originals: bool = Field(False, description="是否保留关键帧原图")

    @field_validator("video_paths")
    @classmethod
    def _strip_paths(cls, values: List[str]) -> List[str]:
        """去除路径首尾空白并拒绝空字符串条目。"""
        cleaned = [item.strip() for item in values if item and item.strip()]
        if not cleaned:
            raise ValueError("视频路径列表不能为空")
        return cleaned


class RejectedItem(BaseModel):
    """批量提交中被拒绝文件的标准化错误结构。"""

    path: str
    code: str
    message: str


class TaskSubmitResponse(BaseModel):
    """批量提交响应：成功入队与被拒绝文件分开返回。"""

    submitted: List[dict]
    rejected: List[RejectedItem]
    queue_depth: int


class TaskStatusResponse(BaseModel):
    """任务实时状态响应。"""

    id: str
    video_path: str
    video_name: str
    mode: str
    status: str
    params: dict
    submitted_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    keyframe_count: int
    output_dir: Optional[str] = None
    montage_path: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    keyframes: List[dict] = []
    video_metadata: Optional[dict] = None


class CancelResponse(BaseModel):
    """取消任务响应。"""

    task_id: str
    cancel_requested: bool
    note: str


class RecordQuery(BaseModel):
    """历史记录查询参数（GET 查询参数在路由内解析，此处仅作文档模型）。"""

    start_time: Optional[str] = None
    end_time: Optional[str] = None
    status: Optional[str] = None
    video_name: Optional[str] = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class RecordListResponse(BaseModel):
    """分页历史记录响应。"""

    total: int
    page: int
    page_size: int
    items: List[TaskStatusResponse]


class DeleteRecordsRequest(BaseModel):
    """批量删除历史记录请求。"""

    ids: List[str] = Field(..., min_length=1, max_length=100)
    delete_files: bool = Field(
        False, description="是否同步删除本地输出文件与缩略图")


class DeleteResponse(BaseModel):
    """删除结果响应。"""

    deleted: int
    files_removed: int
    not_found: List[str]


class HealthResponse(BaseModel):
    """系统健康检查响应。"""

    status: str
    ffmpeg_available: bool
    data_dir: str
    queue_depth: int


class SelfCheckResponse(BaseModel):
    """F7 自检报告。"""

    passed: bool
    checks: List[dict]
    summary: str
