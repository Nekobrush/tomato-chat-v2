"""回复流水线：记忆 → 组装提示词 → 调模型 → 清洗 → 抽表情包 → 组装消息。"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment

import bot_config as cfg

from .model import llm
from .memory import DEFAULT_MARK, PERSISTENCE_MARK, MemoryStore
from .prompts import REQFAIL_TIPS, SYSTEM_MESSAGES, TIPS_NO_EMOTION, TIPS_NO_MEOW, TIPS_TOO_LONG
from .text import collapse_whitespace, extract_emotions, remove_emojis

store = MemoryStore(cfg.MAX_MEMORY)

_locks: dict[str, asyncio.Lock] = {}

_IMAGE_CACHE: dict[str, list[Path]] = {}


def group_lock(group_id: str) -> asyncio.Lock:
    """每个群一把锁，保证"记录 → 生成 → 发送"不会交错。"""
    lock = _locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[group_id] = lock
    return lock


# ---------------------------------------------------------------------------
# 记忆写入
# ---------------------------------------------------------------------------

def format_user_message(nickname: str, user_id: int | str, text: str) -> str:
    """用户消息统一格式：[毫秒时间戳]昵称(QQ号):\\n内容。"""
    timestamp = int(time.time() * 1000)
    return f"[{timestamp}]{nickname}({user_id}):\n{text}"


def remember_user(group_id: str, nickname: str, user_id: int | str, text: str) -> None:
    store.append(group_id, "user", format_user_message(nickname, user_id, text))


def merge_pending_user(group_id: str) -> bool:
    """把上一条未回复的用户消息与最新一条合并。"""
    return store.merge_user_tail(group_id)


def remember_system(group_id: str, text: str, *, persist: bool = False) -> None:
    mark = PERSISTENCE_MARK if persist else DEFAULT_MARK
    store.append(group_id, "system", text, mark=mark)
    if persist:
        store.set_max_record(store.max_record + 1)


def reset_group(group_id: str) -> int:
    """清空群记忆并恢复默认容量，返回清掉的条数。"""
    removed = store.clear(group_id)
    store.set_max_record(cfg.MAX_MEMORY)
    return removed


def fail_tip() -> str:
    return random.choice(REQFAIL_TIPS)


# ---------------------------------------------------------------------------
# 提示词组装
# ---------------------------------------------------------------------------

def system_context(bot_id: int | str) -> dict[str, str]:
    now = time.time()
    readable = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    content = f"当前毫秒级时间戳:{int(now * 1000)}\n当前时间:{readable}\n你的QQ号:{bot_id}"
    return {"role": "system", "content": content}


def build_messages(group_id: str, bot_id: int | str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = list(SYSTEM_MESSAGES)
    messages.append(system_context(bot_id))
    messages.extend(store.messages(group_id))
    return messages


# ---------------------------------------------------------------------------
# 表情包
# ---------------------------------------------------------------------------

def emotion_candidates(emotion: str) -> list[Path]:
    cached = _IMAGE_CACHE.get(emotion)
    if cached is not None:
        return cached
    folder = cfg.EMOTION_PIC_PATH / emotion
    candidates: list[Path] = []
    try:
        for path in folder.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in cfg.IMAGE_SUFFIXES:
                continue
            try:
                if path.stat().st_size > cfg.IMAGE_MAX_BYTES:
                    continue
            except OSError:
                continue
            candidates.append(path)
    except OSError:
        candidates = []
    _IMAGE_CACHE[emotion] = candidates
    return candidates


def pick_emotion_image(emotion: str | None) -> bytes | None:
    if not emotion or emotion not in cfg.EMOTIONS:
        return None
    candidates = emotion_candidates(emotion)
    if not candidates:
        return None
    try:
        return random.choice(candidates).read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def _needs_meow(text: str) -> bool:
    trimmed = text.rstrip("。！？!?~～….,，、 \t\u3000")
    return not trimmed.endswith(cfg.KEYWORDS)


def collect_tips(text: str, emotions: list[str]) -> str:
    tips: list[str] = []
    if not emotions:
        tips.append(TIPS_NO_EMOTION)
    if len(text) > cfg.RESP_LEN_LIMIT:
        tips.append(TIPS_TOO_LONG)
    if _needs_meow(text):
        tips.append(TIPS_NO_MEOW)
    return "\n".join(tips)


def compose_message(text: str, emotions: list[str], reply_id: int | None) -> Message:
    message = Message()
    if reply_id is not None and random.random() < cfg.REPLY_PROBABILITY:
        message.append(MessageSegment.reply(reply_id))

    body = text or "喵？"
    emotion = random.choice(emotions) if emotions else None
    image = pick_emotion_image(emotion)

    if image is not None and random.random() < cfg.SEND_IMAGE_PROBABILITY:
        image_segment = MessageSegment.image(image)
        if random.random() < 0.5:
            message.extend([image_segment, MessageSegment.text(body)])
        else:
            message.extend([MessageSegment.text(body), image_segment])
    else:
        message.append(MessageSegment.text(body))
    return message


async def generate_reply(group_id: str, bot_id: int | str, reply_id: int | None = None) -> Message:
    """调用模型并返回可直接发送的 Message。调用方必须持有该群的锁。"""
    messages = build_messages(group_id, bot_id)
    raw = await llm.chat(messages)

    without_emoji = remove_emojis(raw)
    emotions, cleaned = extract_emotions(without_emoji)
    cleaned = collapse_whitespace(cleaned)

    # 记忆里保留带情绪标记的原文，方便模型学习格式
    store.append(group_id, "assistant", collapse_whitespace(without_emoji))

    tips = collect_tips(cleaned, emotions)
    if tips:
        store.append(group_id, "system", tips)

    return compose_message(cleaned, emotions, reply_id)
