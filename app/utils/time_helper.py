"""视频时间戳换算与通用时间格式化工具。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def format_timestamp(seconds: float) -> str:
    """把秒数换算成 ``HH:MM:SS.mmm`` 形式的视频内时间戳。

    :param seconds: 距视频开头的秒数，负数会被归零处理。
    :return: 固定宽度的时间戳字符串，例如 ``00:01:23.500``。
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def frame_to_seconds(frame_index: int, fps: float) -> float:
    """按帧序号与帧率换算时间（秒）。

    :param frame_index: 帧序号，从 0 开始。
    :param fps: 视频帧率，非法（<=0）时退化为 1fps 避免除零。
    :return: 该帧对应的视频时间（秒）。
    """
    if not fps or fps <= 0:
        fps = 1.0
    return frame_index / fps


def now_iso() -> str:
    """返回当前本地时间的 ISO8601 秒级字符串。"""
    return datetime.now().replace(microsecond=0).isoformat()


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    """把 datetime 转为 ISO 字符串，None 原样返回。"""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 字符串，失败或入参为 None 时返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def add_seconds(value: str, seconds: float) -> str:
    """在 ISO 时间字符串上叠加指定秒数，返回新的 ISO 字符串。"""
    base = parse_iso(value) or datetime.now()
    return to_iso(base + timedelta(seconds=seconds))  # type: ignore[arg-type]


def is_within_range(value: str, start: Optional[str],
                    end: Optional[str]) -> bool:
    """判断 ISO 时间是否落在 [start, end] 闭区间内，边界为空表示不限制。"""
    target = parse_iso(value)
    if target is None:
        return False
    if start:
        start_dt = parse_iso(start)
        if start_dt and target < start_dt:
            return False
    if end:
        end_dt = parse_iso(end)
        if end_dt and target > end_dt:
            return False
    return True
