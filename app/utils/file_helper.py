"""文件系统辅助：路径合法性校验、临时文件清理、磁盘空间检查。"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

# 允许接入的监控录像容器格式（小写、含点号）
ALLOWED_SUFFIXES = {".mp4", ".avi", ".mov"}


class PathValidationError(ValueError):
    """输入路径未通过合法性校验时抛出。"""


def validate_input_path(raw_path: str,
                        allowed_suffixes: Optional[Iterable[str]] = None
                        ) -> Path:
    """校验用户提交的本地视频路径。

    校验规则：
    1. 非空且必须是绝对路径，拒绝相对路径与 ``~`` 简写造成的歧义；
    2. 解析后不得包含 ``..`` 路径遍历片段；
    3. 后缀必须在允许列表内（F1 仅支持 mp4/avi/mov）；
    4. 文件必须真实存在且为普通文件。

    :param raw_path: 用户输入的原始路径字符串。
    :param allowed_suffixes: 自定义允许后缀集合，None 使用默认集合。
    :return: 解析后的绝对路径 :class:`Path`。
    :raises PathValidationError: 任一校验不通过时抛出。
    """
    if not raw_path or not str(raw_path).strip():
        raise PathValidationError("视频路径不能为空")
    suffixes = {s.lower() for s in (allowed_suffixes or ALLOWED_SUFFIXES)}
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise PathValidationError(f"仅支持绝对路径: {raw_path}")
    if ".." in candidate.parts:
        raise PathValidationError(f"路径包含非法遍历片段: {raw_path}")
    resolved = candidate.resolve()
    if resolved.suffix.lower() not in suffixes:
        raise PathValidationError(
            f"不支持的文件格式 {resolved.suffix}，仅支持 "
            f"{', '.join(sorted(suffixes))}")
    if not resolved.exists():
        raise PathValidationError(f"文件不存在: {resolved}")
    if not resolved.is_file():
        raise PathValidationError(f"目标不是普通文件: {resolved}")
    return resolved


def ensure_dir(path: Path | str) -> Path:
    """确保目录存在，返回 Path 对象。"""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def create_task_temp_dir(base_temp_dir: Path | str, task_id: str) -> Path:
    """为指定任务创建独立临时目录，任务间互不污染。"""
    task_dir = Path(base_temp_dir) / task_id
    return ensure_dir(task_dir)


def remove_path(path: Path | str) -> bool:
    """安全删除文件或目录树，不存在视为成功。

    :return: 删除成功（含原本不存在）返回 True，删除失败返回 False。
    """
    target = Path(path)
    if not target.exists():
        return True
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True
    except OSError:
        return False


def cleanup_stale_temp(temp_dir: Path | str,
                       keep_seconds: int = 0) -> int:
    """清理临时目录中的残留文件。

    服务启动时调用：``keep_seconds=0`` 立即清理全部残留；大于 0 时
    仅清理最后修改时间早于保留周期的条目（F8）。

    :return: 实际清理的顶层条目数量。
    """
    root = Path(temp_dir)
    if not root.exists():
        return 0
    removed = 0
    deadline = time.time() - keep_seconds
    for child in root.iterdir():
        try:
            if keep_seconds <= 0 or child.stat().st_mtime <= deadline:
                remove_path(child)
                removed += 1
        except OSError:
            continue
    return removed


def get_free_space_gb(path: Path | str) -> float:
    """返回目标路径所在磁盘的剩余空间（GB，保留两位小数）。"""
    target = Path(path)
    ensure_dir(target)
    usage = shutil.disk_usage(str(target))
    return round(usage.free / (1024 ** 3), 2)


def has_enough_space(path: Path | str, required_bytes: int) -> bool:
    """判断目标磁盘剩余空间是否满足需求字节数。"""
    target = Path(path)
    ensure_dir(target)
    return shutil.disk_usage(str(target)).free >= required_bytes


def estimate_output_size(frame_count: int, width: int, height: int,
                         keep_originals: bool) -> int:
    """粗略估算任务输出所需磁盘字节数，用于处理前空间预检。"""
    thumb_bytes = width * height * 3 * frame_count
    montage_bytes = width * height * 3 * max(1, frame_count)
    original_bytes = frame_count * 1024 * 1024 if keep_originals else 0
    return thumb_bytes + montage_bytes + original_bytes


def make_temp_text_file(content: str) -> Path:
    """在系统临时目录写入文本文件（测试辅助），返回文件路径。"""
    fd, name = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    return Path(name)
