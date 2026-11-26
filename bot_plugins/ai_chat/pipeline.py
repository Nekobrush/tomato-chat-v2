"""回复流水线：记忆 → 组装提示词 → 调模型 → 清洗 → 抽表情包 → 组装消息。"""

from __future__ import annotations

import asyncio
import pathlib
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.log import logger

import bot_config as cfg

from . import prompts, worldbook
from .memory import (
    DEFAULT_MARK,
    PERSISTENCE_MARK,
    TIP_MARK,
    JsonMemoryStorage,
    MemoryStore,
)
from .model import llm
from .text import collapse_whitespace, extract_emotions, remove_emojis

# 是否把记忆落盘由配置决定；关闭时行为与以前完全一致（纯内存）。
store = MemoryStore(
    cfg.MAX_MEMORY,
    JsonMemoryStorage(cfg.MEMORY_STORE_PATH)
    if getattr(cfg, "MEMORY_PERSIST_ENABLED", False)
    else None,
)


def now_ms() -> int:
    """当前时间的毫秒时间戳。

    单点封装是为了让测试可以替换时钟，也让"记录时间"只依赖这一个来源。
    """
    return int(time.time() * 1000)

_locks: dict[str, asyncio.Lock] = {}

_IMAGE_CACHE: dict[str, list[Path]] = {}

# 最近几条用户消息保留完整时间戳前缀，更早的剥掉以压缩 prompt（本地 prefill 是真花钱）
KEEP_PREFIX_RECORDS = 2

_books_cache: list[worldbook.Book] | None = None
_books_mtime: int | None = None

_TIMESTAMP_PREFIX = re.compile(r"^\[\d{10,16}\]")

# 合并过的记录里可能嵌着多个 `[时间戳]昵称(QQ号):` 头（旧版合并方式留下的）。
# 时间戳对模型没有价值（它只需要昵称和先后顺序），所以沿用时序渲染时统一去掉；
# 昵称/QQ 号保留，否则合并记录里好几个人说话就分不清谁是谁。
_ANY_TIMESTAMP = re.compile(r"\[\d{10,16}\]")

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
    timestamp = int(occurred_at) if occurred_at is not None else now_ms()
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
    now = now_ms() / 1000
    readable = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    content = prompts.render(
        prompts.CONTEXT_NOTE_TEMPLATE,
        timestamp=int(now * 1000),
        time=readable,
        self_id=bot_id,
    )
    return {"role": "system", "content": content}


def _strip_timestamp(content: str) -> str:
    """去掉旧记录里的头部（含合并记录中的嵌套头），并清理多余空行。

    只对「较旧的」记录调用；最近两条保留原始格式，让模型知道这轮是谁说的。
    旧版合并把整条格式化消息拼进前一条，于是同一行里塞了好几个
    `[1795623983000]喵(123):`，还会因为截断留下 `…5624066000]` 这种残片。
    """
    cleaned = _ANY_TIMESTAMP.sub("", content.replace("\r\n", "\n"))
    cleaned = re.sub(r"\.{0,1}\d{4,16}\]", "", cleaned)  # 截断残留的半截时间戳
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() or content.strip()


def books() -> list[worldbook.Book]:
    """读取设定书文件；按 mtime 缓存，改完文件不用重启。"""
    global _books_cache, _books_mtime
    if not getattr(cfg, "WORLDBOOK_ENABLED", True):
        return []
    path = pathlib.Path(cfg.WORLDBOOK_FILE)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _books_cache, _books_mtime = [], None
        return []
    if _books_cache is None or _books_mtime != mtime:
        _books_cache = worldbook.load_books(path)
        _books_mtime = mtime
        logger.info(f"已加载 {len(_books_cache)} 本设定书")
    return _books_cache


def book_hint(group_id: str) -> str:
    """把命中的设定书条目拼成一段尾部提示（没有命中就返回空串）。"""
    books_loaded = books()
    if not books_loaded:
        return ""
    scan = [record.content for record in store.records(group_id)[-6:] if record.role == "user"]
    if not scan:
        return ""
    hits = worldbook.match_entries(
        books_loaded,
        scan,
        max_chars=int(getattr(cfg, "WORLDBOOK_MAX_CHARS", 1200)),
    )
    if not hits:
        return ""
    return "【设定书】\n" + "\n".join(hits)


def memory_budget() -> int:
    """记忆能占多少 token。

    `MAX_MEMORY` 只是条数上限，短句和长句占的空间差很多；这里再给一个
    与上下文长度挂钩的预算：配置为 0 时按 `GLOBAL_CTX` 的 35% 估算。
    """
    configured = int(getattr(cfg, "MEMORY_TOKEN_BUDGET", 0) or 0)
    if configured > 0:
        return configured
    ctx = int(getattr(cfg, "GLOBAL_CTX", 8192) or 8192)
    return max(512, int(ctx * 0.35))


def build_messages(
    group_id: str,
    bot_id: int | str,
    focus: str | None = None,
    note: str | None = None,
    images: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """组装请求消息，**让前缀尽量稳定**。

    本地推理的 prefill 是实打实的等待时间，而 llama.cpp / LM Studio 只会在
    请求前缀完全一致时复用 KV cache。所以：

    - 第 0 条 system 只放人设与 `chatsys` 的持久化设定（很少变）；
    - few-shot 紧随其后（永不变）；
    - 历史消息只追加不改写（只剥掉旧消息的时间戳，属于一次性改写）；
    - 时间、纠错提示这些每轮都变的东西，全部塞到最后一条 user 消息末尾。

    这样缓存命中的是「人设 + few-shot + 历史」，每轮只需 prefill 新增的那一两条。
    """
    # 条数上限之外再按 token 预算裁一次：长句群里自动少留几条，短句群能多留
    store.trim_to_tokens(group_id, memory_budget())
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
    messages: list[dict[str, Any]] = [
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
    hint = book_hint(group_id)
    if hint:
        # 设定书命中：放在时间/纠错之后、场景与聚焦之前——比纠错重要，但不如
        # 「这一轮到底回谁」的聚焦指令重要。
        tail_parts.append(hint)
    if note:
        # 场景说明（比如「这是私聊」）也放尾部，避免污染稳定前缀
        tail_parts.append(note)
    if focus:
        # 聚焦指令放最后：离生成最近，锚定效果最强
        tail_parts.append(focus)
    if images:
        # 图片挂在最后一条 user 上，这里补一句，提醒模型"这一条是有图的"
        tail_parts.append(f"（这条消息附带了 {len(images)} 张图片，请直接看图回应；看不清就直说看不清，不要假装在分析。）")
    tail = "\n".join(part for part in tail_parts if part)
    if messages[-1]["role"] == "user":
        messages[-1] = {
            "role": "user",
            "content": f"{messages[-1]['content']}\n\n{tail}",
        }
    else:
        messages.append({"role": "user", "content": tail})
    if images:
        # 图片只挂在最后一条 user 消息上：模型看到的永远是"这个人刚发的这张图"，
        # 也不会因为历史里长期挂着 base64 把 prompt 撑爆。
        messages[-1]["images"] = list(images)
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


def _hard_split(text: str, limit: int) -> list[str]:
    """把超长的一段按 limit 硬切开，尽量落在句末标点上。"""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(char) for char in _SENTENCE_ENDINGS)
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


def split_reply(text: str) -> list[str]:
    """按配置把一条回复切成多条气泡（留空 / 0 表示不切）。"""
    chunks = [text]
    pattern = (cfg.SPLIT_PATTERN or "").strip()
    if pattern:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:  # 配置里写坏了就退化成整条发送
            logger.warning(f"分条正则 {pattern!r} 无效，已忽略：{exc}")
        else:
            chunks = [piece for chunk in chunks for piece in compiled.split(chunk)]

    limit = int(cfg.SPLIT_MAX_CHARS or 0)
    if limit > 0:
        chunks = [piece for chunk in chunks for piece in _hard_split(chunk, limit)]

    return [piece for piece in (item.strip() for item in chunks) if piece]


def split_delay(previous_text: str) -> float:
    """两条气泡之间的停顿：基准 + 按上一条字数线性增加 + 随机浮动。"""
    base = max(0.0, float(cfg.SPLIT_DELAY_BASE))
    per_char = max(0.0, float(cfg.SPLIT_DELAY_PER_CHAR))
    jitter = max(0.0, float(cfg.SPLIT_DELAY_JITTER))

    delay = base + per_char * len(previous_text)
    if jitter:
        delay += random.uniform(-jitter, jitter)
    return max(0.0, delay)


def compose_messages(text: str, emotions: list[str], reply_id: int | None) -> list[Message]:
    """把回复组装成一条或多条消息；分条时第一段带引用和配图。"""
    chunks = split_reply(text or prompts.EMPTY_REPLY)
    if not chunks:
        chunks = [prompts.EMPTY_REPLY]

    emotion = random.choice(emotions) if emotions else None
    image = pick_emotion_image(emotion)
    use_image = image is not None and random.random() < cfg.SEND_IMAGE_PROBABILITY

    messages: list[Message] = []
    for index, chunk in enumerate(chunks):
        message = Message()
        if index == 0 and reply_id is not None and random.random() < cfg.REPLY_PROBABILITY:
            message.append(MessageSegment.reply(reply_id))
        body = prompts.decorate(chunk)
        if index == 0 and use_image:
            image_segment = MessageSegment.image(image)
            if random.random() < 0.5:
                message.extend([image_segment, MessageSegment.text(body)])
            else:
                message.extend([MessageSegment.text(body), image_segment])
        else:
            message.append(MessageSegment.text(body))
        messages.append(message)
    return messages


def compose_message(text: str, emotions: list[str], reply_id: int | None) -> Message:
    """兼容旧调用：只返回第一条消息。"""
    return compose_messages(text, emotions, reply_id)[0]


async def generate_reply(
    group_id: str,
    bot_id: int | str,
    reply_id: int | None = None,
    *,
    focus: str | None = None,
    note: str | None = None,
    images: list[dict[str, str]] | None = None,
) -> list[Message]:
    """调用模型并返回可直接发送的消息列表。调用方必须持有该群的锁。"""
    messages = build_messages(group_id, bot_id, focus=focus, note=note, images=images)
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

    return compose_messages(cleaned, emotions, reply_id)
