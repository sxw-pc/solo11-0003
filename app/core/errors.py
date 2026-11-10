"""领域错误与标准化错误码定义。

所有业务异常都携带稳定的字符串错误码，API 层统一转换为 JSON 响应，
便于离线环境下的问题定位（F5 失败任务返回具体错误码与原因）。
"""

from __future__ import annotations


class WorkflowError(Exception):
    """业务异常基类。"""

    code = "WORKFLOW_ERROR"
    http_status = 400

    def __init__(self, message: str, code: str | None = None,
                 http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status

    def to_dict(self) -> dict:
        """转换为可序列化的错误响应体。"""
        return {"code": self.code, "message": self.message}


class FFmpegNotFoundError(WorkflowError):
    """本地未检测到可用 FFmpeg 可执行文件。"""

    code = "FFMPEG_NOT_FOUND"
    http_status = 503


class VideoValidationError(WorkflowError):
    """视频文件校验失败（损坏、格式非法、无视频流等）。"""

    code = "VIDEO_INVALID"
    http_status = 422


class FFmpegExecError(WorkflowError):
    """FFmpeg 子进程执行失败。"""

    code = "FFMPEG_EXEC_FAILED"
    http_status = 500


class TaskNotFoundError(WorkflowError):
    """任务 ID 不存在或已被清理。"""

    code = "TASK_NOT_FOUND"
    http_status = 404


class TaskStateError(WorkflowError):
    """任务当前状态不允许该操作（例如取消处理中的任务）。"""

    code = "TASK_STATE_INVALID"
    http_status = 409


class DiskSpaceError(WorkflowError):
    """磁盘剩余空间不足以支撑任务输出。"""

    code = "DISK_SPACE_INSUFFICIENT"
    http_status = 507


class ConfigError(WorkflowError):
    """本地配置缺失或非法。"""

    code = "CONFIG_INVALID"
    http_status = 500
