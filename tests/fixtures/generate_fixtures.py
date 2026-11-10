#!/usr/bin/env python3
"""离线测试素材生成脚本。

所有模拟监控视频均通过本地 FFmpeg 的 lavfi 虚拟源合成，不使用任何受
版权保护的外部素材，也不发起网络请求。生成内容：

- mock_static.mp4  静态监控场景（无移动目标）
- mock_moving.mp4  含移动色块的监控场景（存在移动目标/场景变化）
- mock_night.mp4   夜间低照度场景
- mock_corrupt.mp4 截断损坏的视频文件
- edge_tiny.mp4    时长 <1 秒的超短视频
- edge_144p.mp4    144p 低分辨率视频
- edge_4k.mp4      4K 高分辨率短视频
- edge_chinese 名.mp4 中文与特殊字符文件名
- edge_metadata_blank.mov 无常规元数据的裸编码片段
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
EXPECTED_DIR = FIXTURE_DIR / "expected"


def find_ffmpeg() -> str:
    """定位本地 FFmpeg：环境变量 > PATH > imageio-ffmpeg 兜底。"""
    env_path = os.environ.get("MONITOR_FFMPEG")
    if env_path and Path(env_path).is_file():
        return env_path
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - 仅在缺依赖时触发
        raise SystemExit("未找到本地 FFmpeg，请先安装或设置 "
                         "MONITOR_FFMPEG 环境变量") from exc


def run_ffmpeg(ffmpeg: str, args: list) -> None:
    """执行 FFmpeg 合成命令，失败时给出明确错误定位。"""
    target = Path(args[-1])
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(
            f"生成素材失败 {target.name}\n命令: {' '.join(cmd)}\n"
            f"错误: {proc.stderr[-400:]}")


def make_static(ffmpeg: str) -> None:
    """静态场景：纯色底 + 固定色块，全程无任何画面运动。"""
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i",
        "color=c=0x3a4a5a:size=320x240:rate=15:duration=5",
        "-vf",
        "drawbox=x=120:y=90:w=80:h=60:color=0x88aacc:t=fill",
        "-pix_fmt", "yuv420p",
        str(FIXTURE_DIR / "mock_static.mp4")])


def make_moving(ffmpeg: str) -> None:
    """移动场景：灰色底上叠加大幅往返移动的亮色色块。"""
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i",
        "color=c=0x404040:size=320x240:rate=15:duration=6",
        "-f", "lavfi", "-i",
        "color=c=0xffdd66:size=70x80:rate=15:duration=6",
        "-filter_complex",
        "overlay=x=20+220*abs(2*t/6-1):y=80,format=yuv420p",
        str(FIXTURE_DIR / "mock_moving.mp4")])


def make_night(ffmpeg: str) -> None:
    """夜间场景：深色底 + 缓慢移动的微弱小光斑，整体亮度很低但非全黑。"""
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i",
        "color=c=0x141418:size=320x240:rate=15:duration=4",
        "-f", "lavfi", "-i",
        "color=c=0x3a4a66:size=24x24:rate=15:duration=4",
        "-filter_complex",
        "overlay=x=60+120*t/4:y=100,format=yuv420p",
        str(FIXTURE_DIR / "mock_night.mp4")])


def make_corrupt(ffmpeg: str) -> None:
    """先合成正常视频再截断尾部字节，模拟损坏录像。"""
    source = FIXTURE_DIR / "_corrupt_source.mp4"
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=4",
        "-pix_fmt", "yuv420p", str(source)])
    data = source.read_bytes()
    (FIXTURE_DIR / "mock_corrupt.mp4").write_bytes(data[: len(data) // 3])
    source.unlink(missing_ok=True)


def make_edge_cases(ffmpeg: str) -> None:
    """生成边界场景素材集合。"""
    # 时长 <1 秒的超短视频
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc=size=176x144:rate=15:duration=0.6",
        "-pix_fmt", "yuv420p", str(FIXTURE_DIR / "edge_tiny.mp4")])
    # 144p 低分辨率
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=15:duration=3",
        "-pix_fmt", "yuv420p", str(FIXTURE_DIR / "edge_144p.mp4")])
    # 4K 高分辨率短视频（控制时长保证生成速度）
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=10:duration=1",
        "-pix_fmt", "yuv420p", str(FIXTURE_DIR / "edge_4k.mp4")])
    # 中文与特殊字符文件名
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-pix_fmt", "yuv420p",
        str(FIXTURE_DIR / "edge_中文 监控(01).mp4")])
    # 无标题/注释元数据的裸编码 mov 片段
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-map_metadata", "-1", "-fflags", "+bitexact",
        "-f", "mov", str(FIXTURE_DIR / "edge_metadata_blank.mov")])
    # 纯黑视频：用于验证全黑无效帧过滤
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i",
        "color=c=black:size=320x240:rate=15:duration=2",
        "-pix_fmt", "yuv420p", str(FIXTURE_DIR / "edge_black.mp4")])
    # 纯白视频：用于验证全白无效帧过滤
    run_ffmpeg(ffmpeg, [
        "-f", "lavfi", "-i",
        "color=c=white:size=320x240:rate=15:duration=2",
        "-pix_fmt", "yuv420p", str(FIXTURE_DIR / "edge_white.mp4")])


def write_expected() -> None:
    """写入测试预期参数（算法输出范围、缩略图尺寸等）。"""
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    expected = {
        "mock_moving": {"min_keyframes": 2, "duration_range": [5.5, 6.5]},
        "mock_static": {"max_smart_keyframes": 2,
                        "duration_range": [4.5, 5.5]},
        "mock_night": {"duration_range": [3.5, 4.5]},
        "thumb_default_size": [320, 240],
        "fixtures": [
            "mock_static.mp4", "mock_moving.mp4", "mock_night.mp4",
            "mock_corrupt.mp4", "edge_tiny.mp4", "edge_144p.mp4",
            "edge_4k.mp4", "edge_中文 监控(01).mp4",
            "edge_metadata_blank.mov", "edge_black.mp4",
            "edge_white.mp4"]
    }
    (EXPECTED_DIR / "expected_params.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> int:
    """顺序生成全部 fixture 并输出结果清单。"""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    print(f"使用 FFmpeg: {ffmpeg}")
    make_static(ffmpeg)
    print("[1/6] mock_static.mp4 静态场景完成")
    make_moving(ffmpeg)
    print("[2/6] mock_moving.mp4 移动场景完成")
    make_night(ffmpeg)
    print("[3/6] mock_night.mp4 夜间场景完成")
    make_corrupt(ffmpeg)
    print("[4/6] mock_corrupt.mp4 损坏文件完成")
    make_edge_cases(ffmpeg)
    print("[5/6] 边界场景素材集合完成")
    write_expected()
    print("[6/6] expected 参数文件完成")
    print(f"素材输出目录: {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
