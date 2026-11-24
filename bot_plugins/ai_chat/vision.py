"""把消息里的图片变成模型能看的东西。

OneBot v11 的图片段只有两种可用信息：``url``（http 地址）或 ``file``
（可能是 ``base64://``、本地路径，也可能只是文件名）。各实现的习惯不一样，
所以这里按「base64 → http → 本地文件」的顺序尽量都试一遍，失败就跳过——
一张图读不到不该让整条回复失败。
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

import httpx
from nonebot.log import logger

import bot_config as cfg

# 图片段之外，QQ 的商城表情/收藏表情也会以图片形式出现
_IMAGE_SEGMENTS = {"image", "mface"}

_MAGIC_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def image_sources(message) -> list[str]:
    """按出现顺序取出消息里所有图片的来源（不做网络请求）。"""
    sources: list[str] = []
    for segment in message:
        if getattr(segment, "type", "") not in _IMAGE_SEGMENTS:
            continue
        data = getattr(segment, "data", {}) or {}
        for key in ("url", "file"):
            value = str(data.get(key) or "").strip()
            if value:
                sources.append(value)
                break
    return sources[: max(1, int(cfg.VISION_MAX_IMAGES))]


def _guess_mime(raw: bytes, source: str) -> str:
    for magic, mime in _MAGIC_MIME:
        if raw.startswith(magic):
            return mime
    suffix = Path(source.split("?")[0]).suffix.lower()
    guessed = mimetypes.guess_type(f"x{suffix}")[0]
    return guessed or "image/png"


def _decode(source: str) -> bytes | None:
    """base64:// / file:// / 本地路径 三种离线来源。"""
    if source.startswith("base64://"):
        try:
            return base64.b64decode(source[len("base64://"):], validate=False)
        except (binascii.Error, ValueError):
            return None
    path_text = source[len("file://"):] if source.startswith("file://") else source
    if "://" in path_text:
        return None
    path = Path(path_text)
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        return None
    return None


async def _download(source: str) -> bytes | None:
    limit = int(cfg.VISION_MAX_BYTES)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=True
        ) as client:
            response = await client.get(source)
            response.raise_for_status()
            if len(response.content) > limit:
                logger.info(f"图片超过 {limit} 字节，跳过：{source[:60]}")
                return None
            return response.content
    except (httpx.HTTPError, ValueError) as exc:
        logger.info(f"图片下载失败（{source[:60]}）：{exc}")
        return None


async def load_part(source: str) -> dict[str, str] | None:
    """把一张图读成 ``{"base64": ..., "mime": ...}``；失败返回 None。"""
    raw = _decode(source)
    if raw is None and source.lower().startswith(("http://", "https://")):
        raw = await _download(source)
    if not raw:
        return None
    if len(raw) > int(cfg.VISION_MAX_BYTES):
        logger.info(f"图片超过 {cfg.VISION_MAX_BYTES} 字节，跳过：{source[:60]}")
        return None
    return {"base64": base64.b64encode(raw).decode("ascii"), "mime": _guess_mime(raw, source)}


async def load_parts(sources: list[str]) -> list[dict[str, str]]:
    """批量读取，任何一张失败都不影响其它图片。"""
    parts: list[dict[str, str]] = []
    for source in sources:
        part = await load_part(source)
        if part is not None:
            parts.append(part)
    return parts
