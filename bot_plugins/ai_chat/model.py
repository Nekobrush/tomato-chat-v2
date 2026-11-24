"""模型服务：管理模型源、当前模型和生成流程。

真正的协议细节在 `providers/` 里（ollama / OpenAI 兼容），这里只负责：
- 读取 `bot_config.MODELS`；
- 切换模型源 / 模型；
- 从模型源同步候选模型列表；
- 串行调用生成，并把异常统一成 `LLMError`。
"""

from __future__ import annotations

import asyncio
from typing import Any

from nonebot.log import logger

import bot_config as cfg

from .errors import LLMError
from .providers import SUPPORTED_TYPES, create_provider
from .providers.base import ChatProvider, GenerationParams

__all__ = ["LLMError", "LLMService", "llm"]


class LLMService:
    def __init__(self, sources: dict[str, Any] | None = None) -> None:
        self._sources = sources
        self.params = GenerationParams.from_config()
        self._providers: dict[str, ChatProvider] = {}
        # 本地小模型串行生成更稳定，也避免并发把记忆顺序打乱
        self._lock = asyncio.Lock()

        available = self.sources
        default = cfg.DEFAULT_SOURCE
        self.source = default if default in available else next(iter(available), "")
        if default not in available and self.source:
            logger.warning(f"默认模型源 {default!r} 不存在，改用 {self.source!r}")
        self.index = cfg.DEFAULT_MODEL_INDEX
        self._clamp_index()

    # ------------------------------------------------------------------ 配置
    @property
    def sources(self) -> dict[str, Any]:
        return self._sources if self._sources is not None else cfg.MODELS

    @property
    def spec(self) -> dict[str, Any]:
        if not self.source:
            raise LLMError("还没有配置任何模型源喵（见 bot_config.MODELS）")
        return self.sources[self.source]

    @property
    def label(self) -> str:
        return str(self.spec.get("label") or self.source)

    @property
    def kind(self) -> str:
        return str(self.spec.get("type") or "ollama")

    @property
    def models(self) -> list[str]:
        return [str(name) for name in self.spec.get("model") or []]

    @property
    def model(self) -> str:
        models = self.models
        if not models:
            raise LLMError(
                f"模型源 {self.source!r} 还没有可用模型，先执行 chatset ss 同步一下喵"
            )
        return models[self.index]

    def _clamp_index(self) -> None:
        count = len(self.models)
        self.index = 0 if count == 0 else max(0, min(self.index, count - 1))

    # ------------------------------------------------------------------ provider
    def _provider_for(self, source: str) -> ChatProvider:
        provider = self._providers.get(source)
        if provider is None:
            provider = create_provider(
                source,
                self.sources[source],
                params=self.params,
                timeout=cfg.REQUEST_TIMEOUT,
            )
            self._providers[source] = provider
        return provider

    @property
    def provider(self) -> ChatProvider:
        if not self.source:
            raise LLMError("还没有配置任何模型源喵（见 bot_config.MODELS）")
        return self._provider_for(self.source)

    async def aclose(self) -> None:
        """关闭所有已创建的客户端（NoneBot 退出时调用）。"""
        for name, provider in list(self._providers.items()):
            try:
                await provider.aclose()
            except Exception as exc:  # noqa: BLE001 - 关闭失败只记日志
                logger.debug(f"关闭模型源 {name} 失败：{exc}")
        self._providers.clear()

    def refresh_params(self) -> None:
        """重新读取生成参数，让网页里改的数值立即生效。"""
        self.params = GenerationParams.from_config()
        for provider in self._providers.values():
            provider.params = self.params

    async def reload(self) -> None:
        """配置（MODELS / 默认源）被网页改写后重建 provider 与当前选择。"""
        await self.aclose()
        self._sources = None  # 单例重新跟随 cfg.MODELS
        available = self.sources
        default = cfg.DEFAULT_SOURCE
        self.source = default if default in available else next(iter(available), "")
        self.index = cfg.DEFAULT_MODEL_INDEX
        self.refresh_params()
        self._clamp_index()
        logger.info(f"模型配置已重载：{self.source}（{self.label}）")

    # ------------------------------------------------------------------ 切换
    def switch_source(self, source: str) -> None:
        if source not in self.sources:
            available = "、".join(self.sources) or "无"
            raise LLMError(f"没有名为 {source!r} 的模型源喵（可用：{available}）")
        self.source = source
        self._clamp_index()
        logger.info(f"切换模型源：{source}（{self.label}）")

    def switch_model(self, index: int) -> str:
        models = self.models
        if not 0 <= index < len(models):
            raise LLMError(f"模型编号 {index + 1} 超出范围喵（共 {len(models)} 个）")
        self.index = index
        logger.info(f"切换模型：{models[index]}")
        return models[index]

    async def sync_models(self, source: str | None = None) -> list[str]:
        """向模型源拉取可用模型列表，并写回配置（仅本次运行有效）。"""
        target = source or self.source
        if target not in self.sources:
            available = "、".join(self.sources) or "无"
            raise LLMError(f"没有名为 {target!r} 的模型源喵（可用：{available}）")

        provider = self._provider_for(target)
        models = await provider.list_models()
        self.sources[target]["model"] = models
        if target == self.source:
            self._clamp_index()
        logger.info(f"同步模型源 {target}：{models}")
        return models

    def describe(self) -> str:
        if not self.source:
            return "还没有配置任何模型源喵（见 bot_config.MODELS）"

        spec = self.spec
        lines = [f"当前模型源：{self.source}（{self.label}）"]
        lines.append(f"地址：{spec.get('url')}（{self.kind}）")

        models = self.models
        if models:
            lines.append(f"当前模型：{models[self.index]}（{self.index + 1}/{len(models)}）")
            lines.append("可选模型：" + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(models)))
        else:
            lines.append("当前模型：（未设置，先执行 chatset ss 同步）")

        lines.append("可用模型源：" + "、".join(self.sources))
        lines.append("支持的类型：" + "、".join(SUPPORTED_TYPES))
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
        provider = self.provider
        async with self._lock:
            try:
                content = await provider.chat(target, messages)
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001 - 兜底包装，避免打断事件处理
                raise LLMError(f"调用模型 {target} 失败：{exc}") from exc
        logger.debug(f"模型 {target} 生成 {len(content)} 字")
        return content


# 全局单例
llm = LLMService()
