"""SqliteClient 通用 CRUD 与任务恢复单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.db.table_schema import (ALL_STATUSES, STATUS_PENDING,
                                 STATUS_RUNNING, STATUS_SUCCESS,
                                 init_schema)
from app.db.sqlite_client import SqliteClient


@pytest.fixture()
def client_db(tmp_path):
    """临时数据库客户端。"""
    return SqliteClient(tmp_path / "nested" / "test.db")


def _task(task_id: str, status: str = STATUS_PENDING) -> dict:
    """构造最小任务记录。"""
    return {
        "id": task_id, "video_path": f"/data/{task_id}.mp4",
        "video_name": f"{task_id}.mp4", "mode": "interval",
        "params": "{}", "status": status,
        "submitted_at": "2026-11-10T10:00:00"}


def test_initialize_creates_schema(client_db):
    """初始化后表与索引存在，数据库文件权限收紧。"""
    rows = client_db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")
    names = {row["name"] for row in rows}
    assert {"tasks", "app_meta"}.issubset(names)
    assert client_db.db_path.is_file()
    if os.name == "posix":
        # Windows 不遵循 POSIX 权限位，仅在类 Unix 环境校验收紧结果
        mode = os.stat(client_db.db_path).st_mode & 0o777
        assert mode == 0o600


def test_insert_and_fetch(client_db):
    """插入后可按主键查询且字段一致。"""
    client_db.insert("tasks", _task("t1"))
    row = client_db.fetch_one("tasks", "id", "t1")
    assert row is not None and row["video_name"] == "t1.mp4"
    assert client_db.fetch_one("tasks", "id", "missing") is None


def test_update_and_count(client_db):
    """更新状态后计数与字段值同步变化。"""
    client_db.insert("tasks", _task("t1"))
    affected = client_db.update("tasks", "id", "t1",
                                {"status": STATUS_SUCCESS})
    assert affected == 1
    assert client_db.count("tasks", "status = ?",
                           [STATUS_SUCCESS]) == 1
    assert client_db.update("tasks", "id", "t1", {}) == 0


def test_delete_batch(client_db):
    """批量删除返回正确行数，重复删除幂等。"""
    for idx in range(3):
        client_db.insert("tasks", _task(f"t{idx}"))
    assert client_db.delete("tasks", "id", []) == 0
    assert client_db.delete("tasks", "id", ["t0", "t1"]) == 2
    assert client_db.count("tasks") == 1
    assert client_db.delete("tasks", "id", ["t1"]) == 0


def test_list_tasks_by_status_ordering(client_db):
    """队列查询按提交时间升序返回。"""
    first = _task("early")
    first["submitted_at"] = "2026-11-10T09:00:00"
    later = _task("later")
    later["submitted_at"] = "2026-11-10T11:00:00"
    client_db.insert("tasks", later)
    client_db.insert("tasks", first)
    rows = client_db.list_tasks_by_status([STATUS_PENDING])
    assert [row["id"] for row in rows] == ["early", "later"]


def test_reset_running_tasks(client_db):
    """异常退出遗留的 running 任务被重置为 pending。"""
    client_db.insert("tasks", _task("running", STATUS_RUNNING))
    client_db.insert("tasks", _task("done", STATUS_SUCCESS))
    reset = client_db.reset_running_tasks()
    assert reset == 1
    row = client_db.fetch_one("tasks", "id", "running")
    assert row["status"] == STATUS_PENDING and row["started_at"] is None


def test_json_helpers():
    """JSON 辅助方法支持中文与默认值回退。"""
    payload = {"name": "监控 01", "n": 1}
    raw = SqliteClient.dumps(payload)
    assert "监控" in raw
    assert SqliteClient.loads(raw, {}) == payload
    assert SqliteClient.loads(None, []) == []
    assert SqliteClient.loads("not-json", {}) == {}


def test_init_schema_idempotent(client_db):
    """重复初始化表结构不报错。"""
    with client_db.connect() as conn:
        init_schema(conn)
    assert client_db.count("app_meta", "key = 'db_version'") == 1


def test_all_statuses_defined():
    """状态常量集合完整。"""
    assert set(ALL_STATUSES) == {
        STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCESS, "failed",
        "cancelled"}


def test_db_file_path_is_path(client_db):
    """数据库路径对外暴露为 Path 对象。"""
    assert isinstance(client_db.db_path, Path)
