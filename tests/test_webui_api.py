"""WebUI 的 HTTP 接口（FastAPI TestClient，不启动真实服务器）。"""

from __future__ import annotations

import copy
import json
import types

import nonebot
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import bot_config as cfg

# 接口在调用时才会去 import 插件模块，这里先把 NoneBot 初始化好。
nonebot.init(
    driver="~fastapi",
    host="127.0.0.1",
    port=2,
    superusers={"1919810"},
    command_start={"/"},
    log_level="WARNING",
    _env_file=None,
)

from webui import api as webui_api  # noqa: E402
from webui import config_store  # noqa: E402

KEYS = [field["key"] for field in config_store.FIELDS]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    saved = {key: copy.deepcopy(getattr(cfg, key)) for key in KEYS}
    monkeypatch.setattr(cfg, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_WEBUI_OVERLAY", tmp_path / config_store.OVERLAY_NAME)
    webui_api._RESTART_PENDING.clear()
    try:
        yield tmp_path
    finally:
        for key, value in saved.items():
            setattr(cfg, key, value)
        webui_api._RESTART_PENDING.clear()


@pytest.fixture
def client(sandbox):
    app = FastAPI()
    app.include_router(webui_api.public_router, prefix="/api")
    app.include_router(webui_api.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def test_health_needs_no_auth(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["name"] == "tomato-webui"
    assert payload["version"] == webui_api.VERSION


def test_access_control_blocks_remote_without_token(monkeypatch):
    remote = types.SimpleNamespace(
        client=types.SimpleNamespace(host="8.8.8.8"),
        headers={},
        query_params={},
    )
    with pytest.raises(HTTPException) as error:
        webui_api.require_access(remote)
    assert error.value.status_code == 403

    monkeypatch.setenv("WEBUI_TOKEN", "s3cret")
    allowed = types.SimpleNamespace(
        client=types.SimpleNamespace(host="8.8.8.8"),
        headers={"x-webui-token": "s3cret"},
        query_params={},
    )
    assert webui_api.require_access(allowed) is None

    local = types.SimpleNamespace(
        client=types.SimpleNamespace(host="127.0.0.1"),
        headers={},
        query_params={},
    )
    assert webui_api.require_access(local) is None


def test_status_shape(client):
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert {segment["group_id"] for segment in payload["groups"]} == set(cfg.ALLOW_GROUPS)
    assert payload["stats"]["group_count"] == len(cfg.ALLOW_GROUPS)
    assert payload["model"]["sources"]
    assert "ALLOW_GROUPS" in payload["config"]["values"]
    assert len(payload["config"]["fields"]) == len(KEYS)
    assert set(payload["emotions"]["counts"]) == set(cfg.EMOTIONS)


def test_get_config(client):
    payload = client.get("/api/config").json()

    assert set(payload["values"]) == set(KEYS)
    assert payload["fields"] == config_store.FIELDS


def test_save_config_applies_and_persists(client, sandbox):
    response = client.post("/api/config", json={"values": {"TEMPERATURE": 0.42, "MAX_MEMORY": 9}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["values"]["TEMPERATURE"] == 0.42
    assert cfg.TEMPERATURE == 0.42
    overlay = json.loads((sandbox / config_store.OVERLAY_NAME).read_text(encoding="utf-8"))
    assert overlay["MAX_MEMORY"] == 9


def test_save_config_rejects_bad_value(client):
    response = client.post("/api/config", json={"values": {"TEMPERATURE": 5}})

    assert response.status_code == 400
    assert "温度" in response.json()["detail"]


def test_restart_required_is_empty_for_hot_fields(client):
    response = client.post("/api/config", json={"values": {"AI_NAME": "mikan"}})

    assert response.status_code == 200
    assert response.json()["restart_required"] == []
    assert client.get("/api/status").json()["restart_required"] == []


def test_group_crud(client):
    created = client.post("/api/groups", json={"group_id": "654321"})
    assert created.status_code == 200
    assert "654321" in cfg.ALLOW_GROUPS

    patched = client.patch(
        "/api/groups/654321",
        json={"enabled": False, "water_enabled": True, "water_probability": 0.5},
    )
    assert patched.status_code == 200
    payload = patched.json()
    assert payload["enabled"] is False
    assert payload["water_enabled"] is True
    assert payload["water_probability"] == 0.5

    removed = client.delete("/api/groups/654321")
    assert removed.status_code == 200
    assert "654321" not in cfg.ALLOW_GROUPS

    assert client.delete("/api/groups/654321").status_code == 404
    assert client.post("/api/groups", json={"group_id": "abc"}).status_code == 400


def test_memory_endpoints(client):
    from bot_plugins.ai_chat import pipeline

    group_id = "777777"
    pipeline.reset_group(group_id)
    pipeline.remember_user(group_id, "测试", "10001", "记住这句话喵")

    payload = client.get(f"/api/groups/{group_id}/memory").json()
    assert payload["records"]
    assert payload["records"][-1]["content"].endswith("喵")
    assert payload["records"][-1]["role"] == "user"

    cleared = client.delete(f"/api/groups/{group_id}/memory").json()
    assert cleared["removed"] >= 1
    assert client.get(f"/api/groups/{group_id}/memory").json()["records"] == []


def test_chat_roundtrip(client, monkeypatch):
    from bot_plugins.ai_chat import pipeline

    async def fake_chat(messages, **_kwargs):
        return "好耶喵！--*happy*--"

    monkeypatch.setattr(pipeline.llm, "chat", fake_chat)
    pipeline.reset_group(webui_api.WEBUI_TEST_GROUP)

    response = client.post("/api/chat", json={"text": "你好呀"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"].startswith("好耶")
    assert "happy" in payload["emotions"]
    assert payload["records"] >= 2

    reset = client.post("/api/chat/reset").json()
    assert reset["ok"] is True


def test_chat_reports_llm_errors(client, monkeypatch):
    from bot_plugins.ai_chat import pipeline
    from bot_plugins.ai_chat.errors import LLMError

    async def broken_chat(messages, **_kwargs):
        raise LLMError("模型挂了喵")

    monkeypatch.setattr(pipeline.llm, "chat", broken_chat)

    response = client.post("/api/chat", json={"text": "在吗"})

    assert response.status_code == 502
    assert "模型挂了喵" in response.json()["detail"]


def test_logs_endpoint(client, sandbox):
    log_dir = sandbox / "logs"
    log_dir.mkdir()
    content = "\n".join(f"第{index}行" for index in range(1, 13)) + "\n"
    (log_dir / "bot.log").write_text(content, encoding="utf-8")

    payload = client.get("/api/logs?lines=10").json()

    assert payload["lines"][-1] == "第12行"
    assert len(payload["lines"]) == 10
    assert payload["path"].endswith("bot.log")


def test_models_switch(client):
    response = client.post("/api/models/switch", json={"source": "local", "index": 0})

    assert response.status_code == 200
    assert response.json()["model"]["source"] == "local"


def test_model_sync_persists_list(client, monkeypatch, sandbox):
    from bot_plugins.ai_chat.model import llm

    target_source = llm.source

    async def fake_sync(source=None):
        llm.sources[target_source]["model"] = ["synced-model"]
        return ["synced-model"]

    monkeypatch.setattr(llm, "sync_models", fake_sync)

    response = client.post("/api/models/sync", json={})

    assert response.status_code == 200
    assert response.json()["models"] == ["synced-model"]
    overlay = json.loads((sandbox / config_store.OVERLAY_NAME).read_text(encoding="utf-8"))
    assert overlay["MODELS"][target_source]["model"] == ["synced-model"]


def test_emotion_preview_serves_image(client):
    response = client.get("/api/emotions/happy")

    assert response.status_code in {200, 404}
    if response.status_code == 200:
        assert response.headers["content-type"].startswith("image/")


def test_prompts_roundtrip_and_validation(client, sandbox, monkeypatch):
    from bot_plugins.ai_chat import prompts

    monkeypatch.setattr(prompts, "PROMPTS_FILE", sandbox / "webui_prompts.json")

    data = client.get("/api/prompts").json()
    assert data["fields"] and "persona" in data["values"] and "prefix" in data["values"]
    assert data["file"] == "webui_prompts.json"
    assert data["values"]["ai_name"] == "tomato"
    assert data["defaults"]["ai_name"] == "tomato"

    values = dict(data["values"])
    values["persona"] = values["persona"] + "\n额外设定喵。"
    values["ai_name"] = "mikan"
    saved = client.post("/api/prompts", json={"values": values})

    assert saved.status_code == 200
    assert "额外设定喵" in saved.json()["values"]["persona"]
    assert "额外设定喵" in prompts.SYSTEM_PROMPT
    assert "mikan" in prompts.SYSTEM_PROMPT
    assert (sandbox / "webui_prompts.json").exists()
    stored = json.loads((sandbox / "webui_prompts.json").read_text(encoding="utf-8"))
    assert stored["ai_name"] == "mikan"
    assert prompts.SYSTEM_PROMPT.count("mikan") >= 1

    bad = dict(values)
    bad["persona"] = ""
    assert client.post("/api/prompts", json={"values": bad}).status_code == 400

    bad_name = dict(values)
    bad_name["ai_name"] = "   "
    assert client.post("/api/prompts", json={"values": bad_name}).status_code == 400

    reset = client.post("/api/prompts/reset")
    assert reset.status_code == 200
    assert not (sandbox / "webui_prompts.json").exists()
    assert "额外设定喵" not in prompts.SYSTEM_PROMPT
    assert reset.json()["values"]["ai_name"] == "tomato"


def test_chat_accepts_dragged_images(client, monkeypatch):
    from bot_plugins.ai_chat import pipeline

    captured: dict = {}

    async def fake_chat(messages, **_kwargs):
        captured["messages"] = messages
        return "看到了喵--*happy*--"

    monkeypatch.setattr(pipeline.llm, "chat", fake_chat)
    monkeypatch.setattr(type(pipeline.llm), "supports_vision", property(lambda self: True))
    pipeline.reset_group(webui_api.WEBUI_TEST_GROUP)

    image = {"base64": "QUJD", "mime": "image/png"}
    response = client.post("/api/chat", json={"text": "", "images": [image]})

    assert response.status_code == 200
    assert captured["messages"][-1]["images"] == [image]
    records = client.get(f"/api/groups/{webui_api.WEBUI_TEST_GROUP}/memory").json()["records"]
    assert records[0]["content"].endswith("[图片]")
    client.post("/api/chat/reset")


def test_chat_rejects_images_without_vision_model(client, monkeypatch):
    from bot_plugins.ai_chat import pipeline

    monkeypatch.setattr(type(pipeline.llm), "supports_vision", property(lambda self: False))
    response = client.post("/api/chat", json={"images": [{"base64": "QUJD", "mime": "image/png"}]})

    assert response.status_code == 400
    assert "多模态" in response.json()["detail"]


def test_chat_rejects_bad_images(client):
    from bot_plugins.ai_chat import pipeline

    pipeline.reset_group(webui_api.WEBUI_TEST_GROUP)

    empty = client.post("/api/chat", json={"text": ""})
    assert empty.status_code == 400

    bad_mime = client.post(
        "/api/chat", json={"images": [{"base64": "QUJD", "mime": "application/pdf"}]}
    )
    assert bad_mime.status_code == 400

    blank = client.post("/api/chat", json={"images": [{"base64": "", "mime": "image/png"}]})
    assert blank.status_code == 400

def test_webui_session_uses_config() -> None:
    assert webui_api.WEBUI_TEST_GROUP == cfg.WEBUI_SESSION_GROUP
    assert webui_api.WEBUI_SESSION_BOT_ID == cfg.WEBUI_SESSION_BOT_ID

