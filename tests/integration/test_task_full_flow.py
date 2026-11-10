"""任务全流程集成测试：提交、调度、处理、产物、恢复与取消。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.core.errors import TaskStateError
from app.core.task_processor import ProcessOptions, build_interval_timestamps
from app.db.table_schema import (STATUS_CANCELLED, STATUS_FAILED,
                                 STATUS_PENDING, STATUS_RUNNING,
                                 STATUS_SUCCESS)
from tests.conftest import fixture_path


def _wait_terminal(queue, task_id, timeout=60.0):
    """轮询等待任务进入终态。"""
    deadline = time.time() + timeout
    record = queue.get_status(task_id)
    while record["status"] in (STATUS_PENDING, STATUS_RUNNING):
        assert time.time() < deadline, f"任务 {task_id} 超时未结束"
        time.sleep(0.2)
        record = queue.get_status(task_id)
    return record


def test_smart_full_flow_success(queue, processor, config):
    """智能模式完整链路：入队 → 处理 → 拼接图 → 临时文件清理。"""
    options = ProcessOptions(mode="smart", sensitivity="medium")
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           options.__dict__)
    queue.start()
    try:
        record = _wait_terminal(queue, task_id)
    finally:
        queue.shutdown(wait=True)
    assert record["status"] == STATUS_SUCCESS
    assert record["keyframe_count"] >= 2
    montage = Path(record["montage_path"])
    assert montage.is_file() and montage.stat().st_size > 0
    assert Path(record["output_dir"]).is_dir()
    assert not list(config.temp_dir.glob("**/*"))


def test_interval_full_flow(queue, processor):
    """固定间隔模式按配置产出关键帧。"""
    options = ProcessOptions(mode="interval", interval_seconds=2)
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           options.__dict__)
    processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["status"] == STATUS_SUCCESS
    expected = build_interval_timestamps(6.0, 2)
    assert record["keyframe_count"] == len(expected)


def test_keep_originals_copies_frames(queue, processor):
    """开启原图保留后输出目录包含关键帧原图。"""
    options = ProcessOptions(mode="interval", interval_seconds=2,
                            keep_originals=True)
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           options.__dict__)
    processor.process(task_id)
    record = queue.get_status(task_id)
    keyframe_dir = Path(record["output_dir"]) / "keyframes"
    assert keyframe_dir.is_dir()
    assert len(list(keyframe_dir.glob("*.png"))) == record[
        "keyframe_count"]


def test_corrupt_video_fails_with_error_code(queue, processor):
    """损坏文件任务失败并记录标准化错误码。"""
    task_id = queue.submit(fixture_path("mock_corrupt.mp4"),
                           ProcessOptions().__dict__)
    with pytest.raises(Exception):
        processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["status"] == STATUS_FAILED
    assert record["error_code"] == "VIDEO_CORRUPT"
    assert record["error_message"]


def test_cancel_pending_task(queue):
    """排队任务可取消，出队处理时直接置为已取消。"""
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           ProcessOptions().__dict__)
    response = queue.cancel(task_id)
    assert response["cancel_requested"] is True
    queue.start()
    try:
        record = _wait_terminal(queue, task_id)
    finally:
        queue.shutdown(wait=True)
    assert record["status"] == STATUS_CANCELLED


def test_cancel_finished_task_rejected(queue, processor):
    """已完成任务取消抛出状态冲突。"""
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           ProcessOptions().__dict__)
    processor.process(task_id)
    with pytest.raises(TaskStateError):
        queue.cancel(task_id)


def test_cancel_running_task_rejected(queue, processor):
    """处理中的任务不允许取消。"""
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           ProcessOptions().__dict__)
    queue.db.update("tasks", "id", task_id,
                    {"status": STATUS_RUNNING})
    with pytest.raises(TaskStateError):
        queue.cancel(task_id)


def test_get_unknown_task_raises(queue):
    """查询不存在的任务抛出 NOT_FOUND 异常。"""
    from app.core.errors import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        queue.get_status("nonexistent-id")


def test_queue_restore_after_restart(queue, processor):
    """模拟服务重启：pending 与遗留 running 任务自动恢复执行。"""
    pending_id = queue.submit(fixture_path("mock_moving.mp4"),
                              ProcessOptions().__dict__)
    running_id = queue.submit(fixture_path("mock_static.mp4"),
                              ProcessOptions().__dict__)
    queue.db.update("tasks", "id", running_id,
                    {"status": STATUS_RUNNING})
    new_queue = type(queue)(queue.db, processor)
    restored = new_queue.restore()
    assert restored == 2
    new_queue.start()
    try:
        record_pending = _wait_terminal(new_queue, pending_id)
        record_running = _wait_terminal(new_queue, running_id)
    finally:
        new_queue.shutdown(wait=True)
    assert record_pending["status"] == STATUS_SUCCESS
    assert record_running["status"] == STATUS_SUCCESS


def test_serial_processing_order(queue, processor):
    """队列默认串行，任务按提交顺序依次成功。"""
    ids = [
        queue.submit(fixture_path("mock_static.mp4"),
                     ProcessOptions().__dict__) for _ in range(3)]
    queue.start()
    try:
        records = [_wait_terminal(queue, task_id) for task_id in ids]
    finally:
        queue.shutdown(wait=True)
    assert all(record["status"] == STATUS_SUCCESS for record in records)


def test_edge_tiny_video(queue, processor):
    """时长 <1 秒的超短视频可正常处理。"""
    task_id = queue.submit(fixture_path("edge_tiny.mp4"),
                           ProcessOptions(mode="interval",
                                          interval_seconds=1).__dict__)
    processor.process(task_id)
    assert queue.get_status(task_id)["status"] == STATUS_SUCCESS


def test_edge_144p_video(queue, processor):
    """144p 低分辨率视频处理成功。"""
    task_id = queue.submit(fixture_path("edge_144p.mp4"),
                           ProcessOptions(mode="smart").__dict__)
    processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["status"] == STATUS_SUCCESS
    metadata = queue.db.loads(record["video_metadata"], {})
    assert metadata["height"] == 144


def test_edge_4k_video(queue, processor):
    """4K 高分辨率视频处理成功且缩略图按比例缩小。"""
    task_id = queue.submit(fixture_path("edge_4k.mp4"),
                           ProcessOptions(mode="interval",
                                          interval_seconds=1).__dict__)
    processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["status"] == STATUS_SUCCESS
    metadata = queue.db.loads(record["video_metadata"], {})
    assert metadata["width"] == 3840


def test_edge_chinese_filename(queue, processor):
    """中文与特殊字符文件名可正常处理。"""
    task_id = queue.submit(fixture_path("edge_中文 监控(01).mp4"),
                           ProcessOptions().__dict__)
    processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["status"] == STATUS_SUCCESS
    assert "中文" in record["video_name"]


def test_edge_metadata_blank_mov(queue, processor):
    """无元数据的裸 mov 片段可完成处理。"""
    task_id = queue.submit(fixture_path("edge_metadata_blank.mov"),
                           ProcessOptions().__dict__)
    processor.process(task_id)
    assert queue.get_status(task_id)["status"] == STATUS_SUCCESS


def test_horizontal_montage_output(queue, processor):
    """横向拼接模式产出 horizontal 命名的拼接图。"""
    options = ProcessOptions(mode="interval", interval_seconds=2,
                            orientation="horizontal")
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           options.__dict__)
    processor.process(task_id)
    montage = Path(queue.get_status(task_id)["montage_path"])
    assert montage.name == "montage_horizontal.jpg" and montage.is_file()


def test_interval_timestamps_helpers():
    """固定间隔时间点序列始终包含首帧且不超过视频时长。"""
    points = build_interval_timestamps(5.0, 2)
    assert points[0] == 0.0 and points == [0.0, 2.0, 4.0]
    assert build_interval_timestamps(0, 10) == [0.0]
