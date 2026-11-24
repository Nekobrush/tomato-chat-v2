"""模型服务测试：切换、同步、错误包装（不访问网络）。"""

import pytest

import bot_config as cfg
import bot_plugins.ai_chat.model as model_module
from bot_plugins.ai_chat.model import LLMError, LLMService


def test_default_model() -> None:
    service = LLMService()
    assert service.source == cfg.DEFAULT_SOURCE
    assert service.model == cfg.MODELS[cfg.DEFAULT_SOURCE]["model"][0]


def test_describe_contains_model() -> None:
    service = LLMService()
    text = service.describe()
    assert service.model in text
    assert service.source in text


def test_switch_source_unknown() -> None:
    service = LLMService()
    with pytest.raises(LLMError):
        service.switch_source("nope")


def test_switch_model_out_of_range() -> None:
    service = LLMService()
    with pytest.raises(LLMError):
        service.switch_model(99)
    with pytest.raises(LLMError):
        service.switch_model(-1)


def test_switch_model_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cfg.MODELS["local"], "model", ["m1", "m2"])
    service = LLMService()
    assert service.switch_model(1) == "m2"
    assert service.model == "m2"


async def test_sync_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cfg.MODELS["local"], "model", ["old"])

    class FakeModel:
        def __init__(self, name: str) -> None:
            self.model = name

    class FakeListResponse:
        models = [FakeModel("a:1"), FakeModel("b:2")]

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list(self) -> FakeListResponse:
            return FakeListResponse()

    monkeypatch.setattr(model_module, "AsyncClient", FakeClient)
    service = LLMService()
    models = await service.sync_models()
    assert models == ["a:1", "b:2"]
    assert cfg.MODELS["local"]["model"] == ["a:1", "b:2"]


async def test_sync_models_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list(self):
            raise ConnectionError("refused")

    monkeypatch.setattr(model_module, "AsyncClient", FakeClient)
    service = LLMService()
    with pytest.raises(LLMError):
        await service.sync_models()


class _FakeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


async def test_chat_returns_content() -> None:
    service = LLMService()
    fake = _FakeClient(_FakeResponse("你好喵"))
    service._client = fake
    result = await service.chat([{"role": "user", "content": "hi"}])
    assert result == "你好喵"
    assert fake.calls[0]["model"] == service.model
    assert fake.calls[0]["options"]["temperature"] == cfg.TEMPERATURE


async def test_chat_empty_content_raises() -> None:
    service = LLMService()
    service._client = _FakeClient(_FakeResponse("   "))
    with pytest.raises(LLMError):
        await service.chat([{"role": "user", "content": "hi"}])


async def test_chat_wraps_error() -> None:
    service = LLMService()
    service._client = _FakeClient(error=RuntimeError("boom"))
    with pytest.raises(LLMError, match="boom"):
        await service.chat([{"role": "user", "content": "hi"}])
