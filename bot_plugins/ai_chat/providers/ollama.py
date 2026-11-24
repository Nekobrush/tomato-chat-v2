"""ollama 原生 HTTP 协议的模型源。"""

from __future__ import annotations

import inspect
import time
from typing import Any

from ollama import AsyncClient, Options

from ..errors import LLMError
from .base import ChatProvider, GenerationParams, log_usage


def _format_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ollama 的图片放在消息的 ``images`` 字段里（裸 base64，不带 data: 前缀）。"""
    formatted: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "role": message.get("role", "user"),
            "content": message.get("content", ""),
        }
        images = message.get("images") or []
        if images:
            item["images"] = [image.get("base64", "") for image in images]
        formatted.append(item)
    return formatted


class OllamaProvider(ChatProvider):
    kind = "ollama"
    kind_label = "ollama"

    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        params: GenerationParams,
        timeout: float,
    ) -> None:
        super().__init__(name, spec, params=params, timeout=timeout)
        self._client = AsyncClient(host=self.url, timeout=timeout)

    @property
    def options(self) -> Options:
        p = self.params
        options = Options(
            num_ctx=p.num_ctx,
            repeat_penalty=p.repeat_penalty,
            num_predict=p.max_tokens,
            temperature=p.temperature,
            top_k=p.top_k,
            top_p=p.top_p,
        )
        if p.stop:
            options["stop"] = list(p.stop)
        return options

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.list()
        except Exception as exc:  # noqa: BLE001 - 统一包装成 LLMError
            raise LLMError(f"无法连接 {self.url}（ollama）：{exc}") from exc

        models = [
            getattr(item, "model", None) or getattr(item, "name", "")
            for item in getattr(response, "models", [])
        ]
        models = [str(name) for name in models if name]
        if not models:
            raise LLMError(f"{self.url} 里没有已安装的模型喵")
        return models

    async def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        started = time.perf_counter()
        try:
            response = await self._client.chat(
                model=model,
                messages=_format_messages(messages),
                options=self.options,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"调用模型 {model} 失败（ollama）：{exc}") from exc

        elapsed = time.perf_counter() - started
        prompt_tokens = getattr(response, "prompt_eval_count", None)
        completion_tokens = getattr(response, "eval_count", None)
        if isinstance(response, dict):
            prompt_tokens = prompt_tokens or response.get("prompt_eval_count")
            completion_tokens = completion_tokens or response.get("eval_count")
        log_usage(
            self.label,
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed=elapsed,
        )

        content = getattr(getattr(response, "message", None), "content", None)
        if content is None and isinstance(response, dict):
            content = (response.get("message") or {}).get("content")
        if not content or not str(content).strip():
            raise LLMError(f"模型 {model} 返回了空内容")
        return str(content)

    async def aclose(self) -> None:
        try:
            result = self._client.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响退出
            return
        if inspect.isawaitable(result):
            await result
