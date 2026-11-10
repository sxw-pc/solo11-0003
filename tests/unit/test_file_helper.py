"""file_helper 与 time_helper 单元测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.utils import file_helper
from app.utils.file_helper import (PathValidationError,
                                   cleanup_stale_temp,
                                   create_task_temp_dir,
                                   estimate_output_size,
                                   get_free_space_gb, has_enough_space,
                                   remove_path, validate_input_path)
from app.utils.time_helper import (add_seconds, frame_to_seconds,
                                   format_timestamp, is_within_range,
                                   now_iso, parse_iso, to_iso)


def test_validate_absolute_path(tmp_path):
    """合法绝对路径通过校验并返回解析后的 Path。"""
    video = tmp_path / "cam.mp4"
    video.write_bytes(b"x" * 200)
    resolved = validate_input_path(str(video))
    assert resolved == video.resolve()


def test_validate_rejects_empty():
    """空路径与纯空白路径被拒绝。"""
    for raw in ("", "   "):
        with pytest.raises(PathValidationError):
            validate_input_path(raw)


def test_validate_rejects_relative():
    """相对路径被拒绝。"""
    with pytest.raises(PathValidationError):
        validate_input_path("data/video.mp4")


def test_validate_rejects_traversal(tmp_path):
    """包含 .. 的路径被拒绝。"""
    with pytest.raises(PathValidationError):
        validate_input_path(str(tmp_path / ".." / "video.mp4"))


def test_validate_rejects_suffix(tmp_path):
    """不支持的后缀被拒绝。"""
    doc = tmp_path / "doc.pdf"
    doc.write_text("x", encoding="utf-8")
    with pytest.raises(PathValidationError):
        validate_input_path(str(doc))


def test_validate_rejects_missing(tmp_path):
    """不存在的文件被拒绝。"""
    with pytest.raises(PathValidationError):
        validate_input_path(str(tmp_path / "missing.mp4"))


def test_validate_rejects_directory(tmp_path):
    """目录路径不是普通文件，被拒绝。"""
    with pytest.raises(PathValidationError):
        validate_input_path(str(tmp_path))


def test_create_and_remove_task_temp_dir(tmp_path):
    """任务临时目录创建后可整体删除。"""
    task_dir = create_task_temp_dir(tmp_path, "task-1")
    assert task_dir.is_dir()
    (task_dir / "a.tmp").write_text("x", encoding="utf-8")
    assert remove_path(task_dir) is True
    assert not task_dir.exists()
    assert remove_path(task_dir) is True  # 删除不存在路径幂等


def test_cleanup_stale_temp_immediate(tmp_path):
    """keep_seconds=0 时立即清理全部残留。"""
    base = tmp_path / "temp"
    for name in ("a", "b"):
        directory = create_task_temp_dir(base, name)
        (directory / "f").write_text("x", encoding="utf-8")
    assert cleanup_stale_temp(base, keep_seconds=0) == 2
    assert not any(base.iterdir())


def test_cleanup_stale_temp_with_retention(tmp_path):
    """保留周期内的新文件不应被清理。"""
    base = tmp_path / "temp"
    directory = create_task_temp_dir(base, "fresh")
    assert cleanup_stale_temp(base, keep_seconds=3600) == 0
    assert directory.exists()


def test_cleanup_missing_dir(tmp_path):
    """清理不存在的目录返回 0 而不是报错。"""
    assert cleanup_stale_temp(tmp_path / "nope") == 0


def test_disk_space_helpers(tmp_path):
    """磁盘空间查询返回正数，空间预检逻辑正确。"""
    assert get_free_space_gb(tmp_path) > 0
    assert has_enough_space(tmp_path, 1) is True
    assert has_enough_space(tmp_path, 10 ** 18) is False


def test_estimate_output_size():
    """输出体积估算随帧数与原图保留开关变化。"""
    small = estimate_output_size(2, 320, 180, False)
    large = estimate_output_size(20, 320, 180, True)
    assert large > small > 0


def test_remove_file(tmp_path):
    """单文件删除正常工作。"""
    path = tmp_path / "one.tmp"
    path.write_text("x", encoding="utf-8")
    assert remove_path(path) is True and not path.exists()


def test_format_timestamp():
    """时间戳格式化覆盖常规与边界值。"""
    assert format_timestamp(3661.005) == "01:01:01.005"
    assert format_timestamp(0) == "00:00:00.000"


def test_frame_to_seconds():
    """帧序号换算秒数，非法帧率回退为 1fps。"""
    assert frame_to_seconds(30, 15.0) == 2.0
    assert frame_to_seconds(5, 0) == 5.0


def test_iso_round_trip():
    """ISO 时间生成、解析、往返转换一致。"""
    raw = now_iso()
    parsed = parse_iso(raw)
    assert isinstance(parsed, datetime)
    assert to_iso(parsed) == raw
    assert parse_iso("bad-value") is None
    assert parse_iso(None) is None
    assert to_iso(None) is None


def test_add_seconds():
    """ISO 时间叠加秒数正确。"""
    result = add_seconds("2026-11-10T10:00:00", 90)
    assert result == "2026-11-10T10:01:30"


def test_is_within_range():
    """时间范围过滤的开闭区间与空边界行为。"""
    value = "2026-11-10T10:00:00"
    assert is_within_range(value, "2026-11-10T09:00:00",
                           "2026-11-10T11:00:00")
    assert not is_within_range(value, "2026-11-10T10:30:00", None)
    assert is_within_range(value, None, None)
    assert not is_within_range("invalid", None, None)


def test_make_temp_text_file():
    """临时文本文件写入后可读且默认在系统临时目录。"""
    path = file_helper.make_temp_text_file("hello")
    try:
        assert path.is_file() and path.read_text() == "hello"
    finally:
        remove_path(path)


def test_allowed_suffixes_content():
    """允许后缀严格限定为 mp4/avi/mov。"""
    assert {".mp4", ".avi", ".mov"} == file_helper.ALLOWED_SUFFIXES
