"""离线环境一键自检（F7）。

使用 tests/fixtures 下的本地模拟视频，在独立临时目录中跑通完整处理
流程，自检结束自动清理全部临时数据，不污染正式数据目录。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..db.sqlite_client import SqliteClient
from ..utils.config_loader import AppConfig
from ..utils.file_helper import cleanup_stale_temp
from .ffmpeg_runner import FFmpegRunner
from .task_processor import ProcessOptions, TaskProcessor
from .task_queue import TaskQueue

# 自检使用的模拟视频（含移动目标，应稳定产出关键帧）
SELF_CHECK_FIXTURE = "mock_moving.mp4"


def _project_root() -> Path:
    """定位项目根目录。"""
    return Path(__file__).resolve().parents[2]


def run_self_check(ffmpeg_path: str | None) -> Dict[str, Any]:
    """执行全链路自检并返回标准化报告。

    :param ffmpeg_path: 本地 FFmpeg 可执行文件路径。
    :return: 包含 passed/checks/summary 的报告字典。
    """
    checks: List[dict] = []
    fixture = _project_root() / "tests" / "fixtures" / SELF_CHECK_FIXTURE
    checks.append(_check_fixture(fixture))
    runner = FFmpegRunner(ffmpeg_path)
    checks.append(_check_ffmpeg(runner))
    if not all(item["passed"] for item in checks):
        return _build_report(checks)
    report = _run_pipeline(runner, fixture)
    checks.extend(report)
    return _build_report(checks)


def _check_fixture(fixture: Path) -> dict:
    """检查自检素材是否随项目交付存在。"""
    return {
        "name": "fixture_ready",
        "passed": fixture.is_file(),
        "detail": str(fixture),
        "message": "" if fixture.is_file() else "自检素材缺失，请先执行 "
                                                "make init-fixture",
    }


def _check_ffmpeg(runner: FFmpegRunner) -> dict:
    """检查 FFmpeg 二进制可正常调用。"""
    available = runner.check_available()
    return {"name": "ffmpeg_available", "passed": available,
            "detail": runner.ffmpeg_path or "",
            "message": "" if available else "FFmpeg 不可用"}


def _run_pipeline(runner: FFmpegRunner, fixture: Path) -> List[dict]:
    """在临时目录中执行完整任务流程并校验产物。"""
    checks: List[dict] = []
    temp_root = Path(tempfile.mkdtemp(prefix="monitor_selfcheck_"))
    try:
        config = AppConfig(data_dir=temp_root)
        config.ensure_dirs()
        db = SqliteClient(config.db_path)
        checks.append(_check_database(db))
        processor = TaskProcessor(db, runner, config)
        queue = TaskQueue(db, processor)
        task_id = queue.submit(
            str(fixture),
            ProcessOptions(mode="smart", sensitivity="medium",
                           thumb_width=320,
                           orientation="vertical").__dict__)
        queue.start()
        record = _wait_finish(queue, task_id)
        checks.append(_check_task(record))
        checks.append(_check_outputs(record))
        queue.shutdown(wait=True)
        cleanup_stale_temp(config.temp_dir)
        checks.append(_check_temp_cleanup(config))
    finally:
        # 仅清理自检自身创建的临时根目录，绝不触碰系统临时目录中的其他内容
        if temp_root.exists():
            import shutil

            shutil.rmtree(temp_root, ignore_errors=True)
    return checks


def _check_database(db: SqliteClient) -> dict:
    """校验 SQLite 读写能力。"""
    db.insert("app_meta", {"key": "selfcheck", "value": "ok"})
    row = db.fetch_one("app_meta", "key", "selfcheck")
    passed = bool(row and row.get("value") == "ok")
    return {"name": "database_read_write", "passed": passed,
            "detail": "sqlite3 local file",
            "message": "" if passed else "数据库读写异常"}


def _wait_finish(queue: TaskQueue, task_id: str,
                 timeout: float = 60.0) -> dict:
    """轮询等待任务进入终态。"""
    import time

    deadline = time.time() + timeout
    record = queue.get_status(task_id)
    while record["status"] in ("pending", "running") \
            and time.time() < deadline:
        time.sleep(0.2)
        record = queue.get_status(task_id)
    return record


def _check_task(record: dict) -> dict:
    """校验任务执行状态与关键帧数量。"""
    passed = record["status"] == "success" \
        and int(record.get("keyframe_count", 0)) > 0
    return {
        "name": "pipeline_execution",
        "passed": passed,
        "detail": f"status={record['status']}, "
                  f"keyframes={record.get('keyframe_count', 0)}",
        "message": record.get("error_message") or "",
    }


def _check_outputs(record: dict) -> dict:
    """校验拼接图与输出目录真实存在且非空。"""
    montage = Path(record.get("montage_path") or "")
    output_dir = Path(record.get("output_dir") or "")
    passed = montage.is_file() and montage.stat().st_size > 0 \
        and output_dir.is_dir()
    return {"name": "montage_output", "passed": passed,
            "detail": str(montage),
            "message": "" if passed else "拼接图产物缺失或为空"}


def _check_temp_cleanup(config: AppConfig) -> dict:
    """校验自检临时抽帧文件已被回收。"""
    leftovers = list(config.temp_dir.glob("**/*"))
    passed = not leftovers
    return {"name": "temp_cleanup", "passed": passed,
            "detail": f"leftovers={len(leftovers)}",
            "message": "" if passed else "临时文件残留"}


def _build_report(checks: List[dict]) -> Dict[str, Any]:
    """汇总单项检查结果为标准化报告。"""
    passed = all(item["passed"] for item in checks)
    total = len(checks)
    success = sum(1 for item in checks if item["passed"])
    return {
        "passed": passed,
        "checks": checks,
        "summary": f"自检 {success}/{total} 项通过",
    }
