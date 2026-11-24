"""WebUI 的 HTTP API。

路由分两组：

- ``public_router``：``/api/health``，一键启动程序用来判断服务是否就绪；
- ``router``：其余接口，默认只允许本机访问；需要远程访问时设置环境变量
  ``WEBUI_TOKEN``，并在请求头带 ``X-WebUI-Token``。
"""

from __future__ import annotations

import copy
import os
import re
import secrets
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import bot_config as cfg

from . import config_store

VERSION = "1.0.0"
# 试聊走一个虚拟会话，键与"机器人 QQ 号"都可在 bot_config.py 里改
WEBUI_TEST_GROUP = cfg.WEBUI_SESSION_GROUP
WEBUI_SESSION_BOT_ID = cfg.WEBUI_SESSION_BOT_ID

STARTED_AT = time.time()
_RESTART_PENDING: set[str] = set()

_LOCAL_HOSTS = {
    "127.0.0.1",
    "::1",
    "::ffff:127.0.0.1",
    "localhost",
    "testclient",  # FastAPI TestClient
}

_GROUP_ID = re.compile(r"^\d{3,12}$")


# ---------------------------------------------------------------------------
# 访问控制
# ---------------------------------------------------------------------------


def _webui_token() -> str:
    return os.getenv("WEBUI_TOKEN", "").strip()


def _token_matches(request: Request) -> bool:
    expected = _webui_token()
    if not expected:
        return False
    supplied = (
        request.headers.get("x-webui-token")
        or request.query_params.get("token")
        or ""
    )
    return bool(supplied) and secrets.compare_digest(expected, supplied)


def require_access(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host in _LOCAL_HOSTS or _token_matches(request):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "WebUI 只允许本机访问喵。远程访问请设置环境变量 WEBUI_TOKEN，"
            "并在请求头 X-WebUI-Token 里带上它。"
        ),
    )


public_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_access)])


# ---------------------------------------------------------------------------
# 内部取模块（延迟 import，避免测试时提前触发插件初始化）
# ---------------------------------------------------------------------------


def _pipeline():
    from bot_plugins.ai_chat import pipeline

    return pipeline


def _runtime():
    from bot_plugins.ai_chat import runtime

    return runtime


def _llm():
    from bot_plugins.ai_chat.model import llm

    return llm


def _online_bots() -> dict[str, Any]:
    try:
        import nonebot

        return {str(self_id): bot for self_id, bot in nonebot.get_bots().items()}
    except Exception:  # noqa: BLE001 - 没初始化时当作离线
        return {}


# ---------------------------------------------------------------------------
# 数据组装
# ---------------------------------------------------------------------------


def _model_info() -> dict[str, Any]:
    from bot_plugins.ai_chat.errors import LLMError

    llm = _llm()
    info: dict[str, Any] = {
        "source": llm.source,
        "label": llm.source,
        "kind": "ollama",
        "model": None,
        "models": [],
        "describe": "",
        "vision": False,
        "sources": [],
    }
    try:
        info["label"] = llm.label
        info["kind"] = llm.kind
        info["models"] = llm.models
        info["model"] = llm.models[llm.index] if llm.models else None
        info["describe"] = llm.describe()
        info["vision"] = llm.supports_vision
    except LLMError as exc:
        info["describe"] = str(exc)

    for name, spec in llm.sources.items():
        info["sources"].append(
            {
                "name": name,
                "label": str(spec.get("label") or name),
                "kind": str(spec.get("type") or "ollama"),
                "url": str(spec.get("url") or ""),
                "models": [str(item) for item in spec.get("model") or []],
                "active": name == llm.source,
            }
        )
    return info


def _group_payload(group_id: str) -> dict[str, Any]:
    runtime = _runtime()
    pipeline = _pipeline()
    state = runtime.state(group_id)
    return {
        "group_id": group_id,
        "enabled": state.enabled,
        "water_enabled": state.water_enabled,
        "water_probability": state.water_probability,
        "records": pipeline.store.count(group_id),
        "max_records": pipeline.store.max_record,
    }


@lru_cache(maxsize=1)
def _emotion_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for emotion in cfg.EMOTIONS:
        folder = cfg.EMOTION_PIC_PATH / emotion
        try:
            counts[emotion] = sum(
                1
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in cfg.IMAGE_SUFFIXES
            )
        except OSError:
            counts[emotion] = 0
    return counts


# ---------------------------------------------------------------------------
# 健康检查与状态
# ---------------------------------------------------------------------------


@public_router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "name": "tomato-webui", "version": VERSION}


@router.get("/status")
async def status() -> dict[str, Any]:
    pipeline = _pipeline()
    bots = _online_bots()
    groups = [_group_payload(group_id) for group_id in sorted(cfg.ALLOW_GROUPS)]
    counts = _emotion_counts()
    return {
        "ok": True,
        "version": VERSION,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "started_at": datetime.fromtimestamp(STARTED_AT).isoformat(timespec="seconds"),
        "online": bool(bots),
        "self_ids": sorted(bots),
        "model": _model_info(),
        "groups": groups,
        "stats": {
            "group_count": len(cfg.ALLOW_GROUPS),
            "water_enabled": sum(1 for group in groups if group["water_enabled"]),
            "record_total": sum(group["records"] for group in groups),
            "max_memory": pipeline.store.max_record,
            "emotion_total": sum(counts.values()),
        },
        "emotions": {"counts": counts, "total": sum(counts.values())},
        "config": {
            "values": config_store.effective(),
            "fields": config_store.FIELDS,
        },
        "restart_required": sorted(_RESTART_PENDING),
    }


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class ConfigIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/config")
async def get_config() -> dict[str, Any]:
    return {
        "values": config_store.effective(),
        "fields": config_store.FIELDS,
        "restart_required": sorted(_RESTART_PENDING),
    }


@router.post("/config")
async def save_config(body: ConfigIn) -> dict[str, Any]:
    try:
        _, restart = config_store.save(body.values)
    except config_store.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config_store.sync_runtime_state()
    await config_store.reload_model()
    _RESTART_PENDING.update(restart)
    return {
        "values": config_store.effective(),
        "restart_required": sorted(_RESTART_PENDING),
    }


# ---------------------------------------------------------------------------
# 人格与提示词
# ---------------------------------------------------------------------------


def _prompts():
    """延迟导入，避免 WebUI 在没有 nonebot 初始化时 import 整个插件包。"""
    from bot_plugins.ai_chat import prompts

    return prompts


class PromptsIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/prompts")
async def get_prompts() -> dict[str, Any]:
    prompts = _prompts()
    return {
        "values": prompts.current(),
        "defaults": prompts.defaults(),
        "fields": prompts.FIELDS,
        "file": prompts.PROMPTS_FILE.name,
    }


@router.post("/prompts")
async def save_prompts(body: PromptsIn) -> dict[str, Any]:
    prompts = _prompts()
    try:
        values = prompts.save(body.values)
    except prompts.PromptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"values": values, "defaults": prompts.defaults(), "fields": prompts.FIELDS}


@router.post("/prompts/reset")
async def reset_prompts() -> dict[str, Any]:
    prompts = _prompts()
    return {"values": prompts.reset(), "defaults": prompts.defaults(), "fields": prompts.FIELDS}


# ---------------------------------------------------------------------------
# 群聊
# ---------------------------------------------------------------------------


class GroupIn(BaseModel):
    group_id: str = Field(min_length=3, max_length=12)


class GroupPatch(BaseModel):
    enabled: bool | None = None
    water_enabled: bool | None = None
    water_probability: float | None = Field(default=None, ge=0, le=1)


@router.post("/groups")
async def add_group(body: GroupIn) -> dict[str, Any]:
    group_id = body.group_id.strip()
    if not _GROUP_ID.match(group_id):
        raise HTTPException(status_code=400, detail="群号必须是 3-12 位数字喵")
    try:
        config_store.save({"ALLOW_GROUPS": sorted({*cfg.ALLOW_GROUPS, group_id})})
    except config_store.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config_store.sync_runtime_state()
    return _group_payload(group_id)


@router.delete("/groups/{group_id}")
async def remove_group(group_id: str) -> dict[str, Any]:
    if group_id not in cfg.ALLOW_GROUPS:
        raise HTTPException(status_code=404, detail="这个群不在白名单里喵")
    try:
        config_store.save({"ALLOW_GROUPS": sorted(cfg.ALLOW_GROUPS - {group_id})})
    except config_store.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config_store.sync_runtime_state()
    return {"ok": True, "removed": group_id}


@router.patch("/groups/{group_id}")
async def patch_group(group_id: str, body: GroupPatch) -> dict[str, Any]:
    if group_id not in cfg.ALLOW_GROUPS:
        raise HTTPException(status_code=404, detail="这个群不在白名单里喵")
    state = _runtime().state(group_id)
    if body.enabled is not None:
        state.enabled = body.enabled
    if body.water_enabled is not None:
        state.water_enabled = body.water_enabled
    if body.water_probability is not None:
        state.water_probability = body.water_probability
    return _group_payload(group_id)


@router.get("/groups/{group_id}/memory")
async def get_memory(group_id: str) -> dict[str, Any]:
    pipeline = _pipeline()
    records = pipeline.store.records(group_id)
    return {
        "group_id": group_id,
        "max_records": pipeline.store.max_record,
        "records": [
            {
                "role": record.role,
                "content": record.content,
                "mark": record.mark,
                "chars": len(record.content),
            }
            for record in records
        ],
    }


@router.delete("/groups/{group_id}/memory")
async def clear_memory(group_id: str) -> dict[str, Any]:
    removed = _pipeline().reset_group(group_id)
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# 试聊
# ---------------------------------------------------------------------------


class ChatIn(BaseModel):
    text: str = Field(default="", max_length=500)
    images: list[dict[str, str]] = Field(default_factory=list)


_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


def _validate_chat_images(images: list[dict[str, str]]) -> list[dict[str, str]]:
    """校验网页拖进来的图片，返回规范化后的列表。"""
    if not images:
        return []
    from bot_config import VISION_MAX_BYTES, VISION_MAX_IMAGES

    if len(images) > VISION_MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"一条消息最多 {VISION_MAX_IMAGES} 张图")
    limit = max(VISION_MAX_BYTES, 1)
    cleaned: list[dict[str, str]] = []
    for index, item in enumerate(images, start=1):
        payload = str(item.get("base64") or "").strip()
        mime = str(item.get("mime") or "").strip().lower()
        if not payload:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图没有数据")
        if mime == "image/jpg":
            mime = "image/jpeg"
        if mime not in _IMAGE_MIME:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图格式不支持：{mime or '未知'}")
        # base64 每 4 个字符还原 3 字节，留一点余量再比较
        if len(payload) * 3 // 4 > limit:
            raise HTTPException(status_code=400, detail=f"第 {index} 张图太大（上限 {limit // 1024} KB）")
        cleaned.append({"base64": payload, "mime": mime})
    return cleaned


@router.post("/chat")
async def webui_chat(body: ChatIn) -> dict[str, Any]:
    from bot_plugins.ai_chat.errors import LLMError
    from bot_plugins.ai_chat.text import extract_emotions

    pipeline = _pipeline()
    images = _validate_chat_images(body.images)
    text = body.text.strip()
    if not text and not images:
        raise HTTPException(status_code=400, detail="说点什么，或者拖一张图进来")
    if images and not _llm().supports_vision:
        raise HTTPException(
            status_code=400,
            detail="当前模型不支持看图：换一个多模态模型，或在「模型与设置 › 模型源」里把它的名字加进「能看图的模型」",
        )

    group_id = WEBUI_TEST_GROUP
    async with pipeline.group_lock(group_id):
        pipeline.remember_user(group_id, "网页访客", "webui", text or "[图片]")
        try:
            messages = await pipeline.generate_reply(
                group_id, WEBUI_SESSION_BOT_ID, images=images or None
            )
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    records = pipeline.store.records(group_id)
    assistant = next((item for item in reversed(records) if item.role == "assistant"), None)
    emotions: list[str] = []
    if assistant is not None:
        emotions, _ = extract_emotions(assistant.content)

    return {
        "reply": "".join(message.extract_plain_text() for message in messages),
        "has_image": any(segment.type == "image" for message in messages for segment in message),
        "emotions": emotions,
        "records": len(records),
    }


@router.post("/chat/reset")
async def reset_chat() -> dict[str, Any]:
    removed = _pipeline().reset_group(WEBUI_TEST_GROUP)
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


@router.get("/logs")
async def get_logs(lines: int = Query(default=200, ge=10, le=2000)) -> dict[str, Any]:
    path = cfg.BASE_DIR / "logs" / "bot.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"lines": [], "path": str(path), "hint": "还没有日志文件，启动后会自动创建"}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取日志失败：{exc}") from exc
    return {"lines": text.splitlines()[-lines:], "path": str(path)}


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


class SyncIn(BaseModel):
    source: str | None = None


class SwitchIn(BaseModel):
    source: str | None = None
    index: int | None = None


@router.post("/models/sync")
async def sync_models(body: SyncIn | None = None) -> dict[str, Any]:
    from bot_plugins.ai_chat.errors import LLMError

    llm = _llm()
    try:
        models = await llm.sync_models(body.source if body else None)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 把同步结果写进覆盖配置，重启后不用再同步
    config_store.save({"MODELS": copy.deepcopy(cfg.MODELS)})
    await config_store.reload_model()
    return {"models": models, "model": _model_info()}


@router.post("/models/switch")
async def switch_model(body: SwitchIn) -> dict[str, Any]:
    from bot_plugins.ai_chat.errors import LLMError

    llm = _llm()
    try:
        if body.source:
            llm.switch_source(body.source)
        if body.index is not None:
            llm.switch_model(body.index)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        config_store.save(
            {"DEFAULT_SOURCE": llm.source, "DEFAULT_MODEL_INDEX": llm.index}
        )
    except config_store.ConfigError as exc:  # pragma: no cover - 理论上不会发生
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model": _model_info()}


# ---------------------------------------------------------------------------
# 表情包预览
# ---------------------------------------------------------------------------


@router.get("/emotions/{emotion}")
async def emotion_preview(emotion: str) -> FileResponse:
    import random

    if emotion not in cfg.EMOTIONS:
        raise HTTPException(status_code=404, detail="没有这个情绪喵")
    candidates = _pipeline().emotion_candidates(emotion)
    if not candidates:
        raise HTTPException(status_code=404, detail="这个情绪还没有表情包喵")
    path = random.choice(candidates)
    return FileResponse(path)
