"""回复流水线：记忆 → 组装提示词 → 调模型 → 清洗 → 抽表情包 → 组装消息。"""

from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message, MessageSegment

import bot_config as cfg

from .model import llm
from .memory import DEFAULT_MARK, PERSISTENCE_MARK, TIP_MARK, MemoryStore
from . import prompts
from .text import collapse_whitespace, extract_emotions, remove_emojis

store = MemoryStore(cfg.MAX_MEMORY)

_locks: dict[str, asyncio.Lock] = {}

_IMAGE_CACHE: dict[str, list[Path]] = {}

# 最近几条用户消息保留完整时间戳前缀，更早的剥掉以压缩 prompt（本地 prefill 是真花钱）
KEEP_PREFIX_RECORDS = 2

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{10,16}\]")

_SENTENCE_ENDINGS = "。！？!?…；;，,、"


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

def format_user_message(
    nickname: str,
    user_id: int | str,
    text: str,
    occurred_at: int | None = None,
) -> str:
    """用户消息统一格式：[毫秒时间戳]昵称(QQ号):\\n内容。

    `occurred_at` 用事件的真实发生时间，而不是"轮到处理它的时间"——
    本地生成一次要好几秒，用处理时间会把对话时间线压平。
    """
    timestamp = int(occurred_at) if occurred_at is not None else int(time.time() * 1000)
    return f"[{timestamp}]{nickname}({user_id}):\n{text}"


def remember_user(
    group_id: str,
    nickname: str,
    user_id: int | str,
    text: str,
    occurred_at: int | None = None,
) -> None:
    """记录用户消息。应在取群锁之前调用，让没抢到锁的消息也能按真实顺序落库。"""
    store.append(group_id, "user", format_user_message(nickname, user_id, text, occurred_at))


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
    return random.choice(prompts.REQFAIL_TIPS)


def focus_hint(nickname: str, user_id: int | str, text: str, count: int = 1) -> str:
    """本轮该回应谁：点名 + 引述原话，把注意力钉在目标消息上。

    群记忆是所有人共享的，模型每轮都能看到别人的话题；这句话负责告诉它
    「现在轮到这个人的这条消息」，并且别重复回应前文或自己上一条说过的话。
    文案可在 WebUI 的「人格与提示词」里改，占位符：{who} {target} {quoted}。
    """
    quoted = text if len(text) <= 80 else text[:80] + "…"
    who = f"{nickname}({user_id})"
    target = f"刚发的这 {count} 条消息" if count > 1 else "的这条消息"
    return prompts.render(prompts.FOCUS_HINT_TEMPLATE, who=who, target=target, quoted=quoted)


# ---------------------------------------------------------------------------
# 提示词组装
# ---------------------------------------------------------------------------

def system_context(bot_id: int | str) -> dict[str, str]:
    now = time.time()
    readable = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    content = prompts.render(
        prompts.CONTEXT_NOTE_TEMPLATE,
        timestamp=int(now * 1000),
        time=readable,
        self_id=bot_id,
    )
    return {"role": "system", "content": content}


def _strip_timestamp(content: str) -> str:
    return _TIMESTAMP_PREFIX.sub("", content, count=1)


def build_messages(
    group_id: str,
    bot_id: int | str,
    focus: str | None = None,
    note: str | None = None,
) -> list[dict[str, str]]:
    """组装请求消息，**让前缀尽量稳定**。

    本地推理的 prefill 是实打实的等待时间，而 llama.cpp / LM Studio 只会在
    请求前缀完全一致时复用 KV cache。所以：

    - 第 0 条 system 只放人设与 `chatsys` 的持久化设定（很少变）；
    - few-shot 紧随其后（永不变）；
    - 历史消息只追加不改写（只剥掉旧消息的时间戳，属于一次性改写）；
    - 时间、纠错提示这些每轮都变的东西，全部塞到最后一条 user 消息末尾。

    这样缓存命中的是「人设 + few-shot + 历史」，每轮只需 prefill 新增的那一两条。
    """
    records = store.records(group_id)

    persistent_system = [
        record.content
        for record in records
        if record.role == "system" and record.mark == PERSISTENCE_MARK
    ]
    volatile_system = [
        record.content
        for record in records
        if record.role == "system"
        and record.mark not in {PERSISTENCE_MARK, TIP_MARK}
    ]
    # 纠错提示只保留最新一条：堆多了既占 prompt，又会互相矛盾
    tips = [record.content for record in records if record.mark == TIP_MARK]
    if tips:
        volatile_system.append(tips[-1])
    dialog = [record for record in records if record.role != "system"]

    system_parts = [prompts.SYSTEM_MESSAGES[0]["content"], *persistent_system]
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "\n\n".join(part for part in system_parts if part)}
    ]
    messages.extend(prompts.SYSTEM_MESSAGES[1:])

    verbatim_from = max(len(dialog) - KEEP_PREFIX_RECORDS, 0)
    for offset, record in enumerate(dialog):
        content = record.content
        if record.role == "user" and offset < verbatim_from:
            content = _strip_timestamp(content)
        messages.append({"role": record.role, "content": content})

    tail_parts = [system_context(bot_id)["content"], *volatile_system]
    if note:
        # 场景说明（比如「这是私聊」）也放尾部，避免污染稳定前缀
        tail_parts.append(note)
    if focus:
        # 聚焦指令放最后：离生成最近，锚定效果最强
        tail_parts.append(focus)
    tail = "\n".join(part for part in tail_parts if part)
    if messages[-1]["role"] == "user":
        messages[-1] = {
            "role": "user",
            "content": f"{messages[-1]['content']}\n\n{tail}",
        }
    else:
        messages.append({"role": "user", "content": tail})
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

def _fit_length(text: str) -> str:
    """超长时在标点处截断，而不是让它写满 PREDICT 个 token。

    本地每多生成一个 token 都要多等，而且长回复大概率会被 post-processing 裁掉，
    不如在生成阶段就尽量短。
    """
    limit = cfg.RESP_LEN_LIMIT
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(char) for char in _SENTENCE_ENDINGS)
    if cut < limit // 2:
        return window.rstrip()
    return window[:cut].rstrip()


def collect_tips(text: str, emotions: list[str]) -> str:
    tips: list[str] = []
    if not emotions:
        tips.append(prompts.TIPS_NO_EMOTION)
    if len(text) > cfg.RESP_LEN_LIMIT:
        tips.append(prompts.TIPS_TOO_LONG)
    return "\n".join(tips)


def compose_message(text: str, emotions: list[str], reply_id: int | None) -> Message:
    message = Message()
    if reply_id is not None and random.random() < cfg.REPLY_PROBABILITY:
        message.append(MessageSegment.reply(reply_id))

    body = prompts.decorate(text or prompts.EMPTY_REPLY)
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


async def generate_reply(
    group_id: str,
    bot_id: int | str,
    reply_id: int | None = None,
    *,
    focus: str | None = None,
    note: str | None = None,
) -> Message:
    """调用模型并返回可直接发送的 Message。调用方必须持有该群的锁。"""
    messages = build_messages(group_id, bot_id, focus=focus, note=note)
    raw = await llm.chat(messages)

    without_emoji = remove_emojis(raw)
    emotions, cleaned = extract_emotions(without_emoji)
    cleaned = collapse_whitespace(cleaned)

    # 先按生成时的长度判断，再截断：截断是补救，纠错提示仍然要留给下一轮
    too_long = len(cleaned) > cfg.RESP_LEN_LIMIT

    cleaned = _fit_length(cleaned)

    # 记忆里保留带情绪标记的原文，方便模型学习格式
    store.append(group_id, "assistant", collapse_whitespace(without_emoji))

    tips: list[str] = []
    if not emotions:
        tips.append(prompts.TIPS_NO_EMOTION)
    if too_long:
        tips.append(prompts.TIPS_TOO_LONG)
    if tips:
        store.append(group_id, "system", "\n".join(tips), mark=TIP_MARK)

    return compose_message(cleaned, emotions, reply_id)
