"""视频文件合法性与完整性校验（F1）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..utils.file_helper import ALLOWED_SUFFIXES, validate_input_path
from .errors import VideoValidationError
from .ffmpeg_runner import FFmpegRunner, VideoMeta

# 单文件大小下限（字节），低于该值视为空文件/损坏文件
MIN_FILE_SIZE = 128
# 可识别但时长极短的视频仍允许进入流程，由算法层兜底
MIN_DIMENSION = 16


@dataclass
class ValidationResult:
    """校验通过后的结构化结果。"""

    path: Path
    meta: VideoMeta

    def to_dict(self) -> dict:
        """序列化为接口响应字典。"""
        return {"path": str(self.path), "metadata": self.meta.to_dict()}


class VideoValidator:
    """组合路径校验、文件大小检查与 FFmpeg 探测的校验器。"""

    def __init__(self, runner: FFmpegRunner,
                 allowed_suffixes: Optional[Iterable[str]] = None):
        """
        :param runner: 已配置好二进制路径的 FFmpeg 封装。
        :param allowed_suffixes: 允许的容器后缀，默认 mp4/avi/mov。
        """
        self.runner = runner
        self.allowed_suffixes = tuple(allowed_suffixes or ALLOWED_SUFFIXES)

    def validate(self, raw_path: str) -> ValidationResult:
        """执行完整校验，任何一步失败都抛出标准化异常。

        :raises PathValidationError: 路径本身不合法。
        :raises VideoValidationError: 文件大小、容器或视频流不合法。
        """
        path = validate_input_path(raw_path, self.allowed_suffixes)
        self._check_file_size(path)
        meta = self._safe_probe(path)
        self._check_meta(meta)
        return ValidationResult(path=path, meta=meta)

    def _check_file_size(self, path: Path) -> None:
        """检查文件大小，空文件直接拒绝。"""
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise VideoValidationError(
                f"无法读取文件信息: {path}", code="VIDEO_UNREADABLE") from exc
        if size < MIN_FILE_SIZE:
            raise VideoValidationError(
                f"文件体积过小（{size} 字节），疑似空文件或损坏文件",
                code="VIDEO_EMPTY")

    def _safe_probe(self, path: Path) -> VideoMeta:
        """调用 FFmpeg 探测，把执行异常归一化为视频校验异常。"""
        try:
            return self.runner.probe(path)
        except VideoValidationError:
            raise
        except Exception as exc:
            raise VideoValidationError(
                f"视频探测失败，文件可能已损坏: {exc}",
                code="VIDEO_CORRUPT") from exc

    def _check_meta(self, meta: VideoMeta) -> None:
        """校验探测出的元数据是否具备可处理的最小条件。"""
        if meta.width < MIN_DIMENSION or meta.height < MIN_DIMENSION:
            raise VideoValidationError(
                f"分辨率过低 {meta.width}x{meta.height}，无法有效抽帧",
                code="VIDEO_RESOLUTION_INVALID")
        if meta.fps <= 0:
            raise VideoValidationError(
                "无法读取有效帧率，文件可能为裸流或已损坏",
                code="VIDEO_FPS_INVALID")
