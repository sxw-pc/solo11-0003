"""KeyframeEngine 关键帧算法单元测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.core.ffmpeg_runner import FFmpegRunner
from app.core.keyframe_engine import (FrameSample, KeyframeEngine,
                                      SENSITIVITY_PROFILES)
from tests.conftest import fixture_path


def _sample_frames(runner: FFmpegRunner, name: str, tmp_path,
                   fps: float = 1.0):
    """采样 fixture 视频并封装为 FrameSample 列表。"""
    frames = runner.sample_frames(fixture_path(name), fps, tmp_path,
                                  limit=300)
    return [FrameSample(path=p, index=i, timestamp=round(i / fps, 3))
            for i, p in enumerate(frames)]


def test_invalid_sensitivity_raises(runner, tmp_path):
    """非法灵敏度档位抛出 ValueError。"""
    with pytest.raises(ValueError):
        KeyframeEngine(sensitivity="extreme")


def test_empty_samples_return_empty():
    """空输入直接返回空列表。"""
    assert KeyframeEngine().detect([]) == []


def test_static_video_single_keyframe(runner, tmp_path):
    """静态视频任意档位都只保留 1 个关键帧。"""
    samples = _sample_frames(runner, "mock_static.mp4", tmp_path)
    for sensitivity in SENSITIVITY_PROFILES:
        engine = KeyframeEngine(sensitivity, filter_invalid=True)
        keyframes = engine.detect(samples)
        assert len(keyframes) == 1
        assert keyframes[0].timestamp == 0.0


def test_moving_video_keyframes(runner, tmp_path):
    """移动视频应筛选出多个关键帧，且时间戳单调递增。"""
    samples = _sample_frames(runner, "mock_moving.mp4", tmp_path)
    engine = KeyframeEngine("medium", filter_invalid=True)
    keyframes = engine.detect(samples)
    assert len(keyframes) >= 2
    timestamps = [kf.timestamp for kf in keyframes]
    assert timestamps == sorted(timestamps)
    assert all(Path(kf.path).is_file() for kf in keyframes)


def test_sensitivity_ordering(runner, tmp_path):
    """高灵敏度关键帧数量不少于低灵敏度。"""
    samples = _sample_frames(runner, "mock_moving.mp4", tmp_path)
    high = len(KeyframeEngine("high").detect(samples))
    low = len(KeyframeEngine("low").detect(samples))
    assert high >= low


def test_night_video_low_light(runner, tmp_path):
    """夜间低照度视频不被误判为全黑，可产出关键帧或回退首帧。"""
    samples = _sample_frames(runner, "mock_night.mp4", tmp_path)
    keyframes = KeyframeEngine("high").detect(samples)
    assert len(keyframes) >= 1


def test_all_black_filtered(runner, tmp_path):
    """开启无效帧过滤时纯黑视频无有效帧。"""
    samples = _sample_frames(runner, "edge_black.mp4", tmp_path)
    assert KeyframeEngine("medium", filter_invalid=True).detect(
        samples) == []


def test_all_white_filtered(runner, tmp_path):
    """开启无效帧过滤时纯白视频无有效帧。"""
    samples = _sample_frames(runner, "edge_white.mp4", tmp_path)
    assert KeyframeEngine("medium", filter_invalid=True).detect(
        samples) == []


def test_filter_disabled_keeps_invalid(runner, tmp_path):
    """关闭无效帧过滤后纯黑帧参与筛选并回退保留首帧。"""
    samples = _sample_frames(runner, "edge_black.mp4", tmp_path)
    keyframes = KeyframeEngine("medium",
                               filter_invalid=False).detect(samples)
    assert len(keyframes) == 1
    assert keyframes[0].reason in ("fallback_first_frame",
                                   "first_valid_frame")


def test_frame_diff_identical_images(tmp_path):
    """完全相同的两帧帧差为 0、直方图距离为 0。"""
    image = Image.new("RGB", (64, 36), (120, 130, 140))
    path = tmp_path / "same.png"
    image.save(path)
    engine = KeyframeEngine()
    feat = engine.extract_features(path)
    assert engine.frame_diff(feat, feat) == 0.0
    assert engine.hist_distance(feat, feat) == 0.0


def test_frame_diff_different_images(tmp_path):
    """明暗差异明显的两帧帧差应大于零。"""
    dark = tmp_path / "dark.png"
    bright = tmp_path / "bright.png"
    Image.new("RGB", (64, 36), (10, 10, 10)).save(dark)
    Image.new("RGB", (64, 36), (240, 240, 240)).save(bright)
    engine = KeyframeEngine()
    diff = engine.frame_diff(engine.extract_features(dark),
                             engine.extract_features(bright))
    assert diff > 0.5


def test_invalid_frame_judgement():
    """全黑/全白判定边界正确。"""
    engine = KeyframeEngine()
    invalid_black, reasons_black = engine._judge_invalid(3.0, 2.0)
    invalid_white, reasons_white = engine._judge_invalid(252.0, 1.0)
    valid, reasons = engine._judge_invalid(120.0, 40.0)
    assert invalid_black and "all_black" in reasons_black
    assert invalid_white and "all_white" in reasons_white
    assert not valid and reasons == []


def test_keyframe_to_dict(runner, tmp_path):
    """关键帧序列化包含全部约定字段。"""
    samples = _sample_frames(runner, "mock_moving.mp4", tmp_path)
    keyframe = KeyframeEngine("high").detect(samples)[0]
    payload = keyframe.to_dict()
    assert set(payload) == {"path", "index", "timestamp", "diff_score",
                            "hist_distance", "reason"}


def test_histogram_normalized(tmp_path):
    """色相直方图归一化后总和为 1。"""
    path = tmp_path / "color.png"
    array = np.zeros((32, 32, 3), dtype=np.uint8)
    array[:, :, 0] = 200
    Image.fromarray(array).save(path)
    hist = KeyframeEngine().extract_features(path).hist
    assert hist.sum() == pytest.approx(1.0)
