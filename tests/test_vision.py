"""图片提取与解码的单元测试。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

import bot_config as cfg
from bot_plugins.ai_chat import vision

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_image_sources_prefers_url() -> None:
    message = Message(
        MessageSegment("image", {"file": "a.jpg", "url": "https://example.com/a.jpg"})
    )
    assert vision.image_sources(message) == ["https://example.com/a.jpg"]


def test_image_sources_falls_back_to_file() -> None:
    message = Message(MessageSegment("image", {"file": "base64://QUJD"}))
    assert vision.image_sources(message) == ["base64://QUJD"]


def test_image_sources_includes_mface_and_ignores_text() -> None:
    message = Message(
        [
            MessageSegment.text("看图"),
            MessageSegment("mface", {"url": "https://example.com/m.png"}),
            MessageSegment.face(1),
        ]
    )
    assert vision.image_sources(message) == ["https://example.com/m.png"]


def test_image_sources_honours_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VISION_MAX_IMAGES", 2)
    message = Message(
        [
            MessageSegment("image", {"file": f"{index}.jpg", "url": f"https://example.com/{index}.jpg"})
            for index in range(5)
        ]
    )
    assert len(vision.image_sources(message)) == 2


async def test_load_part_decodes_base64() -> None:
    part = await vision.load_part("base64://" + base64.b64encode(PNG_BYTES).decode())
    assert part is not None
    assert part["mime"] == "image/png"
    assert base64.b64decode(part["base64"]) == PNG_BYTES


async def test_load_part_reads_local_file(tmp_path: Path) -> None:
    path = tmp_path / "a.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 8)
    part = await vision.load_part(str(path))
    assert part is not None
    assert part["mime"] == "image/jpeg"


async def test_load_part_rejects_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "VISION_MAX_BYTES", 4)
    assert await vision.load_part("base64://" + base64.b64encode(b"12345").decode()) is None


async def test_load_parts_skips_failures() -> None:
    good = "base64://" + base64.b64encode(PNG_BYTES).decode()
    parts = await vision.load_parts(["http://127.0.0.1:1/none.png", "/no/such/file.png", good])
    assert len(parts) == 1
