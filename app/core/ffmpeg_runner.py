"""本地 FFmpeg 可执行文件调用封装。

仅通过 ``subprocess`` 调用本机 FFmpeg，不引入任何远程能力。负责：
- 视频元数据探测（时长、分辨率、帧率、编码）；
- 固定时间点抽帧与均匀采样抽帧；
- 输出文件存在性校验。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .errors import FFmpegExecError, FFmpegNotFoundError, WorkflowError

# 单次 FFmpeg 调用的最长时长（秒），避免异常文件造成进程悬挂
DEFAULT_TIMEOUT = 300


@dataclass
class VideoMeta:
    """视频元数据结构化结果。"""

    duration: float
    width: int
    height: int
    fps: float
    codec: str
    bit_rate: int = 0
    has_audio: bool = False

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的字典。"""
        return {
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "codec": self.codec,
            "bit_rate": self.bit_rate,
            "has_audio": self.has_audio,
        }


class FFmpegRunner:
    """FFmpeg 子进程的轻量封装，实例无状态、可复用。"""

    def __init__(self, ffmpeg_path: Optional[str],
                 timeout: int = DEFAULT_TIMEOUT):
        """
        :param ffmpeg_path: FFmpeg 可执行文件路径，None 或文件不存在时
            后续调用会抛出 :class:`FFmpegNotFoundError`。
        :param timeout: 子进程超时时间（秒）。
        """
        self.ffmpeg_path = ffmpeg_path
        self.timeout = timeout

    def _ensure_binary(self) -> str:
        """确认 FFmpeg 二进制可用，否则抛出标准化异常。"""
        if not self.ffmpeg_path or not Path(self.ffmpeg_path).is_file():
            raise FFmpegNotFoundError(
                "未检测到本地 FFmpeg，请安装后通过 MONITOR_FFMPEG 指向它")
        return self.ffmpeg_path

    def _run(self, args: List[str]) -> subprocess.CompletedProcess:
        """执行子进程并统一处理超时与非零退出码。"""
        binary = self._ensure_binary()
        cmd = [binary, *args]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegExecError(
                f"FFmpeg 执行超时（{self.timeout}s）: {' '.join(args)}"
            ) from exc
        except OSError as exc:
            raise FFmpegExecError(f"FFmpeg 调用失败: {exc}") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-3:]
            raise FFmpegExecError(
                "FFmpeg 退出码 %s: %s" % (proc.returncode, " | ".join(tail)))
        return proc

    def check_available(self) -> bool:
        """快速探测 FFmpeg 是否可正常运行。"""
        try:
            self._run(["-version"])
            return True
        except WorkflowError:
            return False
        except Exception:  # pragma: no cover
            return False

    def probe(self, video_path: Path | str) -> VideoMeta:
        """解析视频元数据。

        优先使用 ``ffprobe``（随 FFmpeg 分发）输出 JSON；当 ffprobe
        不存在时回退到 ``ffmpeg -i`` 的 stderr 文本解析，保证仅依赖
        单个 ffmpeg 二进制的环境也能工作。
        """
        video = str(video_path)
        ffprobe = _sibling_ffprobe(self._ensure_binary())
        if ffprobe:
            return self._probe_with_ffprobe(ffprobe, video)
        return self._probe_with_stderr(video)

    def _probe_with_ffprobe(self, ffprobe: str, video: str) -> VideoMeta:
        """通过 ffprobe -print_format json 获取精确元数据。"""
        cmd = [ffprobe, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", video]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FFmpegExecError(f"ffprobe 执行失败: {exc}") from exc
        if proc.returncode != 0:
            raise FFmpegExecError("ffprobe 解析失败: "
                                  + (proc.stderr or "")[-200:])
        payload = json.loads(proc.stdout or "{}")
        return _meta_from_ffprobe(payload)

    def _probe_with_stderr(self, video: str) -> VideoMeta:
        """通过 ffmpeg -i 的 stderr 文本回退解析元数据。"""
        binary = self._ensure_binary()
        try:
            proc = subprocess.run([binary, "-hide_banner", "-i", video],
                                  capture_output=True, text=True,
                                  timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FFmpegExecError(f"FFmpeg 探测失败: {exc}") from exc
        return _meta_from_stderr(proc.stderr or "")

    def extract_frame(self, video_path: Path | str, at_seconds: float,
                      output_path: Path | str) -> Path:
        """在指定时间点抽取单帧为 PNG。

        :param at_seconds: 距视频开头的秒数，负值归零。
        :return: 实际写入的图片路径。
        """
        at_seconds = max(0.0, float(at_seconds))
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._run(["-hide_banner", "-loglevel", "error", "-ss",
                   f"{at_seconds:.3f}", "-i", str(video_path),
                   "-frames:v", "1", "-y", str(output)])
        if not output.is_file() or output.stat().st_size == 0:
            raise FFmpegExecError(f"抽帧失败，输出文件无效: {output}")
        return output

    def sample_frames(self, video_path: Path | str, sample_fps: float,
                      output_dir: Path | str,
                      limit: Optional[int] = None) -> List[Path]:
        """按采样帧率均匀抽帧到目录，文件名按序号排序。

        :param sample_fps: 采样帧率（fps），例如 0.5 表示每 2 秒一帧。
        :param limit: 最多抽取帧数，None 不限制。
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(output_dir / "sample_%06d.png")
        args = ["-hide_banner", "-loglevel", "error", "-i", str(video_path),
                "-vf", f"fps={sample_fps}"]
        if limit:
            args += ["-frames:v", str(int(limit))]
        args += ["-y", pattern]
        self._run(args)
        frames = sorted(output_dir.glob("sample_*.png"))
        if not frames:
            raise FFmpegExecError("采样抽帧未产生任何图片")
        return frames


def _sibling_ffprobe(ffmpeg_path: str) -> Optional[str]:
    """查找与 ffmpeg 同目录的 ffprobe（兼容 Windows 的 .exe 后缀）。

    同目录找不到时再回退到系统 PATH，均不存在返回 None，调用方会改用
    ``ffmpeg -i`` 文本解析。
    """
    base = Path(ffmpeg_path)
    for name in ("ffprobe", "ffprobe.exe"):
        candidate = base.with_name(name)
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def _parse_fps(raw: str) -> float:
    """把 ``30000/1001`` 或 ``25`` 形式的帧率字符串换算为浮点数。"""
    raw = raw.strip()
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        denominator_f = float(denominator)
        if denominator_f == 0:
            return 0.0
        return float(numerator) / denominator_f
    return float(raw or 0)


def _meta_from_ffprobe(payload: dict) -> VideoMeta:
    """从 ffprobe JSON 结构构建 :class:`VideoMeta`。"""
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams
                         if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise FFmpegExecError("文件中不存在视频流")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(video_stream.get("duration")
                     or payload.get("format", {}).get("duration") or 0)
    fps = _parse_fps(video_stream.get("r_frame_rate", "0/1"))
    return VideoMeta(
        duration=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        fps=fps,
        codec=video_stream.get("codec_name", "unknown"),
        bit_rate=int(video_stream.get("bit_rate")
                     or payload.get("format", {}).get("bit_rate") or 0),
        has_audio=has_audio,
    )


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(
    r"Video:\s*(\w+).*?,\s*(\d{2,5})x(\d{2,5}).*?"
    r"(\d+(?:\.\d+)?)\s*fps")


def _meta_from_stderr(text: str) -> VideoMeta:
    """从 ffmpeg -i 的 stderr 文本回退构建元数据。"""
    duration_match = _DURATION_RE.search(text)
    video_match = _VIDEO_RE.search(text)
    if video_match is None:
        raise FFmpegExecError("无法识别视频流，文件可能已损坏")
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        duration = 0.0
    codec, width, height, fps = video_match.groups()
    return VideoMeta(duration=duration, width=int(width),
                     height=int(height), fps=float(fps), codec=codec,
                     has_audio="Audio:" in text)
