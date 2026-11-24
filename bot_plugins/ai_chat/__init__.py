"""番茄猫娘 —— NoneBot2 / OneBot v11 插件。

模块划分：

- ``text``       CQ 码 / emoji / 情绪标记 / 指令解析（无副作用）
- ``memory``     每群记忆的裁剪、合并
- ``providers``  模型后端（ollama 原生协议、OpenAI 兼容 API）
- ``model``      模型源与模型的切换、统一调用入口
- ``pipeline``   记忆 + 生成 + 表情包 + 消息组装
- ``runtime``    群状态与权限判断
- ``messaging``  发送辅助
- ``commands``   指令处理

指令前缀与旧版保持一致：`y`、`!`、`！`、`tomato`、`/`，
例如 `ychat`、`y chat`、`!chatset p 3%`、`tomato chatcls`。
"""

from __future__ import annotations

import random

from nonebot import get_driver, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.plugin import PluginMetadata

import bot_config as cfg

from . import pipeline
from .commands import USAGE_TEXT, handle_command
from .model import llm
from .runtime import STATES, GroupState, state as _state
from .text import parse_command, segments_to_text

__plugin_meta__ = PluginMetadata(
    name="番茄猫娘",
    description="群聊猫娘 AI，带情绪表情包",
    usage=USAGE_TEXT,
    type="application",
    supported_adapters={"~onebot.v11"},
)

# ---------------------------------------------------------------------------
# 规则与 matcher
# ---------------------------------------------------------------------------


def _allowed(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent) and str(event.group_id) in cfg.ALLOW_GROUPS


def _command_rule(event: Event) -> bool:
    return _allowed(event) and parse_command(event.get_plaintext()) is not None


def _chat_rule(event: Event) -> bool:
    return _allowed(event)


# priority 1：指令；priority 10：普通聊天 / 水群
command_matcher = on_message(rule=_command_rule, priority=1, block=True)
chat_matcher = on_message(rule=_chat_rule, priority=10, block=False)

# 指令处理逻辑在 commands.py，这里注册到 matcher 上
command_matcher.handle()(handle_command)


# ---------------------------------------------------------------------------
# 普通消息：@机器人 必回；否则按概率水群
# ---------------------------------------------------------------------------


@chat_matcher.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent) -> None:
    group_id = str(event.group_id)
    state = _state(group_id)
    if not state.enabled:
        return

    text = segments_to_text(event.message, event.self_id)
    if not text:
        # 纯图片/表情等无法理解的消息，不回复
        return

    # 延迟 import：messaging 依赖 pipeline 与本模块用到的单例，避免循环
    from .messaging import chat_and_reply, record_only

    if event.is_tome():
        async with pipeline.group_lock(group_id):
            await chat_and_reply(bot, event, text)
        return

    async with pipeline.group_lock(group_id):
        if not state.water_enabled or random_float() > state.water_probability:
            await record_only(group_id, event, text)
            return
        await chat_and_reply(bot, event, text, silent=True)


def random_float() -> float:
    """单独抽出来，方便测试时替换。"""
    return random.random()


async def _close_providers() -> None:
    await llm.aclose()


# 单元测试会在没有初始化 NoneBot 的情况下 import 本模块，这里允许注册失败；
# 真正运行时（main.py 先 init 再 load_plugins）一定能注册上。
try:
    get_driver().on_shutdown(_close_providers)
except ValueError:
    pass


__all__ = [
    "STATES",
    "GroupState",
    "chat_matcher",
    "command_matcher",
    "handle_command",
    "handle_group_message",
    "llm",
    "pipeline",
]
