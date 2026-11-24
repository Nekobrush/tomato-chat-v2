"""模型提供方注册表：根据 `bot_config.MODELS` 里的 `type` 创建对应客户端。"""

from __future__ import annotations

from typing import Any

from ..errors import LLMError
from .base import ChatProvider, GenerationParams
from .ollama import OllamaProvider
from .openai import OpenAICompatibleProvider

_PROVIDER_TYPES: dict[str, type[ChatProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatibleProvider,
}

# 常见别名，方便在配置里随手写
_ALIASES = {
    "ollama": "ollama",
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "lmstudio": "openai",
    "lm-studio": "openai",
    "api": "openai",
}

SUPPORTED_TYPES: tuple[str, ...] = tuple(sorted(_PROVIDER_TYPES))


def provider_type(spec: dict[str, Any]) -> str:
    """解析配置里的 type；缺省按 ollama 处理（兼容旧配置）。"""
    raw = str(spec.get("type") or "ollama").strip().lower()
    return _ALIASES.get(raw, raw)


def create_provider(
    name: str,
    spec: dict[str, Any],
    *,
    params: GenerationParams,
    timeout: float,
) -> ChatProvider:
    if not isinstance(spec, dict):
        raise LLMError(f"模型源 {name!r} 的配置必须是字典")
    kind = provider_type(spec)
    cls = _PROVIDER_TYPES.get(kind)
    if cls is None:
        raise LLMError(
            f"模型源 {name!r} 的 type={kind!r} 不受支持喵（可用：{'、'.join(SUPPORTED_TYPES)}）"
        )
    return cls(name, spec, params=params, timeout=timeout)


__all__ = [
    "ChatProvider",
    "GenerationParams",
    "SUPPORTED_TYPES",
    "create_provider",
    "provider_type",
]
