"""消息发送辅助：统一「记录 → 生成 → 发送」的流程和回复格式。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger

from . import pipeline
from .errors import LLMError
from .runtime import nickname


async def send(bot: Bot, event: GroupMessageEvent, message: Message | str) -> None:
    await bot.send_group_msg(group_id=event.group_id, message=message)


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
    """记录用户消息 → 调模型 → 发送（调用方需持有该群的锁）。"""
    group_id = str(event.group_id)
    pipeline.remember_user(group_id, nickname(event), event.user_id, text)
    try:
        message = await pipeline.generate_reply(
            group_id,
            event.self_id,
            reply_id=event.message_id,
        )
    except LLMError as exc:
        logger.warning(f"[{group_id}] 生成回复失败：{exc}")
        if not silent:
            await reply_text(bot, event, pipeline.fail_tip())
        return
    await send(bot, event, message)


async def record_only(group_id: str, event: GroupMessageEvent, text: str) -> None:
    """不回复，只记录上下文，并合并上一条未回复的用户消息。"""
    pipeline.remember_user(group_id, nickname(event), event.user_id, text)
    pipeline.merge_pending_user(group_id)
