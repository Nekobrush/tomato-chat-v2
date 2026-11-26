"""酒馆角色卡解析（cards.py）的单元测试。"""

from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

from bot_plugins.ai_chat import cards


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _png(text_chunks: dict[str, str]) -> bytes:
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    for key, value in text_chunks.items():
        data += _chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("utf-8"))
    return data + _chunk(b"IEND", b"")


def test_png_text_chunks_reads_text_blocks():
    png = _png({"chara": "hello", "Comment": "说明"})

    assert cards.png_text_chunks(png) == {"chara": "hello", "Comment": "说明"}


def test_card_json_from_png_prefers_ccv3():
    v3 = {"name": "新版"}
    v2 = {"name": "旧版"}
    png = _png(
        {
            "chara": base64.b64encode(json.dumps(v2).encode()).decode(),
            "ccv3": base64.b64encode(json.dumps(v3).encode()).decode(),
        }
    )

    assert cards.card_json_from_png(png)["name"] == "新版"


def test_card_json_from_png_rejects_missing_and_bad_payload():
    with pytest.raises(cards.CardError):
        cards.card_json_from_png(_png({"Comment": "没有角色卡"}))

    broken = base64.b64encode(b"not json").decode()
    with pytest.raises(cards.CardError):
        cards.card_json_from_png(_png({"chara": broken}))

    with pytest.raises(cards.CardError):
        cards.card_json_from_png(b"not a png")


def test_normalize_card_supports_v1_and_v2():
    v2 = {"data": {"name": "小满", "description": "猫娘"}}
    v1 = {"name": "小满", "description": "猫娘"}

    assert cards.normalize_card(v2)["name"] == "小满"
    assert cards.normalize_card(v1)["description"] == "猫娘"

    with pytest.raises(cards.CardError):
        cards.normalize_card({"description": ""})


def test_split_examples_takes_last_block():
    user, char = cards._split_examples(
        "<START>\n{{user}}: 早\n{{char}}: 早喵\n<START>\n{{user}}: 晚上好\n{{char}}: 晚上好喵"
    )

    assert user == "晚上好"
    assert char == "晚上好喵"


def test_card_profile_builds_persona_examples_and_book():
    card = cards.normalize_card(
        {
            "name": "小满",
            "description": "爱睡觉的猫娘。",
            "personality": "懒散。",
            "scenario": "便利店。",
            "first_mes": "哈啊……",
            "mes_example": "{{user}}: 你好\n{{char}}: 你好喵",
            "character_book": {
                "entries": [{"uid": 0, "key": ["小满"], "content": "喜欢罐头。", "disable": False}]
            },
        }
    )

    profile = cards.card_profile(card)

    assert "爱睡觉的猫娘" in profile["persona"]
    assert "【场景】" in profile["persona"]
    assert profile["example_user"] == "你好"
    assert profile["example_assistant"] == "你好喵"
    assert profile["book"] is not None
    assert profile["book"].kind == "character"
    assert profile["book"].entries[0].content == "喜欢罐头。"


def test_card_profile_falls_back_to_first_message():
    card = cards.normalize_card({"name": "小满", "description": "猫娘", "first_mes": "你好喵"})

    profile = cards.card_profile(card)

    assert profile["example_assistant"] == "你好喵"
    assert profile["book"] is None
