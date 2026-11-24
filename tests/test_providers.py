"""模型后端测试：OpenAI 兼容协议用 httpx.MockTransport，ollama 用假客户端。"""

from __future__ import annotations

import json

import httpx
import pytest

from bot_plugins.ai_chat.errors import LLMError
from bot_plugins.ai_chat.providers import SUPPORTED_TYPES, create_provider, provider_type
from bot_plugins.ai_chat.providers.base import GenerationParams
from bot_plugins.ai_chat.providers.ollama import OllamaProvider
from bot_plugins.ai_chat.providers.openai import OpenAICompatibleProvider, normalize_base_url

PARAMS = GenerationParams(
    temperature=0.7,
    top_p=0.9,
    top_k=40,
    max_tokens=64,
    num_ctx=2048,
    repeat_penalty=1.2,
)


# ---------------------------------------------------------------------------
# URL / 注册表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:1234", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
        ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ollama", "ollama"),
        ("openai", "openai"),
        ("openai-compatible", "openai"),
        ("openai_compatible", "openai"),
        ("lmstudio", "openai"),
        ("LM-Studio", "openai"),
        ("api", "openai"),
        (None, "ollama"),
        ("weird", "weird"),
    ],
)
def test_provider_type(raw: str | None, expected: str) -> None:
    spec = {} if raw is None else {"type": raw}
    assert provider_type(spec) == expected


def test_supported_types() -> None:
    assert set(SUPPORTED_TYPES) == {"ollama", "openai"}


def test_create_provider_unknown_type() -> None:
    with pytest.raises(LLMError, match="不受支持"):
        create_provider("x", {"type": "nope", "url": "http://x"}, params=PARAMS, timeout=1)


def test_create_provider_requires_url() -> None:
    with pytest.raises(LLMError, match="缺少 url"):
        create_provider("x", {"type": "openai"}, params=PARAMS, timeout=1)


async def test_create_provider_alias() -> None:
    provider = create_provider(
        "lm",
        {"type": "lmstudio", "url": "http://127.0.0.1:1234"},
        params=PARAMS,
        timeout=1,
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    await provider.aclose()


# ---------------------------------------------------------------------------
# OpenAI 兼容
# ---------------------------------------------------------------------------


def _openai(spec: dict | None = None) -> OpenAICompatibleProvider:
    base = {"url": "http://test.local", "model": ["m"]}
    base.update(spec or {})
    return OpenAICompatibleProvider("api", base, params=PARAMS, timeout=5)


async def _install(
    provider: OpenAICompatibleProvider,
    handler,
) -> None:
    headers = dict(provider._client.headers)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=f"{provider.base_url}/",
        headers=headers,
    )


async def test_openai_chat_payload() -> None:
    provider = _openai({"api_key": "k", "extra_body": {"repeat_penalty": 1.1}})
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "你好喵"}}]},
        )

    await _install(provider, handler)
    messages = [{"role": "user", "content": "hi"}]
    assert await provider.chat("m", messages) == "你好喵"

    assert seen["url"] == "http://test.local/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == "m"
    assert body["messages"] == messages
    assert body["stream"] is False
    assert body["temperature"] == 0.7
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 64
    assert body["repeat_penalty"] == 1.1


async def test_openai_chat_content_parts() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": [{"text": "你"}, {"text": "好"}]}}
                ]
            },
        )

    await _install(provider, handler)
    assert await provider.chat("m", []) == "你好"


async def test_openai_chat_empty_choices() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    await _install(provider, handler)
    with pytest.raises(LLMError, match="choices"):
        await provider.chat("m", [])


async def test_openai_chat_reasoning_only() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "", "reasoning_content": "思考中……"},
                        "finish_reason": "length",
                    }
                ]
            },
        )

    await _install(provider, handler)
    with pytest.raises(LLMError, match="reasoning_effort"):
        await provider.chat("m", [])


async def test_openai_chat_http_error() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom detail")

    await _install(provider, handler)
    with pytest.raises(LLMError, match="HTTP 500"):
        await provider.chat("m", [])


async def test_openai_list_models() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://test.local/v1/models"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    await _install(provider, handler)
    assert await provider.list_models() == ["a", "b"]


async def test_openai_list_models_empty() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    await _install(provider, handler)
    with pytest.raises(LLMError, match="没有返回任何模型"):
        await provider.list_models()


async def test_openai_list_models_skips_embedding() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen3.5-9b@q6_k"},
                    {"id": "text-embedding-nomic-embed-text-v1.5"},
                    {"id": "bge-rerank"},
                    {"id": "deepseek-r1"},
                ]
            },
        )

    await _install(provider, handler)
    assert await provider.list_models() == ["qwen3.5-9b@q6_k", "deepseek-r1"]


# ---------------------------------------------------------------------------
# ollama
# ---------------------------------------------------------------------------


def _ollama() -> OllamaProvider:
    return OllamaProvider(
        "local",
        {"url": "http://127.0.0.1:1", "model": ["m"]},
        params=PARAMS,
        timeout=5,
    )


def test_ollama_options_mapping() -> None:
    options = dict(_ollama().options)
    assert options["temperature"] == 0.7
    assert options["top_p"] == 0.9
    assert options["top_k"] == 40
    assert options["num_predict"] == 64
    assert options["num_ctx"] == 2048
    assert options["repeat_penalty"] == 1.2


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeOllamaClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    async def list(self):
        if self.error is not None:
            raise self.error
        return self.response


async def test_ollama_chat() -> None:
    provider = _ollama()
    fake = _FakeOllamaClient(_FakeResponse("在的喵"))
    provider._client = fake

    result = await provider.chat("m", [{"role": "user", "content": "hi"}])
    assert result == "在的喵"
    assert fake.calls[0]["model"] == "m"
    assert fake.calls[0]["options"]["temperature"] == 0.7


async def test_ollama_chat_empty() -> None:
    provider = _ollama()
    provider._client = _FakeOllamaClient(_FakeResponse("   "))
    with pytest.raises(LLMError, match="空内容"):
        await provider.chat("m", [])


async def test_ollama_chat_error() -> None:
    provider = _ollama()
    provider._client = _FakeOllamaClient(error=ConnectionError("refused"))
    with pytest.raises(LLMError, match="refused"):
        await provider.chat("m", [])


async def test_ollama_list_models() -> None:
    provider = _ollama()

    class Item:
        def __init__(self, model: str) -> None:
            self.model = model

    class Response:
        models = [Item("a:1"), Item("b:2")]

    provider._client = _FakeOllamaClient(Response())
    assert await provider.list_models() == ["a:1", "b:2"]


async def test_ollama_list_models_error() -> None:
    provider = _ollama()
    provider._client = _FakeOllamaClient(error=ConnectionError("refused"))
    with pytest.raises(LLMError, match="refused"):
        await provider.list_models()

# ---------------------------------------------------------------------------
# 多模态

# ---------------------------------------------------------------------------
# 多模态
# ---------------------------------------------------------------------------


def test_supports_vision_by_model_name() -> None:
    provider = _openai({"model": ["qwen2.5-vl-7b"]})
    assert provider.supports_vision("qwen2.5-vl-7b") is True
    assert provider.supports_vision("qwen3.5-9b@q6_k") is False


def test_supports_vision_override_wins() -> None:
    forced = _openai({"vision": True})
    assert forced.supports_vision("some-text-model") is True
    disabled = _openai({"vision": False})
    assert disabled.supports_vision("qwen2.5-vl-7b") is False


def test_supports_vision_respects_global_switch(monkeypatch) -> None:
    import bot_config as cfg

    provider = _openai()
    monkeypatch.setattr(cfg, "VISION_ENABLED", False)
    assert provider.supports_vision("qwen2.5-vl-7b") is False


async def test_openai_chat_sends_images_as_content_parts() -> None:
    provider = _openai()
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "看到了"}}]})

    await _install(provider, handler)
    messages = [
        {"role": "system", "content": "人设"},
        {
            "role": "user",
            "content": "这是什么",
            "images": [{"base64": "QUJD", "mime": "image/png"}],
        },
    ]
    assert await provider.chat("m", messages) == "看到了"

    sent = seen["body"]["messages"]
    assert sent[0] == {"role": "system", "content": "人设"}
    assert sent[1]["role"] == "user"
    assert sent[1]["content"][0] == {"type": "text", "text": "这是什么"}
    assert sent[1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,QUJD"},
    }


async def test_ollama_chat_sends_images_field() -> None:
    provider = _ollama()
    fake = _FakeOllamaClient(_FakeResponse("看到了"))
    provider._client = fake

    messages = [
        {
            "role": "user",
            "content": "看图",
            "images": [{"base64": "QUJD", "mime": "image/png"}],
        }
    ]
    assert await provider.chat("m", messages) == "看到了"
    sent = fake.calls[0]["messages"][0]
    assert sent["images"] == ["QUJD"]
    assert "base64" not in sent


def test_supports_vision_lists_specific_models() -> None:
    provider = _openai({"model": ["qwen3.5-9b@q6_k", "llama3.1"], "vision": ["qwen3.5-9b"]})
    # 列表里只写了片段，也应覆盖同名不同量化的版本
    assert provider.supports_vision("qwen3.5-9b@q6_k") is True
    assert provider.supports_vision("Qwen3.5-9B@Q8_0") is True
    assert provider.supports_vision("llama3.1:latest") is False
    assert provider.supports_vision("qwen2.5-vl-7b") is False


def test_supports_vision_wildcard_and_off_strings() -> None:
    assert _openai({"vision": "*"}).supports_vision("anything") is True
    assert _openai({"vision": "all"}).supports_vision("anything") is True
    assert _openai({"vision": "off"}).supports_vision("qwen2.5-vl-7b") is False
    assert _openai({"vision": "qwen3.5-9b,qwen2.5-vl"}).supports_vision("qwen3.5-9b@q6_k") is True


# ---------------------------------------------------------------------------
# 每源上下文 / 并发度 / 能力探测
# ---------------------------------------------------------------------------


def test_provider_context_falls_back_to_params() -> None:
    assert _openai().context == PARAMS.num_ctx
    assert _ollama().context == PARAMS.num_ctx
    assert _openai({"context": 32768}).context == 32768


def test_ollama_num_ctx_follows_source_context() -> None:
    provider = OllamaProvider(
        "local",
        {"url": "http://127.0.0.1:1", "model": ["m"], "context": 4096},
        params=PARAMS,
        timeout=5,
    )
    assert dict(provider.options)["num_ctx"] == 4096


async def test_openai_probe_vision_reads_lmstudio_metadata() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://test.local/api/v0/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "text-1", "type": "llm"},
                    {"id": "vl-1", "type": "vlm"},
                ]
            },
        )

    await _install(provider, handler)
    assert await provider.probe_vision("vl-1") is True
    assert await provider.probe_vision("text-1") is False


async def test_openai_probe_vision_missing_endpoint() -> None:
    provider = _openai()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    await _install(provider, handler)
    # 探测不到就返回 None，交给名字启发式
    assert await provider.probe_vision("anything") is None


async def test_ollama_probe_vision_uses_capabilities() -> None:
    provider = _ollama()

    class Fake:
        def __init__(self, capabilities=None) -> None:
            self.capabilities = capabilities

        async def show(self, model):
            return {"capabilities": self.capabilities} if self.capabilities else {}

    provider._client = Fake(["completion", "vision"])
    assert await provider.probe_vision("m") is True

    provider._client = Fake(["completion"])
    assert await provider.probe_vision("m") is False

    class Broken:
        async def show(self, model):
            raise ConnectionError("refused")

    provider._client = Broken()
    assert await provider.probe_vision("m") is None

