"""ThumbBuilder 缩略图与拼接单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.core.thumb_builder import (MAX_THUMB_WIDTH, MIN_THUMB_WIDTH,
                                    ThumbBuilder, ThumbItem)
from app.utils.time_helper import format_timestamp


def _make_frames(tmp_path, count=4, size=(640, 360)):
    """生成若干纯色测试帧与递增时间戳。"""
    paths, timestamps = [], []
    colors = [(200, 60, 60), (60, 200, 60), (60, 60, 200), (200, 200, 60)]
    for index in range(count):
        path = tmp_path / f"src_{index}.png"
        Image.new("RGB", size, colors[index % len(colors)]).save(path)
        paths.append(path)
        timestamps.append(index * 10.0)
    return paths, timestamps


def test_invalid_width_too_small():
    """宽度低于下限抛 ValueError。"""
    with pytest.raises(ValueError):
        ThumbBuilder(thumb_width=MIN_THUMB_WIDTH - 1)


def test_invalid_width_too_large():
    """宽度高于上限抛 ValueError。"""
    with pytest.raises(ValueError):
        ThumbBuilder(thumb_width=MAX_THUMB_WIDTH + 1)


def test_invalid_orientation():
    """非法拼接方向抛 ValueError。"""
    with pytest.raises(ValueError):
        ThumbBuilder(orientation="diagonal")


def test_build_thumbnails_size_and_count(tmp_path):
    """缩略图数量与输入一致，宽度统一为配置值。"""
    paths, timestamps = _make_frames(tmp_path)
    builder = ThumbBuilder(thumb_width=320)
    items = builder.build_thumbnails(paths, timestamps,
                                     tmp_path / "thumbs")
    assert len(items) == 4
    for item in items:
        assert item.path.is_file()
        with Image.open(item.path) as image:
            assert image.width == 320
            assert image.height == 180


def test_thumbnail_keeps_aspect_ratio(tmp_path):
    """非 16:9 源帧按比例计算高度，不发生拉伸。"""
    paths, timestamps = _make_frames(tmp_path, count=1, size=(320, 240))
    items = ThumbBuilder(thumb_width=160).build_thumbnails(
        paths, timestamps, tmp_path / "thumbs")
    with Image.open(items[0].path) as image:
        assert image.size == (160, 120)


def test_build_vertical_montage(tmp_path):
    """纵向拼接图高度为各缩略图高度加分隔线。"""
    paths, timestamps = _make_frames(tmp_path)
    builder = ThumbBuilder(thumb_width=320, orientation="vertical")
    items = builder.build_thumbnails(paths, timestamps,
                                     tmp_path / "thumbs")
    montage = builder.build_montage(items, tmp_path / "v.jpg")
    assert montage.is_file() and montage.stat().st_size > 0
    with Image.open(montage) as image:
        assert image.width == 320
        assert image.height == 4 * 180 + 3 * 4


def test_build_horizontal_montage(tmp_path):
    """横向拼接图宽度为各缩略图宽度加分隔线。"""
    paths, timestamps = _make_frames(tmp_path)
    builder = ThumbBuilder(thumb_width=160, orientation="horizontal")
    items = builder.build_thumbnails(paths, timestamps,
                                     tmp_path / "thumbs")
    montage = builder.build_montage(items, tmp_path / "h.jpg")
    with Image.open(montage) as image:
        assert image.height == 90
        assert image.width == 4 * 160 + 3 * 4


def test_empty_montage_raises(tmp_path):
    """空缩略图列表不能拼接。"""
    with pytest.raises(ValueError):
        ThumbBuilder().build_montage([], tmp_path / "empty.jpg")


def test_mismatched_lengths_raise(tmp_path):
    """帧路径与时间戳数量不一致时抛 ValueError。"""
    paths, timestamps = _make_frames(tmp_path)
    with pytest.raises(ValueError):
        ThumbBuilder().build_thumbnails(paths, timestamps[:-1],
                                        tmp_path / "x")


def test_thumb_item_serialization():
    """ThumbItem 序列化字段完整。"""
    item = ThumbItem(path=Path("/tmp/a.jpg"), timestamp=12.5, index=3)
    payload = item.to_dict()
    assert payload["index"] == 3 and payload["timestamp"] == 12.5


def test_timestamp_text_format():
    """时间戳格式化覆盖小时与毫秒位。"""
    assert format_timestamp(0) == "00:00:00.000"
    assert format_timestamp(83.5) == "00:01:23.500"
    assert format_timestamp(-1) == "00:00:00.000"
