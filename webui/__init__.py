"""内嵌 WebUI：挂在 NoneBot 自己的 FastAPI 应用上。

这样只需要启动一个进程（``python main.py``），QQ 反向 WS 和网页控制台就都在
同一个端口上：

- ``/onebot/v11/ws`` —— OneBot v11 反向 WebSocket（给 QQ 框架用）
- ``/webui/``        —— 网页控制台
- ``/api/*``         —— 控制台接口（默认仅本机）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import bot_config as cfg

from .api import VERSION, public_router, router

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOG_DIR = cfg.BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "bot.log"

_log_sink_installed = False


def install_log_sink() -> None:
    """把 NoneBot 的 loguru 日志同时写一份到 logs/bot.log，供网页查看。"""
    global _log_sink_installed
    if _log_sink_installed:
        return
    from nonebot.log import logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(LOG_FILE),
        rotation="5 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} - {message}",
    )
    _log_sink_installed = True


def setup(app: FastAPI) -> FastAPI:
    """注册 API、静态页面和日志文件输出。重复调用是安全的。"""
    if getattr(app.state, "tomato_webui", False):
        return app

    app.include_router(public_router, prefix="/api")
    app.include_router(router, prefix="/api")
    app.mount("/webui", StaticFiles(directory=str(STATIC_DIR), html=True), name="webui")
    app.add_api_route(
        "/",
        lambda: RedirectResponse(url="/webui/"),
        methods=["GET"],
        include_in_schema=False,
    )
    app.state.tomato_webui = True
    install_log_sink()
    return app


__all__ = ["VERSION", "install_log_sink", "setup"]
