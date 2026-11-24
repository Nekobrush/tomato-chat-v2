"""WebUI 的配置存储。

- 网页上保存的设置写到项目根目录的 ``webui_config.json``；
- ``bot_config.py`` 在导入时读取该文件覆盖默认值，所以重启后依然生效；
- 所有字段都会在保存前校验，坏值直接拒绝，不会写进文件。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import bot_config as cfg

OVERLAY_NAME = "webui_config.json"

# 网页表单里展示的字段。kind 决定输入控件和校验方式。
FIELDS: list[dict[str, Any]] = [
    {"key": "ALLOW_GROUPS", "label": "允许的群号", "kind": "groups",
     "help": "严格白名单：不在列表里的群不会收到任何回复"},
    {"key": "DEFAULT_SOURCE", "label": "默认模型源", "kind": "source"},
    {"key": "DEFAULT_MODEL_INDEX", "label": "默认模型序号", "kind": "int", "min": 0, "max": 999},
    {"key": "MODELS", "label": "模型源", "kind": "models"},
    {"key": "GLOBAL_CTX", "label": "上下文长度", "kind": "int", "min": 512, "max": 131072,
     "help": "仅 ollama 源生效；OpenAI 兼容源由服务端自己决定"},
    {"key": "REPEAT_PENALTY", "label": "不重复率", "kind": "float", "min": 0, "max": 4, "step": 0.05,
     "help": "ollama 直接生效；OpenAI 兼容源需在模型源里开启 send_sampling 或写进 extra_body"},
    {"key": "PREDICT", "label": "最大生成 token", "kind": "int", "min": 16, "max": 4096,
     "help": "本地生成速度约 20 tok/s，这个值直接决定最长等待时间"},
    {"key": "TEMPERATURE", "label": "温度", "kind": "float", "min": 0, "max": 2, "step": 0.05},
    {"key": "TOP_K", "label": "top_k", "kind": "int", "min": 0, "max": 1000,
     "help": "ollama 直接生效；OpenAI 兼容源需开启 send_sampling"},
    {"key": "TOP_P", "label": "top_p", "kind": "float", "min": 0, "max": 1, "step": 0.005},
    {"key": "REQUEST_TIMEOUT", "label": "请求超时（秒）", "kind": "float", "min": 5, "max": 600},
    {"key": "DEFAULT_WATER_PROBABILITY", "label": "默认水群概率", "kind": "prob"},
    {"key": "SEND_IMAGE_PROBABILITY", "label": "附带表情包概率", "kind": "prob"},
    {"key": "REPLY_PROBABILITY", "label": "引用回复概率", "kind": "prob"},
    {"key": "NORMAL_USER_MAX_WATER_PROBABILITY", "label": "普通成员概率上限", "kind": "prob"},
    {"key": "MAX_MEMORY", "label": "每群记忆条数", "kind": "int", "min": 2, "max": 100},
    {"key": "MERGE_MAX_LEN", "label": "合并消息长度", "kind": "int", "min": 16, "max": 4096},
    {"key": "RESP_LEN_LIMIT", "label": "回复过长阈值", "kind": "int", "min": 16, "max": 1000},
    {"key": "SPLIT_PATTERN", "label": "分条正则", "kind": "pattern",
     "help": "按这个正则把一条回复切成多条气泡，留空则不按正则切（例如 [。！？]；想保留标点用 ([。！？])）"},
    {"key": "SPLIT_MAX_CHARS", "label": "分条字符数", "kind": "int", "min": 0, "max": 500,
     "help": "每条气泡的字符上限，0 表示不按字数截断；留空即不截断"},
    {"key": "SPLIT_DELAY_BASE", "label": "分条基准间隔（秒）", "kind": "float", "min": 0, "max": 30, "step": 0.1,
     "help": "每两条气泡之间的基础停顿"},
    {"key": "SPLIT_DELAY_PER_CHAR", "label": "每条字符追加（秒）", "kind": "float", "min": 0, "max": 1, "step": 0.001,
     "help": "按上一条的字数线性增加停顿，例如 0.02 表示每个字多停 20 毫秒"},
    {"key": "SPLIT_DELAY_JITTER", "label": "分条随机浮动（秒）", "kind": "float", "min": 0, "max": 10, "step": 0.1,
     "help": "在算出的停顿上随机加减这么多秒，避免机器般均匀"},
    {"key": "VISION_ENABLED", "label": "识别图片（多模态）", "kind": "bool",
     "help": "只有当前模型支持看图时才生效；@ 机器人或回复机器人时必看，其他人发的图按水群概率随机看"},
    {"key": "VISION_MAX_IMAGES", "label": "每条最多看几张图", "kind": "int", "min": 1, "max": 10},
    {"key": "VISION_MAX_BYTES", "label": "单张图大小上限（字节）", "kind": "int", "min": 65536, "max": 33554432},
]

FIELD_BY_KEY: dict[str, dict[str, Any]] = {field["key"]: field for field in FIELDS}

_GROUP_ID = re.compile(r"^\d{3,12}$")
_SOURCE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")

_PROVIDER_TYPES = {
    "ollama",
    "openai",
    "openai-compatible",
    "openai_compatible",
    "lmstudio",
    "lm-studio",
    "api",
}


class ConfigError(ValueError):
    """配置校验失败。"""


def overlay_path() -> Path:
    return cfg.BASE_DIR / OVERLAY_NAME


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def effective() -> dict[str, Any]:
    """当前生效的业务配置（JSON 友好）。"""
    values: dict[str, Any] = {}
    for field in FIELDS:
        key = field["key"]
        if key == "ALLOW_GROUPS":
            values[key] = sorted(str(gid) for gid in cfg.ALLOW_GROUPS)
        elif key == "MODELS":
            values[key] = deepcopy(cfg.MODELS)
        else:
            values[key] = getattr(cfg, key)
    return values


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _as_number(field: dict[str, Any], value: Any) -> float | int:
    if isinstance(value, bool):
        raise ConfigError(f"{field['label']} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field['label']} 必须是数字") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ConfigError(f"{field['label']} 必须是有限数字")
    if "min" in field and number < field["min"]:
        raise ConfigError(f"{field['label']} 不能小于 {field['min']}")
    if "max" in field and number > field["max"]:
        raise ConfigError(f"{field['label']} 不能大于 {field['max']}")
    if field["kind"] == "int":
        if number != int(number):
            raise ConfigError(f"{field['label']} 必须是整数")
        return int(number)
    return float(number)


def _validate_models(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise ConfigError("至少需要配置一个模型源")
    cleaned: dict[str, dict[str, Any]] = {}
    for raw_name, spec in value.items():
        name = str(raw_name).strip()
        if not _SOURCE_NAME.match(name):
            raise ConfigError(f"模型源名称 {name!r} 只能用字母、数字、下划线、点和横线")
        if not isinstance(spec, dict):
            raise ConfigError(f"模型源 {name} 的配置必须是对象")
        url = str(spec.get("url") or "").strip()
        if not url:
            raise ConfigError(f"模型源 {name} 缺少 url")
        provider_type = str(spec.get("type") or "ollama").strip().lower()
        if provider_type not in _PROVIDER_TYPES:
            raise ConfigError(f"模型源 {name} 的 type 不支持：{provider_type}")
        raw_models = spec.get("model") or []
        if not isinstance(raw_models, list):
            raise ConfigError(f"模型源 {name} 的 model 必须是列表")
        models = [str(item).strip() for item in raw_models if str(item).strip()]

        entry: dict[str, Any] = {"type": provider_type, "url": url, "model": models}
        if spec.get("label"):
            entry["label"] = str(spec["label"]).strip()
        if spec.get("api_key"):
            entry["api_key"] = str(spec["api_key"]).strip()
        if isinstance(spec.get("extra_body"), dict) and spec["extra_body"]:
            entry["extra_body"] = spec["extra_body"]
        vision = spec.get("vision")
        if isinstance(vision, bool):
            entry["vision"] = vision
        elif isinstance(vision, (list, tuple)) and vision:
            picked = [str(item).strip() for item in vision if str(item).strip()]
            if not picked:
                raise ConfigError(f"模型源 {name} 的 vision 列表不能全是空白")
            for item in picked:
                if len(item) > 64:
                    raise ConfigError(f"模型源 {name} 的 vision 里有一项太长：{item[:20]}…")
            entry["vision"] = picked
        elif isinstance(vision, str) and vision.strip():
            text = vision.strip()
            if text.lower() in {"*", "all", "true", "yes", "on"}:
                entry["vision"] = True
            elif text.lower() in {"false", "no", "off", "none"}:
                entry["vision"] = False
            else:
                entry["vision"] = [part.strip() for part in text.split(",") if part.strip()]
        if isinstance(spec.get("headers"), dict) and spec["headers"]:
            entry["headers"] = {str(k): str(v) for k, v in spec["headers"].items()}
        cleaned[name] = entry
    return cleaned


def _coerce(field: dict[str, Any], value: Any) -> Any:
    kind = field["kind"]
    if kind == "str":
        text = str(value).strip()
        if not text:
            raise ConfigError(f"{field['label']} 不能为空")
        if len(text) > 32:
            raise ConfigError(f"{field['label']} 不能超过 32 个字符")
        return text
    if kind == "groups":
        if not isinstance(value, (list, tuple, set)):
            raise ConfigError("群号列表必须是数组")
        groups: list[str] = []
        for item in value:
            gid = str(item).strip()
            if not gid:
                continue
            if not _GROUP_ID.match(gid):
                raise ConfigError(f"群号 {gid!r} 不合法（应为 3-12 位数字）")
            if gid not in groups:
                groups.append(gid)
        return groups
    if kind == "source":
        source = str(value).strip()
        if not source:
            raise ConfigError("默认模型源不能为空")
        return source
    if kind == "models":
        return _validate_models(value)
    if kind == "pattern":
        pattern = str(value).strip()
        if not pattern:
            return ""
        if len(pattern) > 200:
            raise ConfigError(f"{field['label']} 不能超过 200 个字符")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"{field['label']} 不是合法的正则：{exc}") from exc
        return pattern
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        raise ConfigError(f"{field['label']} 只能是开或关")
    if kind == "prob":
        field = {**field, "min": 0, "max": 1}
        return _as_number(field, value)
    return _as_number(field, value)


# ---------------------------------------------------------------------------
# 保存与应用
# ---------------------------------------------------------------------------


def apply_to_cfg(values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key == "ALLOW_GROUPS":
            setattr(cfg, key, {str(gid) for gid in value})
        else:
            setattr(cfg, key, value)


def save(values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """校验并保存配置，同时更新当前进程的 ``bot_config``。

    返回 (合并后的完整配置, 需要重启才生效的字段)。
    """
    current = effective()
    clean: dict[str, Any] = {}
    for key, raw in values.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            continue  # 忽略未知字段，方便前端多传
        clean[key] = _coerce(field, raw)

    merged = {**current, **clean}
    merged["ALLOW_GROUPS"] = sorted(merged["ALLOW_GROUPS"])

    models = merged["MODELS"]
    if merged["DEFAULT_SOURCE"] not in models:
        raise ConfigError(f"默认模型源 {merged['DEFAULT_SOURCE']!r} 不在模型源列表里")
    picks = models[merged["DEFAULT_SOURCE"]].get("model") or []
    index = int(merged["DEFAULT_MODEL_INDEX"])
    merged["DEFAULT_MODEL_INDEX"] = max(0, min(index, len(picks) - 1)) if picks else 0

    overlay_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    apply_to_cfg(merged)

    restart = [
        field["key"]
        for field in FIELDS
        if field.get("restart") and merged.get(field["key"]) != current.get(field["key"])
    ]
    return merged, restart


# ---------------------------------------------------------------------------
# 在线应用
# ---------------------------------------------------------------------------


def sync_runtime_state() -> None:
    """让群状态、记忆容量跟随配置变化。"""
    from bot_plugins.ai_chat import pipeline
    from bot_plugins.ai_chat.runtime import STATES, GroupState

    for group_id in cfg.ALLOW_GROUPS:
        STATES.setdefault(str(group_id), GroupState())
    for group_id in list(STATES):
        if group_id not in cfg.ALLOW_GROUPS:
            STATES.pop(group_id)

    pipeline.store.set_max_record(cfg.MAX_MEMORY)


async def reload_model() -> None:
    """重建模型 provider，让模型源 / 参数改动立即生效。"""
    from bot_plugins.ai_chat.model import llm

    await llm.reload()
