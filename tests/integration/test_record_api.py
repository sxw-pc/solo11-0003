"""历史记录 API 集成测试（F6）。"""

from __future__ import annotations

import time
from pathlib import Path

from app.db.table_schema import STATUS_FAILED, STATUS_SUCCESS
from tests.conftest import fixture_path


def _create_finished_task(client, video="mock_moving.mp4",
                          failed=False):
    """通过 API 提交并等待任务执行完成，返回任务 ID。"""
    response = client.post("/api/tasks", json={
        "video_paths": [fixture_path(video)],
        "mode": "interval", "interval_seconds": 2})
    assert response.status_code == 200
    task_id = response.json()["submitted"][0]["task_id"]
    deadline = time.time() + 60
    while True:
        detail = client.get(f"/api/tasks/{task_id}").json()
        if detail["status"] not in ("pending", "running"):
            break
        assert time.time() < deadline
        time.sleep(0.2)
    expected = STATUS_FAILED if failed else STATUS_SUCCESS
    assert detail["status"] == expected
    return task_id, detail


def test_submit_and_query_record(client):
    """提交任务后可在记录列表中查询到。"""
    task_id, detail = _create_finished_task(client)
    response = client.get("/api/records")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert any(item["id"] == task_id for item in payload["items"])
    assert detail["montage_path"]


def test_record_filter_by_status(client):
    """按状态过滤只返回匹配记录。"""
    _create_finished_task(client)
    response = client.get("/api/records", params={"status": "success"})
    items = response.json()["items"]
    assert items and all(item["status"] == "success" for item in items)


def test_record_filter_by_invalid_status(client):
    """非法状态值返回 422 参数错误。"""
    response = client.get("/api/records", params={"status": "exploded"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PARAM_INVALID"


def test_record_filter_by_video_name(client):
    """文件名模糊查询支持部分关键字。"""
    _create_finished_task(client, video="mock_static.mp4")
    response = client.get("/api/records",
                          params={"video_name": "static"})
    items = response.json()["items"]
    assert items and all("static" in item["video_name"]
                         for item in items)


def test_record_pagination(client):
    """分页参数生效，page_size 控制单页条数。"""
    for _ in range(3):
        _create_finished_task(client, video="mock_static.mp4")
    response = client.get("/api/records",
                          params={"page": 1, "page_size": 2})
    payload = response.json()
    assert payload["page_size"] == 2 and len(payload["items"]) == 2
    assert payload["total"] >= 3


def test_record_time_range_filter(client):
    """时间范围过滤：未来区间为空，当前区间包含记录。"""
    _create_finished_task(client)
    empty = client.get("/api/records", params={
        "start_time": "2099-01-01T00:00:00"})
    assert empty.json()["total"] == 0
    hit = client.get("/api/records", params={
        "start_time": "2000-01-01T00:00:00"})
    assert hit.json()["total"] >= 1


def test_delete_record_keep_files(client):
    """仅删除数据库记录时输出文件保留。"""
    task_id, detail = _create_finished_task(client)
    montage = Path(detail["montage_path"])
    response = client.request("DELETE", "/api/records",
                              json={"ids": [task_id],
                                    "delete_files": False})
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert montage.exists()
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_delete_record_with_files(client):
    """删除记录并同步清理本地输出文件。"""
    task_id, detail = _create_finished_task(client)
    output_dir = Path(detail["output_dir"])
    response = client.request("DELETE", "/api/records",
                              json={"ids": [task_id],
                                    "delete_files": True})
    assert response.json()["deleted"] == 1
    assert response.json()["files_removed"] == 1
    assert not output_dir.exists()


def test_batch_delete_with_missing_id(client):
    """批量删除中不存在的 ID 进入 not_found 列表。"""
    task_id, _ = _create_finished_task(client)
    response = client.request("DELETE", "/api/records",
                              json={"ids": [task_id, "ghost-id"]})
    payload = response.json()
    assert payload["deleted"] == 1
    assert payload["not_found"] == ["ghost-id"]


def test_corrupt_record_persisted_with_error(client):
    """损坏文件在提交阶段被拒绝，不产生任务记录。"""
    response = client.post("/api/tasks", json={
        "video_paths": [fixture_path("mock_corrupt.mp4")]})
    payload = response.json()
    assert payload["rejected"] and not payload["submitted"]
    assert payload["rejected"][0]["code"] == "VIDEO_CORRUPT"
    assert client.get("/api/records").json()["total"] == 0
