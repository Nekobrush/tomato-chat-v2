"""tomato-chat 启动入口。

用法：
    .venv\\Scripts\\python.exe main.py
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
    nonebot.run()


if __name__ == "__main__":
    main()
