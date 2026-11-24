"""指令处理。

所有指令统一成 `(bot, event, state, args)` 的签名，由 `handle_command` 查表分发。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

import bot_config as cfg

from . import pipeline
from .errors import LLMError
from .messaging import reply_text
from .model import llm
from .queue import enqueue
from .runtime import GroupState, can_manage, is_superuser, nickname, state as get_state
from .text import parse_command

USAGE_TEXT = "\n".join(
    [
        "y chat <内容>      —— 主动和猫娘聊天",
        "y chatsys <内容>   —— 追加系统设定（--p 持久化）",
        "y chatcls          —— 清空本群记忆",
        "y chatstart        —— 开启本群自动聊天（水群）",
        "y chatstop         —— 关闭本群自动聊天",
        "y chatset p <概率> —— 设置水群概率",
        "y chatset l        —— 查看当前模型",
        "y chatset s <源>   —— 切换模型源",
        "y chatset m <序号> —— 切换模型",
        "y chatset ss [源]  —— 同步模型源里的模型列表",
        "y chatset c / r    —— 关闭 / 重新开启本群对话",
    ]
)

_CHATSYS_SUFFIXES = ("--persistence", "--p")

Handler = Callable[[Bot, GroupMessageEvent, GroupState, str], Awaitable[None]]


# ---------------------------------------------------------------------------
# 指令入口
# ---------------------------------------------------------------------------


async def handle_command(bot: Bot, event: GroupMessageEvent) -> None:
    parsed = parse_command(event.get_plaintext())
    if parsed is None:
        return
    handler = _HANDLERS.get(parsed.name)
    if handler is None:
        return
    await handler(bot, event, get_state(str(event.group_id)), parsed.args)


# ---------------------------------------------------------------------------
# chat / chatsys / chatcls
# ---------------------------------------------------------------------------


async def _cmd_chat(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    if not state.enabled:
        return
    text = args.strip()
    if not text:
        await reply_text(
            bot,
            event,
            f"想聊什么呢喵？可以这样：{ cfg.COMMAND_PREFIXES[0] } chat 你好喵",
        )
        return
    group_id = str(event.group_id)
    occurred_at = int(event.time) * 1000 if event.time else None
    pipeline.remember_user(group_id, nickname(event), event.user_id, text, occurred_at)
    await enqueue(bot, event, text)


async def _cmd_chatsys(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    if not is_superuser(event):
        await reply_text(bot, event, "只有管理员才能修改系统设定喵")
        return

    text = args.strip()
    persist = False
    for suffix in _CHATSYS_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip()
            persist = True
            break
    if not text:
        await reply_text(bot, event, "要在后面写上设定内容喵，例如：y chatsys 你是一只猫 --p")
        return

    pipeline.remember_system(str(event.group_id), text, persist=persist)
    tail = "（已持久化）" if persist else ""
    await reply_text(bot, event, f"记住啦喵{tail}")


async def _cmd_chatcls(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    if not can_manage(event):
        await reply_text(bot, event, "只有管理员才能清空记忆喵")
        return
    removed = pipeline.reset_group(str(event.group_id))
    await reply_text(bot, event, f"已进行A级记忆删除喵（清除了 {removed} 条记录）")


# ---------------------------------------------------------------------------
# 自动聊天开关
# ---------------------------------------------------------------------------


async def _cmd_switch_water(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
    *,
    enabled: bool,
) -> None:
    if not can_manage(event):
        await reply_text(bot, event, "只有管理员才能开关自动聊天喵")
        return
    state.water_enabled = enabled
    if enabled:
        await reply_text(
            bot,
            event,
            f"开启本群自动聊天模式喵（{state.water_probability:.0%} 概率接话）",
        )
    else:
        await reply_text(bot, event, "本群自动聊天已关闭喵，@我照样会回喵")


async def _cmd_chatstart(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    await _cmd_switch_water(bot, event, state, args, enabled=True)


async def _cmd_chatstop(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    await _cmd_switch_water(bot, event, state, args, enabled=False)


# ---------------------------------------------------------------------------
# chatset
# ---------------------------------------------------------------------------

_CHATSET_USAGE = "用法：y chatset p|l|s|m|ss|c|r ..."


async def _cmd_chatset(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    parts = args.split(None, 1)
    if not parts:
        await reply_text(bot, event, _CHATSET_USAGE)
        return

    action = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""
    handler = _CHATSET_ACTIONS.get(action)
    if handler is None:
        await reply_text(bot, event, "没有这个设置项喵，可用：p / l / s / m / ss / c / r")
        return
    await handler(bot, event, state, value)


async def _set_probability(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not value:
        await reply_text(bot, event, f"当前水群概率是 {state.water_probability:.2%} 喵")
        return

    percent = value.endswith("%")
    raw = value.rstrip("%").strip()
    try:
        number = float(raw)
    except ValueError:
        await reply_text(bot, event, "概率要写成数字喵，例如 0.03 或 3%")
        return

    if percent or number > 1:
        number /= 100
    if number < 0:
        await reply_text(bot, event, "概率不能是负数喵")
        return
    if number > 1:
        await reply_text(bot, event, "概率不能大于 100% 喵")
        return

    limit = cfg.NORMAL_USER_MAX_WATER_PROBABILITY
    if not is_superuser(event) and number > limit:
        await reply_text(bot, event, f"普通群成员最多只能设置到 {limit:.1%} 喵")
        return

    state.water_probability = number
    await reply_text(bot, event, f"水群概率已设为 {number:.2%} 喵")


async def _show_models(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    await reply_text(bot, event, llm.describe())


async def _switch_source(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not is_superuser(event):
        await reply_text(bot, event, "只有管理员才能切换模型源喵")
        return
    if not value:
        await reply_text(bot, event, llm.describe())
        return
    try:
        llm.switch_source(value)
    except LLMError as exc:
        await reply_text(bot, event, str(exc))
        return
    await reply_text(bot, event, llm.describe())


async def _switch_model(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not is_superuser(event):
        await reply_text(bot, event, "只有管理员才能切换模型喵")
        return
    if not value:
        await reply_text(bot, event, llm.describe())
        return
    try:
        number = int(value)
    except ValueError:
        await reply_text(bot, event, "模型序号要是整数喵，先用 chatset l 看看列表")
        return
    try:
        name = llm.switch_model(number - 1)
    except LLMError as exc:
        await reply_text(bot, event, str(exc))
        return
    await reply_text(bot, event, f"已切换到 {name} 喵")


async def _sync_models(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not is_superuser(event):
        await reply_text(bot, event, "只有管理员才能同步模型列表喵")
        return
    try:
        models = await llm.sync_models(value or None)
    except LLMError as exc:
        await reply_text(bot, event, str(exc))
        return
    listing = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(models))
    await reply_text(bot, event, f"已同步模型喵：\n{listing}")


async def _close_group(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not can_manage(event):
        await reply_text(bot, event, "只有管理员才能关闭本群对话喵")
        return
    state.enabled = False
    await reply_text(bot, event, "本群对话已关闭喵，使用 chatset r 可以重新开启")


async def _open_group(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not can_manage(event):
        await reply_text(bot, event, "只有管理员才能开启本群对话喵")
        return
    state.enabled = True
    await reply_text(bot, event, "本群对话已重新开启喵")


_CHATSET_ACTIONS: dict[str, Handler] = {
    "p": _set_probability,
    "l": _show_models,
    "s": _switch_source,
    "m": _switch_model,
    "ss": _sync_models,
    "c": _close_group,
    "r": _open_group,
}

_HANDLERS: dict[str, Handler] = {
    "chat": _cmd_chat,
    "chatsys": _cmd_chatsys,
    "chatcls": _cmd_chatcls,
    "chatstart": _cmd_chatstart,
    "chatstop": _cmd_chatstop,
    "chatset": _cmd_chatset,
}
