"""SQLite 表结构定义与初始化逻辑。

任务表同时承担轻量任务队列（F5）与处理记录（F6）两种职责：
排队/恢复依赖 status 字段，历史查询依赖时间与文件名索引。
"""

from __future__ import annotations

# 任务状态常量，避免散落在业务代码中的魔法字符串
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = (STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS,
                STATUS_FAILED, STATUS_CANCELLED)
# 服务重启后需要自动恢复的未完成状态
RECOVERABLE_STATUSES = (STATUS_PENDING, STATUS_RUNNING)

DB_VERSION = 1

CREATE_TASK_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    video_path       TEXT NOT NULL,
    video_name       TEXT NOT NULL,
    mode             TEXT NOT NULL,
    params           TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending',
    error_code       TEXT,
    error_message    TEXT,
    submitted_at     TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    keyframe_count   INTEGER NOT NULL DEFAULT 0,
    output_dir       TEXT,
    montage_path     TEXT,
    video_metadata   TEXT,
    keyframes        TEXT NOT NULL DEFAULT '[]',
    cancel_requested INTEGER NOT NULL DEFAULT 0
)
"""

CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_submitted "
    "ON tasks(submitted_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(video_name)",
)

CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


def init_schema(conn) -> None:
    """在给定连接上创建全部表与索引（幂等执行）。

    :param conn: 已打开的 sqlite3.Connection。
    """
    conn.execute(CREATE_TASK_TABLE)
    conn.execute(CREATE_META_TABLE)
    for statement in CREATE_INDEXES:
        conn.execute(statement)
    conn.execute(
        "INSERT OR REPLACE INTO app_meta(key, value) VALUES('db_version', ?)",
        (str(DB_VERSION),))
    conn.commit()
