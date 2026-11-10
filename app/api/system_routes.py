"""系统自检、健康检查与配置查询接口（F7）。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.self_check import run_self_check
from ..utils.file_helper import get_free_space_gb
from .schemas import HealthResponse, SelfCheckResponse

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health", response_model=HealthResponse,
            summary="服务健康检查")
def health(request: Request) -> dict:
    """返回服务存活状态、FFmpeg 可用性与队列深度。"""
    state = request.app.state
    return {
        "status": "ok",
        "ffmpeg_available": state.runner.check_available(),
        "data_dir": str(state.config.data_dir),
        "queue_depth": state.queue.queue_depth(),
    }


@router.get("/config", summary="查询本地运行配置（不含敏感信息）")
def get_config(request: Request) -> dict:
    """返回非敏感的本地配置项，便于离线环境核对。"""
    config = request.app.state.config
    return {
        "host": config.host,
        "port": config.port,
        "data_dir": str(config.data_dir),
        "db_path": str(config.db_path),
        "temp_dir": str(config.temp_dir),
        "output_dir": str(config.output_dir),
        "ffmpeg_path": config.ffmpeg_path,
        "keep_temp_seconds": config.keep_temp_seconds,
        "free_space_gb": get_free_space_gb(config.data_dir),
    }


@router.post("/self-check", response_model=SelfCheckResponse,
             summary="一键离线环境自检")
def self_check(request: Request) -> dict:
    """使用自带 fixture 跑通完整处理流程并返回自检报告。"""
    return run_self_check(request.app.state.config.ffmpeg_path)
