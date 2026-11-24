"""每个群一条串行队列，按人合并待办。

本地生成一条回复要好几秒，这段时间里别人再 @ 机器人，如果每条都排一次生成：
最后一个人要等十几秒，而且后几次生成看到的是同一份上下文，会得到几乎一样的重复回复。

所以这里做两件事：

- 同一个人连发的消息合并成一条待办（他本来就是一口气说完的）；
- 不同的人各占一条待办，按先来后到串行回复，谁都不会被无视。

生成时还会把「现在轮到回应谁、原话是什么、别重复回应别人」作为聚焦指令追加到
最后一条消息末尾（见 ``pipeline.focus_hint``），减少串话题和重复。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.log import logger

import bot_config as cfg

from . import pipeline, vision
from .errors import LLMError


@dataclass
class Pending:
    """一条待回复的消息（同一人的多条会合并进同一条）。"""

    user_id: int
    nickname: str
    text: str
    message_id: int
    self_id: int
    occurred_at: int
    count: int = 1
    images: list[str] = field(default_factory=list)


class GroupQueue:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        # dict 保持插入顺序：先 @ 的人先被回复
        self.items: dict[int, Pending] = {}
        self.task: asyncio.Task[None] | None = None


_queues: dict[str, GroupQueue] = {}


def _queue(group_id: str) -> GroupQueue:
    queue = _queues.get(group_id)
    if queue is None:
        queue = GroupQueue(group_id)
        _queues[group_id] = queue
    return queue


def _merge_text(current: str, incoming: str) -> str:
    merged = f"{current}\n{incoming}".strip()
    limit = cfg.PENDING_TEXT_LIMIT
    if len(merged) <= limit:
        return merged
    return "…" + merged[-limit:]


async def enqueue(
    bot: Bot,
    event: GroupMessageEvent,
    text: str,
    *,
    images: list[str] | None = None,
) -> None:
    """把这次 @ 变成一条待办；同一个人连发只占一条。"""
    from .runtime import nickname

    queue = _queue(str(event.group_id))
    occurred_at = int(event.time) * 1000 if event.time else int(time.time() * 1000)
    user_id = int(event.user_id)
    incoming = list(images or [])

    existing = queue.items.get(user_id)
    if existing is None:
        queue.items[user_id] = Pending(
            user_id=user_id,
            nickname=nickname(event),
            text=text,
            message_id=int(event.message_id),
            self_id=int(event.self_id),
            occurred_at=occurred_at,
            images=incoming,
        )
    else:
        existing.text = _merge_text(existing.text, text)
        existing.message_id = int(event.message_id)
        existing.occurred_at = occurred_at
        existing.count += 1
        # 合并时也合并图片，但总量受配置限制，避免一次塞十几张图
        limit = max(1, int(cfg.VISION_MAX_IMAGES))
        existing.images = (existing.images + incoming)[-limit:]

    if queue.task is None or queue.task.done():
        queue.task = asyncio.create_task(_drain(queue, bot))


async def _drain(queue: GroupQueue, bot: Bot) -> None:
    """依次处理这个群的待办；每次生成都会占用群锁，和别的路径互斥。"""
    try:
        # 先等一小会儿：把同一波连发合并成一条，避免第一条刚开始生成又来一条
        await asyncio.sleep(cfg.PENDING_DEBOUNCE)
        while queue.items:
            user_id = next(iter(queue.items))
            item = queue.items.pop(user_id)
            age_ms = int(time.time() * 1000) - item.occurred_at
            if age_ms > cfg.PENDING_TTL * 1000:
                logger.info(
                    f"[{queue.group_id}] 丢弃过期待办：{item.nickname}（已等 {age_ms / 1000:.0f}s）"
                )
                continue
            async with pipeline.group_lock(queue.group_id):
                await _reply(bot, queue.group_id, item)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 一条坏待办不应该把整个群的队列卡死
        logger.exception(f"[{queue.group_id}] 处理待办时出错：{exc}")
    finally:
        queue.task = None
        # 极端情况下（清空检查与 finally 之间刚好有新消息）别把待办落在队列里没人处理
        if queue.items:
            queue.task = asyncio.create_task(_drain(queue, bot))


async def _reply(bot: Bot, group_id: str, item: Pending) -> None:
    focus = pipeline.focus_hint(item.nickname, item.user_id, item.text, item.count)
    parts = await vision.load_parts(item.images) if item.images else []
    try:
        messages = await pipeline.generate_reply(
            group_id,
            item.self_id,
            reply_id=item.message_id,
            focus=focus,
            images=parts or None,
        )
    except LLMError as exc:
        logger.warning(f"[{group_id}] 生成回复失败：{exc}")
        messages = [MessageSegment.reply(item.message_id) + MessageSegment.text(pipeline.fail_tip())]
    for index, message in enumerate(messages):
        if index:
            await asyncio.sleep(pipeline.split_delay(messages[index - 1].extract_plain_text()))
        await bot.send_group_msg(group_id=int(group_id), message=message)


async def wait_idle(group_id: str, timeout: float = 30.0) -> None:
    """等这个群的队列清空（测试和 WebUI 用）。"""
    queue = _queues.get(group_id)
    if queue is None or queue.task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(queue.task), timeout)
    except asyncio.TimeoutError:
        pass


def pending_count(group_id: str) -> int:
    queue = _queues.get(group_id)
    return len(queue.items) if queue else 0


def busy(group_id: str) -> bool:
    queue = _queues.get(group_id)
    return bool(queue and queue.task and not queue.task.done())


def reset(group_id: str) -> None:
    """清空队列（测试用）。"""
    queue = _queues.pop(group_id, None)
    if queue and queue.task and not queue.task.done():
        queue.task.cancel()
