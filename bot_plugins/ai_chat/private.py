"""私聊：一对一聊天。

刻意**不加载**群聊那套机制：

- 没有群白名单、水群概率、群开关、排队合并（一对一不需要争抢）
- 没有指令与权限（`chatset` 之类都是群管理的概念）
- 不需要「引用回复」，也不做按人合并

复用的只有：人设与记忆、模型调用、情绪标记解析、表情包、长度与「喵」的兜底。
"""

from __future__ import annotations

import asyncio

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.log import logger

import bot_config as cfg

from . import pipeline, prompts
from .errors import LLMError
from .runtime import nickname
from .text import segments_to_text


def _allowed(event: PrivateMessageEvent) -> bool:
    if not cfg.PRIVATE_CHAT_ENABLED:
        return False
    allowed = cfg.ALLOW_PRIVATE_USERS
    return not allowed or str(event.user_id) in allowed


def _private_rule(event: Event) -> bool:
    return isinstance(event, PrivateMessageEvent) and _allowed(event)


private_matcher = on_message(rule=_private_rule, priority=10, block=False)


def memory_key(user_id: int | str) -> str:
    """私聊记忆单独的键，和群号不会冲突。"""
    return f"{cfg.PRIVATE_MEMORY_PREFIX}:{user_id}"


@private_matcher.handle()
async def handle_private_message(bot: Bot, event: PrivateMessageEvent) -> None:
    text = segments_to_text(event.message, event.self_id)
    if not text:
        # 纯图片 / 表情，没什么可回的
        return

    key = memory_key(event.user_id)
    occurred_at = int(event.time) * 1000 if event.time else None
    pipeline.remember_user(key, nickname(event), event.user_id, text, occurred_at)

    async with pipeline.group_lock(key):
        try:
            messages = await pipeline.generate_reply(
                key,
                event.self_id,
                reply_id=None,
                note=prompts.PRIVATE_NOTE,
            )
        except LLMError as exc:
            logger.warning(f"[私聊 {event.user_id}] 生成回复失败：{exc}")
            await bot.send_private_msg(user_id=event.user_id, message=pipeline.fail_tip())
            return

    for index, message in enumerate(messages):
        if index:
            await asyncio.sleep(max(0.0, float(cfg.SPLIT_DELAY)))
        await bot.send_private_msg(user_id=event.user_id, message=message)


__all__ = ["handle_private_message", "memory_key", "private_matcher"]
