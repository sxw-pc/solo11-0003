"""监帧工坊服务入口。

职责：
- 构建 FastAPI 应用并注册全部本地路由；
- 初始化 SQLite、任务队列并在启动时恢复未完成任务（F5/F8）；
- 提供 ``python -m app`` 启动服务与 ``python -m app --self-check``
  命令行自检两种运行方式，默认仅监听 127.0.0.1。
"""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import record_routes, system_routes, task_routes
from .core.errors import WorkflowError
from .core.ffmpeg_runner import FFmpegRunner
from .core.self_check import run_self_check
from .core.task_processor import TaskProcessor
from .core.task_queue import TaskQueue
from .core.video_validator import VideoValidator
from .db.sqlite_client import SqliteClient
from .utils.config_loader import AppConfig, load_config
from .utils.file_helper import PathValidationError
from .utils.file_helper import cleanup_stale_temp

LOGGER = logging.getLogger("monitor_workshop")


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    """构建并返回 FastAPI 应用实例（测试与启动共用同一工厂）。"""
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期：启动恢复队列，关闭时停止工作线程。"""
        state = app.state
        state.queue.start()
        state.queue.restore()
        LOGGER.info("任务队列已启动，待处理任务 %d 个",
                    state.queue.queue_depth())
        yield
        state.queue.shutdown(wait=True)
        LOGGER.info("任务队列已停止")

    app = FastAPI(
        title="监帧工坊 - 本地监控录像离线分析工具",
        version="1.0.0",
        description="纯本地关键帧提取与摘要拼接服务，无任何云端依赖",
        lifespan=lifespan,
    )
    _init_state(app, config)
    _register_exception_handler(app)
    _register_routers(app)
    return app


def _init_state(app: FastAPI, config: AppConfig) -> None:
    """装配数据库、FFmpeg、校验器、处理器与串行队列。"""
    # F8 启动即清理上次异常退出残留的临时文件
    removed = cleanup_stale_temp(config.temp_dir,
                                 keep_seconds=config.keep_temp_seconds)
    if removed:
        LOGGER.info("启动清理残留临时目录 %d 个", removed)
    db = SqliteClient(config.db_path)
    runner = FFmpegRunner(config.ffmpeg_path)
    validator = VideoValidator(runner)
    processor = TaskProcessor(db, runner, config)
    queue = TaskQueue(db, processor)
    app.state.config = config
    app.state.db = db
    app.state.runner = runner
    app.state.validator = validator
    app.state.processor = processor
    app.state.queue = queue


def _register_routers(app: FastAPI) -> None:
    """注册全部业务路由。"""
    app.include_router(task_routes.router)
    app.include_router(record_routes.router)
    app.include_router(system_routes.router)

    @app.get("/", tags=["system"], summary="服务根路径欢迎信息")
    def index() -> dict:
        """根路径仅返回本地服务说明，不暴露任何环境信息。"""
        return {"service": "monitor-workshop", "version": "1.0.0",
                "docs": "/docs"}


def _register_exception_handler(app: FastAPI) -> None:
    """把业务异常统一转换为标准化 JSON 错误响应。"""

    @app.exception_handler(WorkflowError)
    async def handle_workflow_error(_: Request,
                                    exc: WorkflowError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status,
                            content={"error": exc.to_dict()})

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request,
                                 exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "PARAM_INVALID",
                               "message": str(exc)}})

    @app.exception_handler(PathValidationError)
    async def handle_path_error(_: Request,
                                exc: PathValidationError) -> JSONResponse:
        """路径合法性错误统一返回 422 与 PATH_INVALID 错误码。"""
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "PATH_INVALID",
                               "message": str(exc)}})


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="监帧工坊 - 本地监控录像离线分析工具")
    parser.add_argument("--self-check", action="store_true",
                        help="执行离线环境自检后退出")
    parser.add_argument("--host", default=None,
                        help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=None,
                        help="监听端口，默认 8000")
    return parser.parse_args()


def main() -> int:
    """命令行入口：自检或启动 Uvicorn 服务。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _parse_args()
    config = load_config()
    if args.self_check:
        report = run_self_check(config.ffmpeg_path)
        for item in report["checks"]:
            flag = "PASS" if item["passed"] else "FAIL"
            print(f"[{flag}] {item['name']}: {item['detail']} "
                  f"{item['message']}")
        print(report["summary"])
        return 0 if report["passed"] else 1
    host = args.host or config.host
    port = args.port or config.port
    if host != "127.0.0.1":  # 安全提示：默认不暴露公网
        LOGGER.warning("服务将监听非回环地址 %s，请确认网络环境可信", host)
    import uvicorn

    uvicorn.run(create_app(config), host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
