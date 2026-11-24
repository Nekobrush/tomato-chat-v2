"""模型提供方的抽象层。

- `GenerationParams`：与具体协议无关的生成参数，各 provider 自己映射。
- `ChatProvider`：一个模型源（一组地址 + 若干模型）的客户端接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import bot_config as cfg

from ..errors import LLMError


@dataclass(frozen=True, slots=True)
class GenerationParams:
    """统一的生成参数，避免各 provider 各读一份配置。"""

    temperature: float = 0.85
    top_p: float = 0.975
    top_k: int = 70
    max_tokens: int = 128
    num_ctx: int = 8192
    repeat_penalty: float = 1.55
    stop: tuple[str, ...] = ()

    @classmethod
    def from_config(cls) -> GenerationParams:
        return cls(
            temperature=getattr(cfg, "TEMPERATURE", cls.temperature),
            top_p=getattr(cfg, "TOP_P", cls.top_p),
            top_k=getattr(cfg, "TOP_K", cls.top_k),
            max_tokens=getattr(cfg, "PREDICT", cls.max_tokens),
            num_ctx=getattr(cfg, "GLOBAL_CTX", cls.num_ctx),
            repeat_penalty=getattr(cfg, "REPEAT_PENALTY", cls.repeat_penalty),
            stop=tuple(getattr(cfg, "STOP_SEQUENCES", ()) or ()),
        )


class ChatProvider(ABC):
    """模型源客户端。子类只需实现 `list_models` 与 `chat`。"""

    kind: ClassVar[str] = "base"
    kind_label: ClassVar[str] = "基础"

    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        params: GenerationParams,
        timeout: float,
    ) -> None:
        if not isinstance(spec, dict):
            raise LLMError(f"模型源 {name!r} 的配置必须是字典")
        url = str(spec.get("url") or "").strip()
        if not url:
            raise LLMError(f"模型源 {name!r} 缺少 url")
        self.name = name
        self.spec = spec
        self.params = params
        self.timeout = timeout
        self.url = url

    # ------------------------------------------------------------------ 元信息
    @property
    def label(self) -> str:
        return str(self.spec.get("label") or self.name)

    @property
    def api_key(self) -> str:
        key = self.spec.get("api_key")
        return str(key).strip() if key else ""

    def supports_vision(self, model: str | None = None) -> bool:
        """这个模型能不能看图。

        模型源里写 ``"vision": true/false`` 时以它为准；否则按模型名猜——
        LM Studio 和 ollama 都不会主动声明多模态能力，只能靠名字里的
        `vl` / `vision` / `llava` 这类关键字。
        """
        if not bool(getattr(cfg, "VISION_ENABLED", True)):
            return False
        override = self.spec.get("vision")
        if isinstance(override, bool):
            return override
        name = (model or "").lower()
        return any(
            token in name for token in getattr(cfg, "VISION_MODEL_PATTERNS", ())
        )

    def describe(self) -> str:
        return f"{self.label}（{self.kind_label}）"

    # ------------------------------------------------------------------ 接口
    @abstractmethod
    async def list_models(self) -> list[str]:
        """返回该模型源当前可用的模型名，用于 `chatset ss`。"""

    @abstractmethod
    async def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        """生成一次回复，返回非空文本；失败抛 `LLMError`。

        ``messages`` 里最后一条 user 消息可能带 ``images`` 字段
        （``[{"base64": ..., "mime": ...}]``），由各 provider 自己转成对应协议。
        """

    async def aclose(self) -> None:
        """释放底层连接，默认什么都不做。"""
        return None


def log_usage(
    label: str,
    model: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    elapsed: float,
) -> None:
    """打印一次调用的 token 用量与耗时。

    本地部署里 prompt 长度直接决定首字延迟，所以这行日志是优化的依据：
    前缀稳定后 prompt_tokens 应该从 900+ 掉到 200 左右。
    """
    if not getattr(cfg, "LOG_USAGE", True):
        return
    from nonebot.log import logger

    prompt = "?" if prompt_tokens is None else str(prompt_tokens)
    completion = "?" if completion_tokens is None else str(completion_tokens)
    speed = ""
    if completion_tokens and elapsed > 0:
        speed = f"，{completion_tokens / elapsed:.1f} tok/s"
    logger.info(
        f"模型调用 {label}/{model} prompt={prompt} completion={completion} "
        f"耗时={elapsed:.2f}s{speed}"
    )
