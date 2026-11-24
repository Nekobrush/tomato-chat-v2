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


def test_system_context_uses_ms() -> None:
    context = pipeline.system_context(10000)
    assert context["role"] == "system"
    assert "毫秒级时间戳" in context["content"]
    assert "10000" in context["content"]
