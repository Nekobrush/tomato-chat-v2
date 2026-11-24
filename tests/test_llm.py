"""模型服务测试：切换、同步、错误包装（不访问网络）。"""

import pytest

import bot_config as cfg
from bot_plugins.ai_chat.errors import LLMError
from bot_plugins.ai_chat.model import LLMService
from bot_plugins.ai_chat.providers.base import GenerationParams


def _service(**overrides) -> LLMService:
    sources = {
        "local": {
            "type": "ollama",
            "url": "http://127.0.0.1:1",
            "model": ["m1", "m2"],
        }
    }
    sources.update(overrides)
    return LLMService(sources)


def test_default_model() -> None:
    service = LLMService()
    assert service.source == cfg.DEFAULT_SOURCE
    assert service.model == cfg.MODELS[cfg.DEFAULT_SOURCE]["model"][0]


def test_describe_contains_model() -> None:
    service = LLMService()
    text = service.describe()
    assert service.model in text
    assert service.source in text
    assert "支持的类型" in text
    assert "ollama" in text


def test_switch_source_unknown() -> None:
    service = LLMService()
    with pytest.raises(LLMError, match="没有名为"):
        service.switch_source("nope")


def test_switch_source_ok() -> None:
    service = _service(
        api={
            "type": "openai",
            "url": "http://127.0.0.1:1",
            "model": ["gpt"],
        }
    )
    service.switch_source("api")
    assert service.source == "api"
    assert service.kind == "openai"
    assert service.model == "gpt"


def test_switch_model_out_of_range() -> None:
    service = _service()
    with pytest.raises(LLMError):
        service.switch_model(99)
    with pytest.raises(LLMError):
        service.switch_model(-1)


def test_switch_model_ok() -> None:
    service = _service()
    assert service.switch_model(1) == "m2"
    assert service.model == "m2"


def test_clamp_index_after_switch() -> None:
    service = _service(other={"type": "ollama", "url": "http://127.0.0.1:1", "model": ["only"]})
    service.switch_model(1)
    service.switch_source("other")
    assert service.index == 0
    assert service.model == "only"


async def test_sync_models(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    provider = service.provider

    async def fake_list() -> list[str]:
        return ["a:1", "b:2"]

    monkeypatch.setattr(provider, "list_models", fake_list)
    models = await service.sync_models()
    assert models == ["a:1", "b:2"]
    assert service.sources["local"]["model"] == ["a:1", "b:2"]


async def test_sync_models_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()

    async def boom() -> list[str]:
        raise LLMError("refused")

    monkeypatch.setattr(service.provider, "list_models", boom)
    with pytest.raises(LLMError, match="refused"):
        await service.sync_models()


async def test_sync_models_unknown_source() -> None:
    service = _service()
    with pytest.raises(LLMError, match="没有名为"):
        await service.sync_models("nope")


async def test_chat_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    calls: list[dict] = []

    async def fake_chat(model: str, messages: list[dict[str, str]]) -> str:
        calls.append({"model": model, "messages": messages})
        return "你好喵"

    monkeypatch.setattr(service.provider, "chat", fake_chat)
    messages = [{"role": "user", "content": "hi"}]
    result = await service.chat(messages)
    assert result == "你好喵"
    assert calls[0]["model"] == service.model
    assert calls[0]["messages"] == messages


async def test_chat_empty_content_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()

    async def empty(model: str, messages: list[dict[str, str]]) -> str:
        raise LLMError("模型返回了空内容")

    monkeypatch.setattr(service.provider, "chat", empty)
    with pytest.raises(LLMError):
        await service.chat([{"role": "user", "content": "hi"}])


async def test_chat_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()

    async def boom(model: str, messages: list[dict[str, str]]) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(service.provider, "chat", boom)
    with pytest.raises(LLMError, match="boom"):
        await service.chat([{"role": "user", "content": "hi"}])


def test_generation_params_from_config() -> None:
    params = GenerationParams.from_config()
    assert params.temperature == cfg.TEMPERATURE
    assert params.top_p == cfg.TOP_P
    assert params.top_k == cfg.TOP_K
    assert params.max_tokens == cfg.PREDICT
    assert params.num_ctx == cfg.GLOBAL_CTX
    assert params.repeat_penalty == cfg.REPEAT_PENALTY
