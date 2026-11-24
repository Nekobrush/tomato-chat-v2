"""回复流水线测试：情绪提取、纠错提示、Message 组装（模型用假实现）。"""

import pytest

import bot_config as cfg
import bot_plugins.ai_chat.pipeline as pipeline
from bot_plugins.ai_chat.model import LLMError


def _fake_chat(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    async def fake(messages, *, model=None):
        return reply

    monkeypatch.setattr(pipeline.llm, "chat", fake)
    monkeypatch.setattr(pipeline, "pick_emotion_image", lambda emotion: None)
    # 不引用回复、不发表情包，保证结果可预期
    monkeypatch.setattr(pipeline.random, "random", lambda: 1.0)


async def test_generate_reply_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_chat(monkeypatch, "你好喵--*happy*--")
    group_id = "test-ok"
    pipeline.reset_group(group_id)

    message = await pipeline.generate_reply(group_id, 10000, reply_id=1)
    assert message.extract_plain_text() == "你好喵"
    records = pipeline.store.records(group_id)
    assert [r.role for r in records] == ["assistant"]
    assert "--*happy*--" in records[0].content


async def test_generate_reply_adds_tip_when_no_emotion(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_chat(monkeypatch, "你好喵")
    group_id = "test-noemotion"
    pipeline.reset_group(group_id)

    message = await pipeline.generate_reply(group_id, 10000)
    assert message.extract_plain_text() == "你好喵"
    records = pipeline.store.records(group_id)
    assert [r.role for r in records] == ["assistant", "system"]
    assert "情绪" in records[1].content


async def test_generate_reply_adds_tip_when_too_long(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_chat(monkeypatch, "喵" * (cfg.RESP_LEN_LIMIT + 1) + "--*happy*--")
    group_id = "test-long"
    pipeline.reset_group(group_id)

    await pipeline.generate_reply(group_id, 10000)
    records = pipeline.store.records(group_id)
    assert "太长" in records[-1].content


async def test_generate_reply_propagates_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(messages, *, model=None):
        raise LLMError("模型炸了")

    monkeypatch.setattr(pipeline.llm, "chat", boom)
    group_id = "test-error"
    pipeline.reset_group(group_id)

    with pytest.raises(LLMError):
        await pipeline.generate_reply(group_id, 10000)
    assert pipeline.store.count(group_id) == 0


def test_collect_tips() -> None:
    assert pipeline.collect_tips("你好喵", ["happy"]) == ""
    assert "情绪" in pipeline.collect_tips("你好喵", [])
    assert "喵" in pipeline.collect_tips("你好", ["happy"])
    assert "太长" in pipeline.collect_tips("喵" * (cfg.RESP_LEN_LIMIT + 1), ["happy"])


def test_compose_with_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "pick_emotion_image", lambda emotion: b"fake-image")
    monkeypatch.setattr(pipeline.random, "random", lambda: 0.0)
    message = pipeline.compose_message("你好喵", ["happy"], reply_id=7)
    types = [segment.type for segment in message]
    assert "reply" in types
    assert "image" in types
    assert "text" in types
    assert message.extract_plain_text() == "你好喵"


def test_compose_without_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "pick_emotion_image", lambda emotion: None)
    monkeypatch.setattr(pipeline.random, "random", lambda: 0.99)
    message = pipeline.compose_message("", [], reply_id=None)
    assert [segment.type for segment in message] == ["text"]
    assert message.extract_plain_text() == "喵？"


def test_format_user_message() -> None:
    text = pipeline.format_user_message("小明", 123, "你好")
    head, _, body = text.partition("\n")
    assert head.startswith("[") and head.endswith("]小明(123):")
    assert len(head) == len("[") + 13 + len("]小明(123):")
    assert body == "你好"


def test_build_messages_single_system() -> None:
    group_id = "test-build"
    pipeline.reset_group(group_id)
    pipeline.remember_system(group_id, "你是猫娘", persist=True)
    pipeline.remember_system(group_id, "上一轮太长了")
    pipeline.remember_user(group_id, "小明", 123, "你好")

    messages = pipeline.build_messages(group_id, 10000)
    assert messages[0]["role"] == "system"
    assert [m["role"] for m in messages].count("system") == 1
    # 持久化设定属于稳定前缀
    assert "你是猫娘" in messages[0]["content"]
    # 时间与纠错提示属于易变内容，放在最后一条 user 消息尾部，前缀才可缓存
    assert "当前毫秒级时间戳:" not in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert "当前毫秒级时间戳:" in messages[-1]["content"]
    assert "上一轮太长了" in messages[-1]["content"]


def test_build_messages_prefix_is_stable() -> None:
    """连续两轮之间，除了新追加的那条，前缀必须逐字节一致。"""
    group_id = "test-stable"
    pipeline.reset_group(group_id)
    pipeline.remember_user(group_id, "小明", 123, "第一句")
    first = pipeline.build_messages(group_id, 10000)

    pipeline.remember_user(group_id, "小明", 123, "第二句")
    second = pipeline.build_messages(group_id, 10000)

    prefix = len(first) - 1  # 去掉上一轮末尾那条带时间戳的 user 消息
    assert second[:prefix] == first[:prefix]


def test_build_messages_strips_old_timestamps() -> None:
    group_id = "test-old"
    pipeline.reset_group(group_id)
    for index in range(4):
        pipeline.remember_user(group_id, "小明", 123, f"第{index}句", 1763000000000)

    messages = pipeline.build_messages(group_id, 10000)
    dialog = [m for m in messages if m["role"] in {"user", "assistant"}][-4:]
    assert dialog[0]["content"].startswith("小明(123):")
    assert dialog[-1]["content"].startswith("[1763000000000]")


def test_system_context_uses_ms() -> None:
    context = pipeline.system_context(10000)
    assert context["role"] == "system"
    assert "毫秒级时间戳" in context["content"]
    assert "10000" in context["content"]
