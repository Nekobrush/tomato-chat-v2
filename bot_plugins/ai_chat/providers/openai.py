"""OpenAI 兼容 API 的模型源。

适用于 LM Studio、Ollama 的 `/v1` 兼容层、DeepSeek、OpenAI 以及各类 OneAPI 网关。
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from ..errors import LLMError
from .base import ChatProvider, GenerationParams, log_usage

_V1_PATTERN = re.compile(r"/v1(?:/|$)")
_NON_CHAT_PATTERN = re.compile(r"embed|rerank|bge-|text-similarity")


def normalize_base_url(url: str) -> str:
    """把用户填的地址规范成 `.../v1`，允许省略 `/v1`。"""
    base = url.strip().rstrip("/")
    if not _V1_PATTERN.search(base):
        base = f"{base}/v1"
    return base


def _snippet(response: httpx.Response, limit: int = 200) -> str:
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - 读不到响应体时不影响报错
        return ""
    text = " ".join(text.split())
    return text[:limit]


class OpenAICompatibleProvider(ChatProvider):
    kind = "openai"
    kind_label = "OpenAI 兼容"

    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        params: GenerationParams,
        timeout: float,
    ) -> None:
        super().__init__(name, spec, params=params, timeout=timeout)
        self.base_url = normalize_base_url(self.url)
        self._extra_body: dict[str, Any] = dict(spec.get("extra_body") or {})
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(spec.get("headers") or {})
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=timeout,
            headers=headers,
        )

    # ------------------------------------------------------------------ 请求体
    @property
    def request_params(self) -> dict[str, Any]:
        p = self.params
        body: dict[str, Any] = {
            "temperature": p.temperature,
            "top_p": p.top_p,
            "max_tokens": p.max_tokens,
        }
        if p.stop:
            body["stop"] = list(p.stop)
        # 部分服务器（LM Studio 等）支持额外参数，允许在配置里透传
        body.update(self._extra_body)
        if self.spec.get("send_sampling"):
            body["top_k"] = p.top_k
            body["repeat_penalty"] = p.repeat_penalty
        return body

    def _error(self, exc: httpx.HTTPStatusError) -> LLMError:
        response = exc.response
        detail = _snippet(response)
        suffix = f"：{detail}" if detail else ""
        return LLMError(f"{self.base_url} 返回 HTTP {response.status_code}{suffix}")

    # ------------------------------------------------------------------ 接口
    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("models")
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise self._error(exc) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"无法连接 {self.base_url}（OpenAI 兼容）：{exc}") from exc

        models = [
            str(item["id"])
            for item in data.get("data") or []
            if isinstance(item, dict) and item.get("id")
        ]
        # 向量 / 重排模型不能用于对话，同步时过滤掉
        models = [name for name in models if not _NON_CHAT_PATTERN.search(name.lower())]
        if not models:
            raise LLMError(f"{self.base_url} 没有返回任何模型喵")
        return models

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            **self.request_params,
        }
        started = time.perf_counter()
        try:
            response = await self._client.post("chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise self._error(exc) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"调用模型 {model} 失败（OpenAI 兼容）：{exc}") from exc

        usage = data.get("usage") or {}
        log_usage(
            self.label,
            model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            elapsed=time.perf_counter() - started,
        )

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型 {model} 没有返回 choices")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        if not content or not str(content).strip():
            # 推理模型（DeepSeek-R1、Qwen3 思考模式等）会把 token 全花在 reasoning_content 上
            if message.get("reasoning_content"):
                raise LLMError(
                    f"模型 {model} 的思考内容用尽了 max_tokens，没有留下正文喵。"
                    '可以在该模型源配置里加 extra_body: {"reasoning_effort": "none"}，'
                    "或者调大 bot_config.PREDICT"
                )
            raise LLMError(f"模型 {model} 返回了空内容")
        return str(content)

    async def aclose(self) -> None:
        await self._client.aclose()
