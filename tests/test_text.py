"""纯文本处理测试：指令解析、CQ 清洗、emoji 过滤、情绪提取。"""

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from bot_plugins.ai_chat.text import (
    ParsedCommand,
    collapse_whitespace,
    extract_emotions,
    parse_command,
    remove_emojis,
    segments_to_text,
    strip_cq,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ychat 你好", ParsedCommand("chat", "你好")),
        ("y chat 你好", ParsedCommand("chat", "你好")),
        ("y\u3000chat 你好", ParsedCommand("chat", "你好")),
        ("!chatcls", ParsedCommand("chatcls", "")),
        ("！chatstop", ParsedCommand("chatstop", "")),
        ("tomato chat 在吗", ParsedCommand("chat", "在吗")),
        ("tomato chatchat", None),
        ("/chatset p 3%", ParsedCommand("chatset", "p 3%")),
        ("y chatsys 你是猫 --p", ParsedCommand("chatsys", "你是猫 --p")),
        ("  ychat  你好  ", ParsedCommand("chat", "你好")),
    ],
)
def test_parse_command(text: str, expected: ParsedCommand | None) -> None:
    assert parse_command(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "hello", "chat", "yes", "y", "tomato", "!unknown", "yunknown"],
)
def test_parse_command_rejects(text: str) -> None:
    assert parse_command(text) is None


def test_strip_cq() -> None:
    raw = "[CQ:at,qq=10000] 你好[CQ:image,file=a.jpg][CQ:face,id=1]"
    assert strip_cq(raw, 10000) == " 你好"


def test_strip_cq_other_at() -> None:
    raw = "[CQ:at,qq=123]看这个[CQ:at,qq=all]"
    assert strip_cq(raw, 10000) == "@123看这个"


def test_segments_to_text() -> None:
    message = Message(
        MessageSegment.text("hi")
        + MessageSegment.at(123)
        + MessageSegment.image("a.jpg")
        + MessageSegment.at(10000)
    )
    assert segments_to_text(message, 10000) == "hi@123"


def test_segments_to_text_pure_image() -> None:
    assert segments_to_text(Message(MessageSegment.image("a.jpg")), 10000) == ""


def test_remove_emojis() -> None:
    assert remove_emojis("你好🌈喵😀") == "你好喵"


def test_collapse_whitespace() -> None:
    assert collapse_whitespace("  a\n\nb\tc  ") == "a b c"


@pytest.mark.parametrize(
    ("text", "emotions", "cleaned"),
    [
        ("你好喵--*happy*--", ["happy"], "你好喵"),
        ("你好喵 -- * suki * --", ["suki"], "你好喵"),
        ("好的喵--*HAPPY*--", ["happy"], "好的喵"),
        ("嗯喵--*joy*--", ["happy"], "嗯喵"),
        ("嗯喵--*不存在*--", [], "嗯喵"),
        ("嗯喵 *happy*", ["happy"], "嗯喵"),
        ("嗯喵--*happy*----*sad*--", ["happy", "sad"], "嗯喵"),
        ("没有标记喵", [], "没有标记喵"),
    ],
)
def test_extract_emotions(text: str, emotions: list[str], cleaned: str) -> None:
    assert extract_emotions(text) == (emotions, cleaned)
