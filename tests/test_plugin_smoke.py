"""插件冒烟测试：真的初始化 NoneBot、加载插件、直接调用 handler。

不启动服务器、不连 ollama，只验证接线和指令逻辑。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender

ROOT = Path(__file__).resolve().parents[1]
SUPERUSER = 1919810


@pytest.fixture(scope="module")
def plugin():
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init(
        driver="~fastapi",
        host="127.0.0.1",
        port=1,
        superusers={str(SUPERUSER)},
        command_start={"/"},
        log_level="WARNING",
        _env_file=None,
    )
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugins(str(ROOT / "bot_plugins"))
    import bot_plugins.ai_chat as ai_chat

    return ai_chat


@pytest.fixture(autouse=True)
def _clean_state(plugin):
    from bot_plugins.ai_chat import queue as chat_queue

    chat_queue.reset("893")
    plugin.pipeline.reset_group("893")
    plugin.pipeline.reset_group("private:12345")
    plugin.STATES.clear()
    plugin.STATES["893"] = plugin.GroupState()
    yield
    chat_queue.reset("893")
    plugin.pipeline.reset_group("893")
    plugin.pipeline.reset_group("private:12345")


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, Message]] = []
        self.private: list[tuple[int, Message]] = []

    async def send_group_msg(self, *, group_id: int, message: Message) -> None:
        self.sent.append((group_id, message))

    async def send_private_msg(self, *, user_id: int, message: Message) -> None:
        self.private.append((user_id, message))

    @property
    def last_text(self) -> str:
        assert self.sent, "机器人没有发送任何消息"
        return self.sent[-1][1].extract_plain_text()

    @property
    def last_private_text(self) -> str:
        assert self.private, "机器人没有发送任何私聊消息"
        return self.private[-1][1].extract_plain_text()


def make_event(
    text: str = "",
    *,
    user_id: int = 12345,
    role: str = "member",
    to_me: bool = False,
    group_id: int = 893,
    message: Message | None = None,
) -> GroupMessageEvent:
    content = message if message is not None else Message(text)
    return GroupMessageEvent(
        time=int(time.time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1001,
        message=content,
        original_message=content,
        raw_message=content.extract_plain_text(),
        font=0,
        sender=Sender(user_id=user_id, nickname="tester", role=role),
        to_me=to_me,
        group_id=group_id,
    )


def test_plugin_registered(plugin) -> None:
    assert plugin.__plugin_meta__.name == "番茄猫娘"
    assert plugin.command_matcher.handlers
    assert plugin.chat_matcher.handlers


async def test_chatset_list(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset l"))
    assert "当前模型源" in bot.last_text


async def test_chatcls_permissions(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatcls"))
    assert "只有管理员" in bot.last_text

    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatcls", role="owner"))
    assert "记忆删除" in bot.last_text


async def test_chatsys_requires_superuser(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatsys 你好"))
    assert "只有管理员" in bot.last_text


async def test_chatsys_persistent(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatsys 你是猫 --p", user_id=SUPERUSER, role="owner"))
    assert "记住啦" in bot.last_text
    records = plugin.pipeline.store.records("893")
    assert records[-1].content == "你是猫"
    assert records[-1].mark == "persistence"
    assert plugin.pipeline.store.max_record == plugin.cfg.MAX_MEMORY + 1


async def test_chat_command(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chat(messages, *, model=None):
        return "你好喵--*happy*--"

    monkeypatch.setattr(plugin.pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(plugin.pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(plugin.pipeline.random, "random", lambda: 1.0)

    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chat 你好"))
    # 现在走队列：指令先入队，回复在后台生成
    from bot_plugins.ai_chat import queue as chat_queue

    await chat_queue.wait_idle("893")
    assert bot.last_text == "你好喵"
    roles = [record.role for record in plugin.pipeline.store.records("893")]
    assert roles == ["user", "assistant"]


async def test_chat_command_without_text(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("ychat"))
    assert "想聊什么" in bot.last_text


async def test_tome_message_auto_reply(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chat(messages, *, model=None):
        return "在的喵--*calm*--"

    monkeypatch.setattr(plugin.pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(plugin.pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(plugin.pipeline.random, "random", lambda: 1.0)

    bot = FakeBot()
    await plugin.handle_group_message(bot, make_event("在吗", to_me=True))
    from bot_plugins.ai_chat import queue as chat_queue

    await chat_queue.wait_idle("893")
    assert bot.last_text == "在的喵"


async def test_plain_message_without_water_only_records(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_group_message(bot, make_event("随便聊聊"))
    assert bot.sent == []
    records = plugin.pipeline.store.records("893")
    assert len(records) == 1
    assert records[0].role == "user"


async def test_plain_message_pure_image_ignored(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from nonebot.adapters.onebot.v11 import MessageSegment

    # 模型不支持看图时，行为退回旧版：纯图片消息直接忽略
    async def not_ready() -> bool:
        return False

    monkeypatch.setattr(plugin, "_vision_ready", not_ready)

    bot = FakeBot()
    event = make_event(message=Message(MessageSegment.image("a.jpg")))
    await plugin.handle_group_message(bot, event)
    assert bot.sent == []
    assert plugin.pipeline.store.count("893") == 0


async def test_chatset_probability_limits(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset p 50%"))
    assert "最多" in bot.last_text

    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset p 50%", user_id=SUPERUSER, role="owner"))
    assert plugin.STATES["893"].water_probability == pytest.approx(0.5)

    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset p 3%"))
    assert plugin.STATES["893"].water_probability == pytest.approx(0.03)


async def test_chatset_switch_source_invalid(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset s nope", user_id=SUPERUSER, role="owner"))
    assert "没有名为" in bot.last_text


async def test_chatset_unknown_action(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatset zzz"))
    assert "没有这个设置项" in bot.last_text


async def test_chatstart_chatstop(plugin) -> None:
    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatstart", role="admin"))
    assert plugin.STATES["893"].water_enabled is True

    bot = FakeBot()
    await plugin.handle_command(bot, make_event("y chatstop", role="admin"))
    assert plugin.STATES["893"].water_enabled is False


async def test_msg_outside_allowed_group_ignored(plugin) -> None:
    bot = FakeBot()
    event = make_event("你好", group_id=999, to_me=True)
    # 规则层会拦住不在白名单的群，这里直接验证规则函数
    assert plugin._chat_rule(event) is False
    assert plugin._command_rule(event) is False


# ---------------------------------------------------------------------------
# 私聊：一对一，不走群聊的白名单/水群/排队/权限
# ---------------------------------------------------------------------------


def make_private_event(text: str = "", *, user_id: int = 12345) -> PrivateMessageEvent:
    content = Message(text)
    return PrivateMessageEvent(
        time=int(time.time()),
        self_id=10000,
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=2001,
        message=content,
        original_message=content,
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname="tester"),
        to_me=True,
    )


async def test_private_message_replies(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chat(messages, *, model=None):
        return "在的喵--*happy*--"

    monkeypatch.setattr(plugin.pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(plugin.pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(plugin.pipeline.random, "random", lambda: 1.0)

    bot = FakeBot()
    await plugin.handle_private_message(bot, make_private_event("在吗"))
    assert bot.last_private_text == "在的喵"
    assert bot.sent == []
    # 私聊记忆单独存，不污染群记忆
    records = plugin.pipeline.store.records("private:12345")
    assert [record.role for record in records] == ["user", "assistant"]
    assert plugin.pipeline.store.count("893") == 0


async def test_private_disabled_by_config(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot_plugins.ai_chat import private as private_module

    monkeypatch.setattr(plugin.cfg, "PRIVATE_CHAT_ENABLED", False)
    assert private_module._private_rule(make_private_event("在吗")) is False


async def test_private_whitelist(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot_plugins.ai_chat import private as private_module

    monkeypatch.setattr(plugin.cfg, "ALLOW_PRIVATE_USERS", {"999"})
    assert private_module._private_rule(make_private_event("在吗")) is False
    assert private_module._private_rule(make_private_event("在吗", user_id=999)) is True


async def test_private_pure_image_ignored(plugin) -> None:
    from nonebot.adapters.onebot.v11 import MessageSegment

    bot = FakeBot()
    event = make_private_event()
    event.message = Message(MessageSegment.image("a.jpg"))
    await plugin.handle_private_message(bot, event)
    assert bot.private == []


# ---------------------------------------------------------------------------
# 多模态：@ / 回复必看，其他人按水群概率
# ---------------------------------------------------------------------------


def _patch_vision(plugin, monkeypatch: pytest.MonkeyPatch, parts=None) -> list[dict]:
    """让插件以为模型支持看图，并拦住真正的网络下载。"""
    from bot_plugins.ai_chat import vision

    seen: list[dict] = []
    payload = parts if parts is not None else [{"base64": "QUJD", "mime": "image/png"}]

    async def fake_load(sources):
        seen.append({"sources": list(sources)})
        return payload

    async def ready() -> bool:
        return True

    monkeypatch.setattr(plugin, "_vision_ready", ready)
    monkeypatch.setattr(vision, "load_parts", fake_load)
    return seen


async def test_tome_image_message_is_recognized(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from nonebot.adapters.onebot.v11 import MessageSegment
    from bot_plugins.ai_chat import queue as chat_queue

    downloaded = _patch_vision(plugin, monkeypatch)
    captured: dict = {}

    async def fake_chat(messages, *, model=None):
        captured["messages"] = messages
        return "好可爱的图--*happy*--"

    monkeypatch.setattr(plugin.pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(plugin.pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(plugin.pipeline.random, "random", lambda: 1.0)

    bot = FakeBot()
    event = make_event(
        to_me=True,
        message=Message(MessageSegment("image", {"file": "a.jpg", "url": "https://example.com/a.jpg"})),
    )
    await plugin.handle_group_message(bot, event)
    await chat_queue.wait_idle("893")

    assert bot.last_text == "好可爱的图"
    assert downloaded and downloaded[0]["sources"] == ["https://example.com/a.jpg"]
    assert captured["messages"][-1]["images"] == [{"base64": "QUJD", "mime": "image/png"}]
    # 记忆里留个占位符，否则历史里会出现一条空消息
    assert "[图片]" in plugin.pipeline.store.records("893")[0].content


async def test_water_image_recognized_when_roll_passes(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from nonebot.adapters.onebot.v11 import MessageSegment

    _patch_vision(plugin, monkeypatch)
    captured: dict = {}

    async def fake_chat(messages, *, model=None):
        captured["messages"] = messages
        return "好看--*curious*--"

    monkeypatch.setattr(plugin.pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(plugin.pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(plugin.pipeline.random, "random", lambda: 1.0)
    monkeypatch.setattr(plugin, "random_float", lambda: 0.0)

    state = plugin.STATES.setdefault("893", plugin.GroupState())
    state.water_enabled = True
    state.water_probability = 0.03

    bot = FakeBot()
    event = make_event(
        message=Message(MessageSegment("image", {"file": "b.jpg", "url": "https://example.com/b.jpg"}))
    )
    await plugin.handle_group_message(bot, event)

    assert bot.last_text == "好看"
    assert captured["messages"][-1]["images"][0]["base64"] == "QUJD"


async def test_water_image_not_downloaded_when_roll_fails(plugin, monkeypatch: pytest.MonkeyPatch) -> None:
    from nonebot.adapters.onebot.v11 import MessageSegment

    downloaded = _patch_vision(plugin, monkeypatch)
    monkeypatch.setattr(plugin, "random_float", lambda: 0.99)

    state = plugin.STATES.setdefault("893", plugin.GroupState())
    state.water_enabled = True
    state.water_probability = 0.03

    bot = FakeBot()
    event = make_event(
        message=Message(MessageSegment("image", {"file": "c.jpg", "url": "https://example.com/c.jpg"}))
    )
    await plugin.handle_group_message(bot, event)

    assert bot.sent == []
    assert downloaded == []  # 没轮到开口就不该下载
    assert plugin.pipeline.store.count("893") == 1
