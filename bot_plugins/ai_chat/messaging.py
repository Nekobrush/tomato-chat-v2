"""消息发送辅助：统一「记录 → 生成 → 发送」的流程和回复格式。"""

from __future__ import annotations

import asyncio

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger

import bot_config as cfg
from . import pipeline
from .errors import LLMError


async def send(bot: Bot, event: GroupMessageEvent, message: Message | str) -> None:
    await bot.send_group_msg(group_id=event.group_id, message=message)


async def send_all(bot: Bot, event: GroupMessageEvent, messages: list[Message]) -> None:
    """依次发送多条气泡，中间按配置停顿。"""
    for index, message in enumerate(messages):
        if index:
            await asyncio.sleep(max(0.0, float(cfg.SPLIT_DELAY)))
        await send(bot, event, message)


async def reply_text(bot: Bot, event: GroupMessageEvent, text: str) -> None:
    """带引用回复的提示语。"""
    message = MessageSegment.reply(event.message_id) + MessageSegment.text(text)
    await send(bot, event, message)


async def chat_and_reply(
    bot: Bot,
    event: GroupMessageEvent,
    text: str,
    *,
    silent: bool = False,
) -> None:
    """调模型 → 发送（调用方需持有该群的锁）。

    用户消息已经由调用方在取锁之前记入记忆，这里只负责生成与发送，
    这样排队的消息也能保留真实的先后与时间戳。
    """
    group_id = str(event.group_id)
    try:
        messages = await pipeline.generate_reply(
            group_id,
            event.self_id,
            reply_id=event.message_id,
        )
    except LLMError as exc:
        logger.warning(f"[{group_id}] 生成回复失败：{exc}")
        if not silent:
            await reply_text(bot, event, pipeline.fail_tip())
        return
    await send_all(bot, event, messages)


def merge_pending(group_id: str) -> None:
    """没抢到发言时，把刚记录的用户消息并入上一条未回复的消息。"""
    pipeline.merge_pending_user(group_id)
