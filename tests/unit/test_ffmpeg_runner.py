"""FFmpegRunner 与 VideoValidator 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.errors import (FFmpegExecError, FFmpegNotFoundError,
                             VideoValidationError)
from app.core.ffmpeg_runner import FFmpegRunner, VideoMeta, _parse_fps
from tests.conftest import fixture_path


def test_check_available(runner):
    """FFmpeg 可用性探测应返回 True。"""
    assert runner.check_available() is True


def test_check_available_missing_binary(tmp_path):
    """二进制路径不存在时可用性为 False，调用抛标准化异常。"""
    missing = FFmpegRunner(str(tmp_path / "no-such-ffmpeg"))
    assert missing.check_available() is False
    with pytest.raises(FFmpegNotFoundError):
        missing.probe(fixture_path("mock_static.mp4"))


@pytest.mark.parametrize("name", [
    "mock_static.mp4", "mock_moving.mp4", "mock_night.mp4",
    "edge_tiny.mp4", "edge_144p.mp4", "edge_4k.mp4",
    "edge_中文 监控(01).mp4", "edge_metadata_blank.mov"])
def test_probe_metadata(runner, name):
    """多种素材的元数据探测应返回完整结构。"""
    meta = runner.probe(fixture_path(name))
    assert isinstance(meta, VideoMeta)
    assert meta.width > 0 and meta.height > 0
    assert meta.fps > 0 and meta.codec
    assert meta.duration >= 0
    payload = meta.to_dict()
    assert set(payload) == {
        "duration", "width", "height", "fps", "codec", "bit_rate",
        "has_audio"}


def test_probe_corrupt_video(runner):
    """损坏文件探测应抛出 FFmpeg 执行异常。"""
    with pytest.raises(FFmpegExecError):
        runner.probe(fixture_path("mock_corrupt.mp4"))


def test_extract_single_frame(runner, tmp_path):
    """定点抽帧应生成非空 PNG 文件。"""
    out = tmp_path / "frame.png"
    result = runner.extract_frame(fixture_path("mock_moving.mp4"), 1.0,
                                  out)
    assert result == out and out.is_file() and out.stat().st_size > 0


def test_extract_frame_negative_time(runner, tmp_path):
    """负时间点应归零处理而不是报错。"""
    out = tmp_path / "neg.png"
    runner.extract_frame(fixture_path("mock_static.mp4"), -5, out)
    assert out.is_file()


def test_sample_frames(runner, tmp_path):
    """均匀采样应按序号产出多张图片。"""
    frames = runner.sample_frames(fixture_path("mock_moving.mp4"), 1.0,
                                  tmp_path)
    assert len(frames) >= 3
    assert all(p.is_file() for p in frames)
    assert frames == sorted(frames)


def test_sample_frames_with_limit(runner, tmp_path):
    """limit 参数应严格限制抽帧数量。"""
    frames = runner.sample_frames(fixture_path("mock_moving.mp4"), 2.0,
                                  tmp_path, limit=2)
    assert len(frames) == 2


def test_parse_fps_variants():
    """帧率字符串解析覆盖分数与小数两种形式。"""
    assert _parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _parse_fps("25") == pytest.approx(25.0)
    assert _parse_fps("0/1") == 0.0


def test_validator_accepts_normal_video(validator):
    """正常视频通过完整校验并返回元数据。"""
    result = validator.validate(fixture_path("mock_moving.mp4"))
    assert result.path.name == "mock_moving.mp4"
    assert result.meta.duration > 0
    assert "metadata" in result.to_dict()


def test_validator_rejects_corrupt(validator):
    """损坏文件返回 VIDEO_CORRUPT 错误码。"""
    with pytest.raises(VideoValidationError) as exc:
        validator.validate(fixture_path("mock_corrupt.mp4"))
    assert exc.value.code == "VIDEO_CORRUPT"


def test_validator_rejects_relative_path(validator):
    """相对路径在入口即被拒绝。"""
    with pytest.raises(Exception):
        validator.validate("tests/fixtures/mock_static.mp4")


def test_validator_rejects_unsupported_suffix(validator, tmp_path):
    """不支持的容器后缀直接拒绝。"""
    fake = tmp_path / "notes.txt"
    fake.write_text("not a video", encoding="utf-8")
    with pytest.raises(Exception):
        validator.validate(str(fake))


def test_validator_rejects_empty_file(validator, tmp_path):
    """空文件应被识别为 VIDEO_EMPTY。"""
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"x" * 10)
    with pytest.raises(VideoValidationError) as exc:
        validator.validate(str(empty))
    assert exc.value.code == "VIDEO_EMPTY"


def test_validator_rejects_missing_file(validator):
    """不存在的文件抛出路径校验异常。"""
    with pytest.raises(Exception):
        validator.validate("/tmp/definitely-not-exists-12345.mp4")


def test_validator_avi_and_mov_allowed(validator):
    """mov 容器在允许列表内可正常校验。"""
    result = validator.validate(fixture_path("edge_metadata_blank.mov"))
    assert result.meta.width == 320


def test_ffmpeg_timeout_config():
    """超时参数应被保存到实例上，二进制路径跨平台可用。"""
    binary = sys.executable
    runner = FFmpegRunner(binary, timeout=42)
    assert runner.timeout == 42
    assert Path(binary).exists()
