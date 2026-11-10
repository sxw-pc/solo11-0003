"""SQLite 连接管理与通用 CRUD 封装。

设计要点：
- 每次操作打开短连接，设置 0o600 权限，仅当前用户可读写；
- 启用 WAL 与外键约束，行工厂固定为 sqlite3.Row；
- 所有写操作在同一方法内提交，异常自动回滚。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .table_schema import init_schema


class SqliteClient:
    """单文件 SQLite 的轻量访问封装。"""

    def __init__(self, db_path: Path | str):
        """
        :param db_path: 数据库文件路径，父目录会被自动创建。
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        """打开一个短生命周期连接的上下文管理器。"""
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """初始化表结构并收紧数据库文件权限。"""
        with self.connect() as conn:
            init_schema(conn)
            conn.execute("PRAGMA journal_mode = WAL")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:  # pragma: no cover - 个别文件系统不支持
            pass

    # ---------- 通用 CRUD ----------

    def insert(self, table: str, record: Dict[str, Any]) -> None:
        """向指定表插入一条字典记录。"""
        columns = list(record.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = (f"INSERT INTO {table} ({', '.join(columns)}) "
               f"VALUES ({placeholders})")
        with self.connect() as conn:
            conn.execute(sql, [record[col] for col in columns])
            conn.commit()

    def update(self, table: str, key_field: str, key_value: str,
               fields: Dict[str, Any]) -> int:
        """按主键更新部分字段，返回受影响行数。"""
        if not fields:
            return 0
        assignments = ", ".join(f"{col} = ?" for col in fields)
        sql = f"UPDATE {table} SET {assignments} WHERE {key_field} = ?"
        params = [*fields.values(), key_value]
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    def fetch_one(self, table: str, key_field: str,
                  key_value: str) -> Optional[Dict[str, Any]]:
        """按主键查询单条记录，不存在返回 None。"""
        sql = f"SELECT * FROM {table} WHERE {key_field} = ?"
        with self.connect() as conn:
            row = conn.execute(sql, (key_value,)).fetchone()
            return dict(row) if row else None

    def delete(self, table: str, key_field: str,
               key_values: Iterable[str]) -> int:
        """按主键批量删除，返回删除行数。"""
        values = list(key_values)
        if not values:
            return 0
        marks = ", ".join("?" for _ in values)
        sql = f"DELETE FROM {table} WHERE {key_field} IN ({marks})"
        with self.connect() as conn:
            cursor = conn.execute(sql, values)
            conn.commit()
            return cursor.rowcount

    def query(self, sql: str,
              params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """执行任意 SELECT 并以字典列表返回结果。"""
        with self.connect() as conn:
            rows = conn.execute(sql, params or []).fetchall()
            return [dict(row) for row in rows]

    def count(self, table: str, where: str = "",
              params: Optional[List[Any]] = None) -> int:
        """统计表行数，支持可选 WHERE 条件。"""
        sql = f"SELECT COUNT(1) AS total FROM {table}"
        if where:
            sql += f" WHERE {where}"
        with self.connect() as conn:
            row = conn.execute(sql, params or []).fetchone()
            return int(row["total"])

    # ---------- 任务专用便捷方法 ----------

    def list_tasks_by_status(self, statuses: Iterable[str]) -> List[dict]:
        """按状态集合查询任务，按提交时间升序（队列顺序）。"""
        statuses = list(statuses)
        marks = ", ".join("?" for _ in statuses)
        return self.query(
            f"SELECT * FROM tasks WHERE status IN ({marks}) "
            "ORDER BY submitted_at ASC", statuses)

    def reset_running_tasks(self) -> int:
        """把异常退出遗留的 running 任务重置为 pending，返回行数。"""
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'pending', started_at = NULL "
                "WHERE status = 'running'")
            conn.commit()
            return cursor.rowcount

    @staticmethod
    def dumps(payload: Any) -> str:
        """统一 JSON 序列化入口，保证中文不转义。"""
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def loads(raw: Optional[str], default: Any) -> Any:
        """统一 JSON 反序列化入口，空值返回默认对象。"""
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default
