"""酒馆角色卡（Character Card）解析：JSON 与 PNG 内嵌数据两种形态。

只依赖标准库：PNG 的 tEXt/iTXt 块手工解析，chara 字段是 base64 的 JSON。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from typing import Any

from . import worldbook

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CARD_DATA_KEYS = ("ccv3", "chara")


class CardError(ValueError):
    """角色卡无法解析。"""


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def png_text_chunks(data: bytes) -> dict[str, str]:
    """读取 PNG 的 tEXt / iTXt 块，返回 {关键字: 文本}。"""
    if not data.startswith(PNG_SIGNATURE):
        raise CardError("不是 PNG 文件")
    offset = len(PNG_SIGNATURE)
    found: dict[str, str] = {}
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        body = data[offset + 8 : offset + 8 + length]
        offset += 12 + length  # 长度 + 类型 + 数据 + CRC
        if len(body) != length:
            break
        if kind == b"tEXt":
            keyword, _, text = body.partition(b"\x00")
            found[_decode_text(keyword)] = _decode_text(text)
        elif kind == b"iTXt":
            keyword, _, rest = body.partition(b"\x00")
            if len(rest) < 2:
                continue
            compressed = rest[0]
            rest = rest[2:]  # 跳过压缩标志与压缩方法
            _, _, rest = rest.partition(b"\x00")  # 语言标签
            _, _, text = rest.partition(b"\x00")  # 译名
            payload = zlib.decompress(text) if compressed else text
            found[_decode_text(keyword)] = _decode_text(payload)
        elif kind == b"IEND":
            break
    return found


def card_json_from_png(data: bytes) -> dict[str, Any]:
    chunks = png_text_chunks(data)
    for key in CARD_DATA_KEYS:
        if key in chunks:
            try:
                raw = base64.b64decode(chunks[key].strip())
            except (ValueError, TypeError) as exc:
                raise CardError(f"角色卡 {key} 字段不是有效 base64：{exc}") from exc
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CardError(f"角色卡 {key} 字段不是有效 JSON：{exc}") from exc
            if isinstance(payload, dict):
                return payload
            raise CardError("角色卡 JSON 顶层必须是对象")
    raise CardError("这张 PNG 里没有找到角色卡数据（chara / ccv3 字段）")


def normalize_card(raw: dict[str, Any]) -> dict[str, Any]:
    """把 v1（字段平铺）与 v2（data 包裹）统一成同一个结构。"""
    if not isinstance(raw, dict):
        raise CardError("角色卡必须是 JSON 对象")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        raise CardError("角色卡 data 字段格式不对")
    name = str(data.get("name") or raw.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    personality = str(data.get("personality") or "").strip()
    scenario = str(data.get("scenario") or "").strip()
    first_mes = str(data.get("first_mes") or "").strip()
    mes_example = str(data.get("mes_example") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip()
    post_history = str(data.get("post_history_instructions") or "").strip()
    book_raw = data.get("character_book")
    if not name and not description and not personality:
        raise CardError("角色卡没有名字或人设内容")
    return {
        "name": name,
        "description": description,
        "personality": personality,
        "scenario": scenario,
        "first_mes": first_mes,
        "mes_example": mes_example,
        "system_prompt": system_prompt,
        "post_history": post_history,
        "book": book_raw if isinstance(book_raw, dict) else None,
        "raw": raw,
    }


def _split_examples(mes_example: str) -> tuple[str, str]:
    """从 mes_example 里取最后一组 {{user}}/{{char}} 对话。"""
    if not mes_example.strip():
        return "", ""
    blocks = [block for block in mes_example.split("<START>") if block.strip()]
    block = blocks[-1] if blocks else mes_example
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    user_line = ""
    char_line = ""
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("{{user}}:") or lowered.startswith("user:"):
            user_line = line.split(":", 1)[1].strip()
        elif lowered.startswith("{{char}}:") or lowered.startswith("char:"):
            char_line = line.split(":", 1)[1].strip()
        elif char_line:
            char_line = f"{char_line} {line}".strip()
        elif user_line:
            user_line = f"{user_line} {line}".strip()
    if not char_line and lines:
        char_line = lines[-1]
    return user_line, char_line


def card_profile(card: dict[str, Any]) -> dict[str, Any]:
    """把角色卡拆成「人格层 / 示例 / 设定书条目」。"""
    name = card["name"]
    persona_parts = [part for part in (card["description"], card["personality"]) if part]
    if card["system_prompt"]:
        persona_parts.append(card["system_prompt"])
    if card["scenario"]:
        persona_parts.append(f"【场景】\n{card['scenario']}")
    if card["post_history"]:
        persona_parts.append(f"【补充要求】\n{card['post_history']}")
    persona = "\n\n".join(persona_parts).strip()

    example_user, example_assistant = _split_examples(card["mes_example"])
    if not example_assistant and card["first_mes"]:
        example_assistant = card["first_mes"]

    book = None
    if card["book"]:
        try:
            book = worldbook.book_from_st(
                card["book"],
                fallback_name=f"{name or '角色卡'}的世界书",
                kind="character",
            )
        except ValueError:
            book = None
    return {
        "name": name,
        "persona": persona,
        "example_user": example_user or "",
        "example_assistant": example_assistant or "",
        "book": book,
    }
