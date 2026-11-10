"""pytest 公共夹具。

所有测试使用独立临时数据目录与临时数据库，全程离线，结束后自动清理，
不污染开发环境数据。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.core.ffmpeg_runner import FFmpegRunner
from app.core.task_processor import TaskProcessor
from app.core.task_queue import TaskQueue
from app.core.video_validator import VideoValidator
from app.db.sqlite_client import SqliteClient
from app.utils.config_loader import AppConfig, _detect_ffmpeg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"


def _ensure_fixtures() -> None:
    """fixture 缺失时自动本地生成，保证全新环境可直接跑测试。"""
    marker = FIXTURE_DIR / "mock_moving.mp4"
    if marker.is_file():
        return
    script = FIXTURE_DIR / "generate_fixtures.py"
    subprocess.run([sys.executable, str(script)], check=True,
                   capture_output=True)


_ensure_fixtures()


@pytest.fixture(scope="session")
def ffmpeg_path() -> str:
    """返回会话级 FFmpeg 路径，缺失则跳过依赖它的测试。"""
    path = _detect_ffmpeg()
    if not path:  # pragma: no cover - 环境兜底
        pytest.skip("本地 FFmpeg 不可用")
    return path


@pytest.fixture()
def config(tmp_path, ffmpeg_path) -> AppConfig:
    """每个用例独立的应用配置与数据目录。"""
    cfg = AppConfig(data_dir=tmp_path / "data",
                    ffmpeg_path=ffmpeg_path)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def db(config) -> SqliteClient:
    """独立临时 SQLite 客户端。"""
    return SqliteClient(config.db_path)


@pytest.fixture()
def runner(ffmpeg_path) -> FFmpegRunner:
    """FFmpeg 调用封装。"""
    return FFmpegRunner(ffmpeg_path)


@pytest.fixture()
def validator(runner) -> VideoValidator:
    """视频校验器。"""
    return VideoValidator(runner)


@pytest.fixture()
def processor(db, runner, config) -> TaskProcessor:
    """任务流水线处理器。"""
    return TaskProcessor(db, runner, config)


@pytest.fixture()
def queue(db, processor) -> TaskQueue:
    """串行任务队列（测试中手动控制启停）。"""
    return TaskQueue(db, processor)


@pytest.fixture()
def client(config):
    """FastAPI 测试客户端，应用生命周期完整生效。"""
    from fastapi.testclient import TestClient

    from app.__main__ import create_app

    app = create_app(config)
    with TestClient(app) as test_client:
        yield test_client
    app.state.queue.shutdown(wait=False)


def fixture_path(name: str) -> str:
    """返回 fixture 素材的绝对路径字符串。"""
    return str((FIXTURE_DIR / name).resolve())
