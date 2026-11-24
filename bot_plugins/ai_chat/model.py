"""与 ollama 交互的模型服务。

负责：切换模型源 / 模型、同步候选模型列表、带统一 options 的对话调用。
"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot.log import logger
from ollama import AsyncClient, Options

import bot_config as cfg


class LLMError(RuntimeError):
    """模型调用或配置错误。"""


class LLMService:
    def __init__(self) -> None:
        if cfg.DEFAULT_SOURCE not in cfg.MODELS:
            raise LLMError(f"DEFAULT_SOURCE={cfg.DEFAULT_SOURCE!r} 不存在于 MODELS 中")
        self.source: str = cfg.DEFAULT_SOURCE
        if not self.models:
            raise LLMError(f"模型源 {self.source!r} 没有配置任何模型")
        self.index: int = min(cfg.DEFAULT_MODEL_INDEX, len(self.models) - 1)
        self._client: AsyncClient = self._make_client(self.source)
        # 本地小模型串行生成更稳定，也避免并发把记忆顺序打乱
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 配置
    @staticmethod
    def _make_client(source: str) -> AsyncClient:
        info = cfg.MODELS[source]
        return AsyncClient(host=info["url"], timeout=cfg.REQUEST_TIMEOUT)

    @property
    def info(self) -> dict[str, Any]:
        return cfg.MODELS[self.source]

    @property
    def label(self) -> str:
        return str(self.info.get("label", self.source))

    @property
    def models(self) -> list[str]:
        return list(self.info.get("model") or [])

    @property
    def model(self) -> str:
        models = self.models
        if not models:
            raise LLMError(f"模型源 {self.source!r} 没有配置任何模型，请先执行 chatset ss 同步")
        return models[self.index]

    @property
    def options(self) -> Options:
        return Options(
            num_ctx=cfg.GLOBAL_CTX,
            repeat_penalty=cfg.REPEAT_PENALTY,
            num_predict=cfg.PREDICT,
            temperature=cfg.TEMPERATURE,
            top_k=cfg.TOP_K,
            top_p=cfg.TOP_P,
        )

    # ------------------------------------------------------------------ 切换
    def switch_source(self, source: str) -> None:
        if source not in cfg.MODELS:
            available = "、".join(cfg.MODELS)
            raise LLMError(f"没有名为 {source!r} 的模型源喵（可用：{available}）")
        self.source = source
        self.index = min(self.index, max(len(self.models) - 1, 0))
        self._client = self._make_client(source)
        logger.info(f"切换模型源：{source}（{self.label}）")

    def switch_model(self, index: int) -> str:
        models = self.models
        if not 0 <= index < len(models):
            raise LLMError(f"模型编号 {index + 1} 超出范围喵（共 {len(models)} 个）")
        self.index = index
        logger.info(f"切换模型：{models[index]}")
        return models[index]

    async def sync_models(self, source: str | None = None) -> list[str]:
        """向 ollama 拉取该模型源下已安装的模型列表并写回配置。"""
        target = source or self.source
        if target not in cfg.MODELS:
            raise LLMError(f"没有名为 {target!r} 的模型源喵")
        info = cfg.MODELS[target]
        client = AsyncClient(host=info["url"], timeout=cfg.REQUEST_TIMEOUT)
        try:
            response = await client.list()
        except Exception as exc:  # noqa: BLE001 - 网络错误统一包装
            raise LLMError(f"无法连接 {info['url']}：{exc}") from exc

        models = [
            getattr(item, "model", None) or getattr(item, "name", "") for item in response.models
        ]
        models = [name for name in models if name]
        if not models:
            raise LLMError("ollama 里没有已安装的模型喵")
        info["model"] = models
        if self.source == target:
            self.index = min(self.index, len(models) - 1)
        logger.info(f"同步模型源 {target}：{models}")
        return models

    def describe(self) -> str:
        models = self.models
        lines = [f"当前模型源：{self.source}（{self.label}） @ {self.info.get('url')}"]
        lines.append(f"当前模型：{self.model}（{self.index + 1}/{len(models)}）")
        lines.append("可选模型：" + "、".join(f"{i + 1}.{name}" for i, name in enumerate(models)))
        return "\n".join(lines)

    # ------------------------------------------------------------------ 对话
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
    ) -> str:
        """调用模型生成回复，返回非空文本；失败抛出 LLMError。"""
        target = model or self.model
        async with self._lock:
            try:
                response = await self._client.chat(
                    model=target,
                    messages=messages,
                    options=self.options,
                )
            except Exception as exc:  # noqa: BLE001
                raise LLMError(f"调用模型 {target} 失败：{exc}") from exc

        content = getattr(getattr(response, "message", None), "content", None)
        if content is None and isinstance(response, dict):
            content = (response.get("message") or {}).get("content")
        if not content or not content.strip():
            raise LLMError(f"模型 {target} 返回了空内容")
        return content


# 全局单例
llm = LLMService()
