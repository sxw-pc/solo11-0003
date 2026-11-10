"""单任务处理流水线：校验 → 抽帧 → 关键帧 → 缩略图 → 拼接 → 清理。

该模块只负责纯本地计算与文件操作，不依赖 Web 框架，可被任务队列与
系统自检复用。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from ..db.sqlite_client import SqliteClient
from ..db.table_schema import (STATUS_CANCELLED, STATUS_FAILED,
                               STATUS_RUNNING, STATUS_SUCCESS)
from ..utils import file_helper
from ..utils.time_helper import now_iso
from .errors import DiskSpaceError, WorkflowError
from .ffmpeg_runner import FFmpegRunner
from .keyframe_engine import FrameSample, KeyframeEngine
from .thumb_builder import ThumbBuilder
from .video_validator import VideoValidator

# 单任务最多处理帧数，防止异常长视频占满本地资源
MAX_FRAMES = 300
# 智能模式各档位对应的默认采样帧率
SAMPLE_FPS_BY_SENSITIVITY = {"high": 2.0, "medium": 1.0, "low": 0.5}


@dataclass
class ProcessOptions:
    """任务处理参数（由 API 层参数转换而来）。"""

    mode: str = "interval"
    interval_seconds: int = 10
    sensitivity: str = "medium"
    filter_invalid: bool = True
    thumb_width: int = 320
    orientation: str = "vertical"
    keep_originals: bool = False


class TaskProcessor:
    """执行单个监控视频任务的完整处理流程。"""

    def __init__(self, db: SqliteClient, runner: FFmpegRunner,
                 config):
        """
        :param db: 已初始化的 SQLite 封装。
        :param runner: FFmpeg 调用封装。
        :param config: 应用配置（提供数据目录与临时保留策略）。
        """
        self.db = db
        self.runner = runner
        self.config = config
        self.validator = VideoValidator(runner)

    def process(self, task_id: str) -> Dict[str, Any]:
        """执行任务，返回成功结果字典；失败时落库后重新抛出异常。"""
        record = self.db.fetch_one("tasks", "id", task_id)
        if record is None:
            raise WorkflowError(f"任务不存在: {task_id}",
                                code="TASK_NOT_FOUND", http_status=404)
        if record.get("cancel_requested"):
            self._mark_cancelled(task_id)
            return {"task_id": task_id, "status": STATUS_CANCELLED}
        temp_dir = file_helper.create_task_temp_dir(
            self.config.temp_dir, task_id)
        try:
            self._mark_running(task_id)
            options = self._parse_options(record)
            result = self._validate_and_prepare(record, options)
            samples = self._extract_samples(record, options, temp_dir,
                                            result["meta"])
            keyframes = self._select_keyframes(options, samples)
            outputs = self._build_outputs(task_id, options, keyframes)
            self._finish_success(task_id, result, keyframes, outputs)
            return {"task_id": task_id, "status": STATUS_SUCCESS,
                    "keyframe_count": len(keyframes), **outputs}
        except Exception as exc:  # noqa: BLE001 - 统一落库错误信息
            self._mark_failed(task_id, exc)
            raise
        finally:
            self._cleanup_temp(temp_dir)

    # ---------- 阶段拆分 ----------

    def _validate_and_prepare(self, record: dict,
                              options: ProcessOptions) -> dict:
        """F1 视频校验与磁盘空间预检。"""
        validation = self.validator.validate(record["video_path"])
        estimated_frames = self._estimate_frames(options,
                                                 validation.meta.duration)
        required = file_helper.estimate_output_size(
            estimated_frames, options.thumb_width,
            round(options.thumb_width * validation.meta.height
                  / max(1, validation.meta.width)),
            options.keep_originals)
        if not file_helper.has_enough_space(self.config.output_dir,
                                            required):
            raise DiskSpaceError(
                "磁盘剩余空间不足，预估需要 %d 字节" % required)
        return {"meta": validation.meta,
                "metadata": validation.meta.to_dict()}

    def _extract_samples(self, record: dict, options: ProcessOptions,
                         temp_dir: Path, meta) -> List[FrameSample]:
        """按抽帧模式产出有序采样帧。"""
        sample_dir = temp_dir / "samples"
        if options.mode == "interval":
            return self._extract_interval(record["video_path"],
                                          options, meta, sample_dir)
        return self._extract_smart(record["video_path"], options, meta,
                                   sample_dir)

    def _extract_interval(self, video_path: str, options: ProcessOptions,
                          meta, sample_dir: Path) -> List[FrameSample]:
        """固定间隔抽帧：逐时间点精确抽帧。"""
        timestamps = build_interval_timestamps(meta.duration,
                                               options.interval_seconds)
        timestamps = timestamps[:MAX_FRAMES]
        samples: List[FrameSample] = []
        for index, ts in enumerate(timestamps):
            out = sample_dir / f"frame_{index:04d}.png"
            self.runner.extract_frame(video_path, ts, out)
            samples.append(FrameSample(path=out, index=index,
                                       timestamp=round(ts, 3)))
        return samples

    def _extract_smart(self, video_path: str, options: ProcessOptions,
                       meta, sample_dir: Path) -> List[FrameSample]:
        """智能模式：均匀采样后交给关键帧引擎筛选。"""
        fps = min(SAMPLE_FPS_BY_SENSITIVITY[options.sensitivity],
                  max(0.1, MAX_FRAMES / max(meta.duration, 1.0)))
        frames = self.runner.sample_frames(video_path, fps, sample_dir,
                                           limit=MAX_FRAMES)
        return [FrameSample(path=path, index=idx,
                            timestamp=round(idx / fps, 3))
                for idx, path in enumerate(frames)]

    def _select_keyframes(self, options: ProcessOptions,
                          samples: List[FrameSample]):
        """固定间隔模式全部保留；智能模式运行本地关键帧算法。"""
        if options.mode == "interval":
            return samples
        engine = KeyframeEngine(sensitivity=options.sensitivity,
                                filter_invalid=options.filter_invalid)
        return engine.detect(samples)

    def _build_outputs(self, task_id: str, options: ProcessOptions,
                       keyframes) -> Dict[str, Path]:
        """生成缩略图、时间戳叠加与摘要拼接长图。"""
        if not keyframes:
            raise WorkflowError("未筛选出任何有效关键帧（可能全为无效帧）",
                                code="NO_KEYFRAME")
        task_output = Path(self.config.output_dir) / task_id
        thumb_dir = task_output / "thumbnails"
        builder = ThumbBuilder(thumb_width=options.thumb_width,
                               orientation=options.orientation)
        paths = [kf.path for kf in keyframes]
        timestamps = [kf.timestamp for kf in keyframes]
        thumbs = builder.build_thumbnails(paths, timestamps, thumb_dir)
        montage = task_output / f"montage_{options.orientation}.jpg"
        builder.build_montage(thumbs, montage)
        outputs = {"output_dir": task_output, "montage_path": montage,
                   "thumbnails": [item.path for item in thumbs]}
        if options.keep_originals:
            outputs["keyframe_dir"] = self._copy_originals(
                keyframes, task_output / "keyframes")
        return outputs

    @staticmethod
    def _copy_originals(keyframes, target_dir: Path) -> Path:
        """按需保存关键帧原图到输出目录。"""
        target_dir.mkdir(parents=True, exist_ok=True)
        for kf in keyframes:
            shutil.copy2(kf.path, target_dir / Path(kf.path).name)
        return target_dir

    # ---------- 状态落库 ----------

    def _mark_running(self, task_id: str) -> None:
        """任务进入处理中状态。"""
        self.db.update("tasks", "id", task_id,
                       {"status": STATUS_RUNNING, "started_at": now_iso()})

    def _finish_success(self, task_id: str, validation: dict,
                        keyframes, outputs: Dict[str, Any]) -> None:
        """写入成功状态、关键帧索引与输出路径。

        临时抽帧文件在任务结束后会被回收，因此关键帧索引中记录的
        ``path`` 统一指向持久化的缩略图，避免历史记录出现失效路径。
        """
        thumbs = outputs.get("thumbnails", [])
        payload = []
        for index, kf in enumerate(keyframes):
            item = (kf.to_dict() if hasattr(kf, "to_dict")
                    else {"path": str(kf.path), "index": kf.index,
                          "timestamp": kf.timestamp})
            if index < len(thumbs):
                item["path"] = str(thumbs[index])
            payload.append(item)
        self.db.update("tasks", "id", task_id, {
            "status": STATUS_SUCCESS,
            "finished_at": now_iso(),
            "keyframe_count": len(keyframes),
            "output_dir": str(outputs["output_dir"]),
            "montage_path": str(outputs["montage_path"]),
            "video_metadata": self.db.dumps(validation["metadata"]),
            "keyframes": self.db.dumps(payload),
            "error_code": None,
            "error_message": None,
        })

    def _mark_failed(self, task_id: str, exc: Exception) -> None:
        """失败任务记录标准化错误码与原因。"""
        code = getattr(exc, "code", "INTERNAL_ERROR")
        self.db.update("tasks", "id", task_id, {
            "status": STATUS_FAILED,
            "finished_at": now_iso(),
            "error_code": code,
            "error_message": str(exc)[:500],
        })

    def _mark_cancelled(self, task_id: str) -> None:
        """排队期间被取消的任务直接置为已取消。"""
        self.db.update("tasks", "id", task_id,
                       {"status": STATUS_CANCELLED,
                        "finished_at": now_iso()})

    def _cleanup_temp(self, temp_dir: Path) -> None:
        """F8 任务结束后的临时资源回收（受保留周期配置影响）。"""
        file_helper.cleanup_stale_temp(
            temp_dir.parent, keep_seconds=self.config.keep_temp_seconds)

    @staticmethod
    def _estimate_frames(options: ProcessOptions,
                         duration: float) -> int:
        """预估任务帧数，用于磁盘空间预检。"""
        if options.mode == "interval":
            return max(1, int(duration // max(1, options.interval_seconds)))
        fps = SAMPLE_FPS_BY_SENSITIVITY[options.sensitivity]
        return max(1, int(duration * fps))

    @staticmethod
    def _parse_options(record: dict) -> ProcessOptions:
        """从数据库 JSON 参数还原处理选项。"""
        params = SqliteClient.loads(record.get("params"), {})
        return ProcessOptions(
            mode=params.get("mode", "interval"),
            interval_seconds=int(params.get("interval_seconds", 10)),
            sensitivity=params.get("sensitivity", "medium"),
            filter_invalid=bool(params.get("filter_invalid", True)),
            thumb_width=int(params.get("thumb_width", 320)),
            orientation=params.get("orientation", "vertical"),
            keep_originals=bool(params.get("keep_originals", False)))


def build_interval_timestamps(duration: float,
                              interval_seconds: int) -> List[float]:
    """生成固定间隔抽帧时间点序列，始终包含 0 秒首帧。"""
    if duration <= 0:
        return [0.0]
    timestamps: List[float] = [0.0]
    moment = float(interval_seconds)
    while moment < duration - 0.05:
        timestamps.append(round(moment, 3))
        moment += interval_seconds
    return timestamps
