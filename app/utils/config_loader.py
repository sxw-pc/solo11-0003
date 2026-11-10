"""本地配置加载与环境变量解析。

配置优先级：环境变量 > 默认值。所有路径默认指向项目根目录下的 ``data``
目录，保证单用户本地运行、数据不出设备。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _project_root() -> Path:
    """返回项目根目录（app 包的上两级目录）。"""
    return Path(__file__).resolve().parents[2]


def _detect_ffmpeg() -> Optional[str]:
    """探测本地 FFmpeg 可执行文件路径。

    依次检查环境变量 ``MONITOR_FFMPEG``、系统 PATH，以及可选的
    imageio-ffmpeg 自带静态二进制（仅用于无系统 FFmpeg 的开发环境，
    不属于项目运行依赖）。
    """
    env_path = os.environ.get("MONITOR_FFMPEG")
    if env_path and Path(env_path).is_file():
        return env_path
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path
    try:  # pragma: no cover - 仅开发环境兜底分支
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(bundled).is_file():
            return bundled
    except Exception:  # pragma: no cover
        return None
    return None


@dataclass
class AppConfig:
    """应用运行配置。

    :param data_dir: 本地数据根目录，包含 db/temp/output 三个子目录。
    :param host: 服务监听地址，默认仅本地回环。
    :param port: 服务监听端口，默认 8000。
    :param ffmpeg_path: FFmpeg 可执行文件绝对路径。
    :param keep_temp_seconds: 临时文件保留秒数，0 表示任务结束立即清理。
    :param max_upload_mb: 预留字段，本项目无上传逻辑，仅作展示。
    """

    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    ffmpeg_path: Optional[str] = None
    keep_temp_seconds: int = 0
    max_upload_mb: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def db_dir(self) -> Path:
        """SQLite 数据库文件目录。"""
        return self.data_dir / "db"

    @property
    def temp_dir(self) -> Path:
        """临时抽帧与中间缓存目录。"""
        return self.data_dir / "temp"

    @property
    def output_dir(self) -> Path:
        """默认输出文件目录。"""
        return self.data_dir / "output"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件完整路径。"""
        return self.db_dir / "monitor_workshop.db"

    def ensure_dirs(self) -> None:
        """创建全部本地数据目录（如已存在则跳过）。"""
        for directory in (self.data_dir, self.db_dir, self.temp_dir,
                          self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)


def load_config(data_dir: Optional[Path | str] = None) -> AppConfig:
    """根据环境变量与默认值加载配置。

    :param data_dir: 显式指定的数据目录（测试场景使用），为 None 时
        读取环境变量 ``MONITOR_DATA_DIR``，否则使用项目内 ``data`` 目录。
    :return: 填充完成的 :class:`AppConfig` 实例。
    """
    if data_dir is None:
        data_dir = Path(os.environ.get("MONITOR_DATA_DIR",
                                       _project_root() / "data"))
    else:
        data_dir = Path(data_dir)
    config = AppConfig(
        data_dir=data_dir.resolve(),
        host=os.environ.get("MONITOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("MONITOR_PORT", "8000")),
        ffmpeg_path=_detect_ffmpeg(),
        keep_temp_seconds=int(os.environ.get("MONITOR_KEEP_TEMP", "0")),
    )
    config.ensure_dirs()
    return config
