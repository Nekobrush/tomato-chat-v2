"""排队与合并：同一人合并、不同人各回一条、过期丢弃、聚焦指令。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message

import bot_config as cfg
from bot_plugins.ai_chat import pipeline
from bot_plugins.ai_chat import queue as chat_queue
from bot_plugins.ai_chat.errors import LLMError

GROUP = 893


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, object]] = []

    async def send_group_msg(self, *, group_id: int, message) -> None:
        self.sent.append((group_id, message))

    @property
    def last_text(self) -> str:
        return self.sent[-1][1].extract_plain_text()


def make_event(
    *,
    user_id: int = 12345,
    message_id: int = 1001,
    age: float = 0.0,
    group_id: int = GROUP,
) -> SimpleNamespace:
    """队列只用到这几个字段，不必构造完整的 GroupMessageEvent。"""
    return SimpleNamespace(
        group_id=group_id,
        user_id=user_id,
        message_id=message_id,
        self_id=10000,
        time=time.time() - age,
        sender=SimpleNamespace(nickname="tester", card=""),
    )


@pytest.fixture(autouse=True)
def _fast_queue(monkeypatch: pytest.MonkeyPatch):
    # 测试里不需要去抖等待
    monkeypatch.setattr(cfg, "PENDING_DEBOUNCE", 0)
    chat_queue.reset(str(GROUP))
    yield
    chat_queue.reset(str(GROUP))


def _fake_generate(monkeypatch: pytest.MonkeyPatch, calls: list[str | None]) -> None:
    async def fake(group_id, bot_id, reply_id=None, *, focus=None, images=None):
        calls.append(focus)
        return [Message("好的喵")]

    monkeypatch.setattr(pipeline, "generate_reply", fake)
    monkeypatch.setattr(pipeline, "pick_emotion_image", lambda emotion: None)


async def test_same_user_messages_merge_into_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    _fake_generate(monkeypatch, calls)
    bot = FakeBot()

    for index, text in enumerate(["在吗", "今天好累", "陪我聊聊"], start=1):
        await chat_queue.enqueue(bot, make_event(message_id=1000 + index), text)
    await chat_queue.wait_idle(str(GROUP))

    assert len(calls) == 1, "同一个人连发三次应该只生成一次"
    assert calls[0] is not None
    assert "这 3 条消息" in calls[0]
    assert "陪我聊聊" in calls[0]
    assert bot.last_text == "好的喵"


async def test_different_users_each_get_a_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    _fake_generate(monkeypatch, calls)
    bot = FakeBot()

    await chat_queue.enqueue(bot, make_event(user_id=1, message_id=101), "今天吃什么")
    await chat_queue.enqueue(bot, make_event(user_id=2, message_id=102), "帮我查个东西")
    await chat_queue.wait_idle(str(GROUP))

    assert len(calls) == 2, "两个人各占一条待办，都要被回应"
    assert "今天吃什么" in calls[0]
    assert "帮我查个东西" in calls[1]
    assert len(bot.sent) == 2


async def test_stale_pending_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "PENDING_TTL", 0.05)
    calls: list[str | None] = []
    _fake_generate(monkeypatch, calls)
    bot = FakeBot()

    # 事件发生在 10 秒前，早就过期了
    await chat_queue.enqueue(bot, make_event(age=10), "很久以前的消息")
    await chat_queue.wait_idle(str(GROUP))

    assert calls == []
    assert bot.sent == []


async def test_generation_error_falls_back_to_tip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(group_id, bot_id, reply_id=None, *, focus=None, images=None):
        raise LLMError("模型炸了")

    monkeypatch.setattr(pipeline, "generate_reply", boom)
    monkeypatch.setattr(pipeline, "fail_tip", lambda: "出错啦喵")

    bot = FakeBot()
    await chat_queue.enqueue(bot, make_event(), "在吗")
    await chat_queue.wait_idle(str(GROUP))

    assert "出错啦喵" in bot.last_text
