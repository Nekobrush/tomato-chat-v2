"""番茄猫娘 —— NoneBot2 / OneBot v11 插件。

指令前缀与旧版保持一致：`y`、`!`、`！`、`tomato`、`/`，
例如 `ychat`、`y chat`、`!chatset p 3%`、`tomato chatcls`。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from nonebot import get_driver, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

import bot_config as cfg

from . import pipeline
from .model import LLMError, llm
from .text import ParsedCommand, parse_command, segments_to_text

__plugin_meta__ = PluginMetadata(
    name="番茄猫娘",
    description="群聊猫娘 AI，带情绪表情包",
    usage="\n".join(
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
            "y chatset ss [源]  —— 从 ollama 同步模型列表",
            "y chatset c / r    —— 关闭 / 重新开启本群对话",
        ]
    ),
    type="application",
    supported_adapters={"~onebot.v11"},
)

# ---------------------------------------------------------------------------
# 群状态
# ---------------------------------------------------------------------------


@dataclass
class GroupState:
    enabled: bool = True
    water_enabled: bool = False
    water_probability: float = cfg.DEFAULT_WATER_PROBABILITY


STATES: dict[str, GroupState] = {group_id: GroupState() for group_id in cfg.ALLOW_GROUPS}

_superuser_cache: set[str] | None = None


def _superusers() -> set[str]:
    global _superuser_cache
    if _superuser_cache is None:
        _superuser_cache = {str(user) for user in get_driver().config.superusers}
    return _superuser_cache


def _state(group_id: str) -> GroupState:
    state = STATES.get(group_id)
    if state is None:
        state = GroupState()
        STATES[group_id] = state
    return state


def _is_superuser(event: GroupMessageEvent) -> bool:
    return str(event.user_id) in _superusers()


def _is_group_admin(event: GroupMessageEvent) -> bool:
    return getattr(event.sender, "role", None) in {"owner", "admin"}


def _can_manage(event: GroupMessageEvent) -> bool:
    return _is_superuser(event) or _is_group_admin(event)


def _nickname(event: GroupMessageEvent) -> str:
    sender = event.sender
    return getattr(sender, "card", None) or getattr(sender, "nickname", None) or str(event.user_id)


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------


def _allowed(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent) and str(event.group_id) in cfg.ALLOW_GROUPS


def _command_rule(event: Event) -> bool:
    if not _allowed(event):
        return False
    return parse_command(event.get_plaintext()) is not None


def _chat_rule(event: Event) -> bool:
    return _allowed(event)


# priority 1：指令；priority 10：普通聊天 / 水群
command_matcher = on_message(rule=_command_rule, priority=1, block=True)
chat_matcher = on_message(rule=_chat_rule, priority=10, block=False)


# ---------------------------------------------------------------------------
# 发送辅助
# ---------------------------------------------------------------------------


async def _send(bot: Bot, event: GroupMessageEvent, message: Message | str) -> None:
    await bot.send_group_msg(group_id=event.group_id, message=message)


async def _reply_text(bot: Bot, event: GroupMessageEvent, text: str) -> None:
    """带引用回复的提示语。"""
    message = MessageSegment.reply(event.message_id) + MessageSegment.text(text)
    await _send(bot, event, message)


async def _chat_and_reply(
    bot: Bot,
    event: GroupMessageEvent,
    text: str,
    *,
    silent: bool = False,
) -> None:
    """记录用户消息 → 调模型 → 发送（调用方已持有群锁）。"""
    group_id = str(event.group_id)
    pipeline.remember_user(group_id, _nickname(event), event.user_id, text)
    try:
        message = await pipeline.generate_reply(
            group_id,
            event.self_id,
            reply_id=event.message_id,
        )
    except LLMError as exc:
        logger.warning(f"[{group_id}] 生成回复失败：{exc}")
        if not silent:
            await _reply_text(bot, event, pipeline.fail_tip())
        return
    await _send(bot, event, message)


async def _record_only(group_id: str, event: GroupMessageEvent, text: str) -> None:
    """不回复，只记录上下文，并合并上一条未回复的用户消息。"""
    pipeline.remember_user(group_id, _nickname(event), event.user_id, text)
    pipeline.merge_pending_user(group_id)


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

    if event.is_tome():
        async with pipeline.group_lock(group_id):
            await _chat_and_reply(bot, event, text)
        return

    async with pipeline.group_lock(group_id):
        if not state.water_enabled or random_float() > state.water_probability:
            await _record_only(group_id, event, text)
            return
        await _chat_and_reply(bot, event, text, silent=True)


def random_float() -> float:
    """单独抽出来，方便测试时替换。"""
    return random.random()


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------


@command_matcher.handle()
async def handle_command(bot: Bot, event: GroupMessageEvent) -> None:
    parsed: ParsedCommand | None = parse_command(event.get_plaintext())
    if parsed is None:
        return
    group_id = str(event.group_id)
    state = _state(group_id)

    if parsed.name == "chat":
        await _cmd_chat(bot, event, state, parsed.args)
    elif parsed.name == "chatsys":
        await _cmd_chatsys(bot, event, parsed.args)
    elif parsed.name == "chatcls":
        await _cmd_chatcls(bot, event)
    elif parsed.name == "chatstart":
        await _cmd_switch_water(bot, event, state, enabled=True)
    elif parsed.name == "chatstop":
        await _cmd_switch_water(bot, event, state, enabled=False)
    elif parsed.name == "chatset":
        await _cmd_chatset(bot, event, state, parsed.args)


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
        await _reply_text(bot, event, f"想聊什么呢喵？可以这样：{cfg.COMMAND_PREFIXES[0]} chat 你好喵")
        return
    async with pipeline.group_lock(str(event.group_id)):
        await _chat_and_reply(bot, event, text)


_CHATSYS_SUFFIXES = ("--persistence", "--p")


async def _cmd_chatsys(bot: Bot, event: GroupMessageEvent, args: str) -> None:
    if not _is_superuser(event):
        await _reply_text(bot, event, "只有管理员才能修改系统设定喵")
        return
    text = args.strip()
    persist = False
    for suffix in _CHATSYS_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip()
            persist = True
            break
    if not text:
        await _reply_text(bot, event, "要在后面写上设定内容喵，例如：y chatsys 你是一只猫 --p")
        return
    pipeline.remember_system(str(event.group_id), text, persist=persist)
    tail = "（已持久化）" if persist else ""
    await _reply_text(bot, event, f"记住啦喵{tail}")


async def _cmd_chatcls(bot: Bot, event: GroupMessageEvent) -> None:
    if not _can_manage(event):
        await _reply_text(bot, event, "只有管理员才能清空记忆喵")
        return
    removed = pipeline.reset_group(str(event.group_id))
    await _reply_text(bot, event, f"已进行A级记忆删除喵（清除了 {removed} 条记录）")


async def _cmd_switch_water(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    *,
    enabled: bool,
) -> None:
    if not _can_manage(event):
        await _reply_text(bot, event, "只有管理员才能开关自动聊天喵")
        return
    state.water_enabled = enabled
    if enabled:
        await _reply_text(
            bot,
            event,
            f"开启本群自动聊天模式喵（{state.water_probability:.0%} 概率接话）",
        )
    else:
        await _reply_text(bot, event, "本群自动聊天已关闭喵，@我照样会回喵")


async def _cmd_chatset(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    args: str,
) -> None:
    parts = args.split(None, 1)
    if not parts:
        await _reply_text(bot, event, "用法：y chatset p|l|s|m|ss|c|r ...")
        return
    action = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""

    if action == "p":
        await _set_probability(bot, event, state, value)
    elif action == "l":
        await _reply_text(bot, event, llm.describe())
    elif action == "s":
        await _switch_source(bot, event, value)
    elif action == "m":
        await _switch_model(bot, event, value)
    elif action == "ss":
        await _sync_models(bot, event, value)
    elif action == "c":
        if not _can_manage(event):
            await _reply_text(bot, event, "只有管理员才能关闭本群对话喵")
            return
        state.enabled = False
        await _reply_text(bot, event, "本群对话已关闭喵，使用 chatset r 可以重新开启")
    elif action == "r":
        if not _can_manage(event):
            await _reply_text(bot, event, "只有管理员才能开启本群对话喵")
            return
        state.enabled = True
        await _reply_text(bot, event, "本群对话已重新开启喵")
    else:
        await _reply_text(bot, event, "没有这个设置项喵，可用：p / l / s / m / ss / c / r")


async def _set_probability(
    bot: Bot,
    event: GroupMessageEvent,
    state: GroupState,
    value: str,
) -> None:
    if not value:
        await _reply_text(bot, event, f"当前水群概率是 {state.water_probability:.2%} 喵")
        return
    percent = value.endswith("%")
    raw = value.rstrip("%").strip()
    try:
        number = float(raw)
    except ValueError:
        await _reply_text(bot, event, "概率要写成数字喵，例如 0.03 或 3%")
        return
    if percent or number > 1:
        number /= 100
    if number < 0:
        await _reply_text(bot, event, "概率不能是负数喵")
        return
    if number > 1:
        await _reply_text(bot, event, "概率不能大于 100% 喵")
        return
    if not _is_superuser(event) and number > cfg.NORMAL_USER_MAX_WATER_PROBABILITY:
        limit = cfg.NORMAL_USER_MAX_WATER_PROBABILITY
        await _reply_text(bot, event, f"普通群成员最多只能设置到 {limit:.1%} 喵")
        return
    state.water_probability = number
    await _reply_text(bot, event, f"水群概率已设为 {number:.2%} 喵")


async def _switch_source(bot: Bot, event: GroupMessageEvent, value: str) -> None:
    if not _is_superuser(event):
        await _reply_text(bot, event, "只有管理员才能切换模型源喵")
        return
    if not value:
        await _reply_text(bot, event, f"当前模型源：{llm.source}（{llm.label}）")
        return
    try:
        llm.switch_source(value)
    except LLMError as exc:
        await _reply_text(bot, event, str(exc))
        return
    await _reply_text(bot, event, llm.describe())


async def _switch_model(bot: Bot, event: GroupMessageEvent, value: str) -> None:
    if not _is_superuser(event):
        await _reply_text(bot, event, "只有管理员才能切换模型喵")
        return
    if not value:
        await _reply_text(bot, event, llm.describe())
        return
    try:
        number = int(value)
    except ValueError:
        await _reply_text(bot, event, "模型序号要是整数喵，先用 chatset l 看看列表")
        return
    try:
        name = llm.switch_model(number - 1)
    except LLMError as exc:
        await _reply_text(bot, event, str(exc))
        return
    await _reply_text(bot, event, f"已切换到 {name} 喵")


async def _sync_models(bot: Bot, event: GroupMessageEvent, value: str) -> None:
    if not _is_superuser(event):
        await _reply_text(bot, event, "只有管理员才能同步模型列表喵")
        return
    try:
        models = await llm.sync_models(value or None)
    except LLMError as exc:
        await _reply_text(bot, event, str(exc))
        return
    await _reply_text(bot, event, "已同步模型喵：\n" + "\n".join(f"{i + 1}. {m}" for i, m in enumerate(models)))
