"""tomato-chat 启动入口。

用法：
    .venv\\Scripts\\python.exe main.py
    或者双击 start.bat（推荐，会打开网页控制台）

启动后：
    - WebUI           http://127.0.0.1:8765/webui/
    - OneBot 反向 WS  ws://127.0.0.1:8765/onebot/v11/ws
"""

from __future__ import annotations

import sys
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

ROOT = Path(__file__).resolve().parent


def main() -> None:
    # 保证项目根目录在 sys.path 里，插件才能被导入为 bot_plugins.ai_chat
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugins(str(ROOT / "bot_plugins"))

    # WebUI 挂在 NoneBot 的 FastAPI 应用上，与 OneBot WS 共用 8765 端口
    app = getattr(nonebot.get_driver(), "server_app", None)
    if app is not None:
        from webui import setup as setup_webui

        setup_webui(app)
        from nonebot.log import logger

        logger.info("WebUI: http://127.0.0.1:8765/webui/")

    nonebot.run()


if __name__ == "__main__":
    main()
