"""系统检查、参数校验与异常场景集成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.self_check import run_self_check
from app.utils.config_loader import load_config
from tests.conftest import fixture_path


def test_health_endpoint(client):
    """健康检查返回服务存活与 FFmpeg 状态。"""
    response = client.get("/api/system/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ffmpeg_available"] is True
    assert payload["queue_depth"] >= 0


def test_config_endpoint_local_only(client):
    """配置查询返回本地路径与磁盘空间，无敏感字段。"""
    response = client.get("/api/system/config")
    payload = response.json()
    assert payload["host"] == "127.0.0.1"
    assert Path(payload["data_dir"]).is_dir()
    assert payload["free_space_gb"] > 0
    assert "password" not in payload and "token" not in payload


def test_self_check_endpoint_passes(client):
    """API 自检全链路通过并返回分项报告。"""
    response = client.post("/api/system/self-check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["passed"] is True
    names = {item["name"] for item in payload["checks"]}
    assert {"ffmpeg_available", "pipeline_execution", "montage_output",
            "database_read_write", "temp_cleanup"}.issubset(names)


def test_self_check_function_cleans_temp(ffmpeg_path):
    """直接调用自检函数，结束后无临时目录残留。"""
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("monitor_selfcheck_*"))
    report = run_self_check(ffmpeg_path)
    assert report["passed"] is True
    after = set(Path(tempfile.gettempdir()).glob("monitor_selfcheck_*"))
    assert after.issubset(before)


def test_self_check_missing_fixture(monkeypatch, tmp_path):
    """素材缺失时自检失败并给出明确提示。"""
    import app.core.self_check as module

    monkeypatch.setattr(module, "_project_root",
                        lambda: tmp_path / "no-project")
    report = run_self_check("/bin/true")
    assert report["passed"] is False
    assert report["checks"][0]["name"] == "fixture_ready"


def test_openapi_docs_available(client):
    """内置 OpenAPI 文档可在本地访问。"""
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_root_welcome(client):
    """根路径返回服务信息。"""
    payload = client.get("/").json()
    assert payload["service"] == "monitor-workshop"


def test_submit_rejects_empty_paths(client):
    """空路径列表触发 Pydantic 422 校验。"""
    response = client.post("/api/tasks", json={"video_paths": []})
    assert response.status_code == 422


def test_submit_rejects_bad_interval(client):
    """抽帧间隔超出 1-300 范围被拒绝。"""
    response = client.post("/api/tasks", json={
        "video_paths": [fixture_path("mock_static.mp4")],
        "interval_seconds": 999})
    assert response.status_code == 422


def test_submit_rejects_bad_thumb_width(client):
    """缩略图宽度超出 160-1920 范围被拒绝。"""
    response = client.post("/api/tasks", json={
        "video_paths": [fixture_path("mock_static.mp4")],
        "thumb_width": 80})
    assert response.status_code == 422


def test_submit_rejects_illegal_mode(client):
    """非法抽帧模式被拒绝。"""
    response = client.post("/api/tasks", json={
        "video_paths": [fixture_path("mock_static.mp4")],
        "mode": "magic"})
    assert response.status_code == 422


def test_submit_deduplicates_paths(client):
    """同批次重复路径进入 rejected 且标记 DUPLICATE_PATH。"""
    path = fixture_path("mock_static.mp4")
    response = client.post("/api/tasks",
                           json={"video_paths": [path, path]})
    payload = response.json()
    assert len(payload["submitted"]) == 1
    assert payload["rejected"][0]["code"] == "DUPLICATE_PATH"


def test_submit_relative_path_rejected(client):
    """相对路径不允许提交，返回标准化路径错误码。"""
    response = client.post("/api/tasks", json={
        "video_paths": ["tests/fixtures/mock_static.mp4"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PATH_INVALID"


def test_task_not_found_404(client):
    """查询不存在任务返回 404 与标准化错误体。"""
    response = client.get("/api/tasks/unknown-task")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_load_config_from_env(tmp_path, monkeypatch):
    """环境变量指定的数据目录可被正确加载并自动建目录。"""
    monkeypatch.setenv("MONITOR_DATA_DIR", str(tmp_path / "env-data"))
    config = load_config()
    assert config.data_dir == (tmp_path / "env-data").resolve()
    assert config.db_dir.is_dir() and config.temp_dir.is_dir()


def test_default_host_is_loopback(client):
    """默认监听地址严格为本地回环。"""
    assert client.app.state.config.host == "127.0.0.1"


def test_disk_space_insufficient(monkeypatch, queue, processor):
    """磁盘空间不足时任务失败并返回 DISK_SPACE_INSUFFICIENT。"""
    from app.core import task_processor

    monkeypatch.setattr(task_processor.file_helper, "has_enough_space",
                        lambda path, required: False)
    task_id = queue.submit(fixture_path("mock_moving.mp4"),
                           {"mode": "interval"})
    from app.core.errors import DiskSpaceError

    with pytest.raises(DiskSpaceError):
        processor.process(task_id)
    record = queue.get_status(task_id)
    assert record["error_code"] == "DISK_SPACE_INSUFFICIENT"
