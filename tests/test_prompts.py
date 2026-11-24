"""提示词仓库：默认值、保存、校验与恢复默认。"""

from __future__ import annotations

import pytest

import bot_config as cfg
from bot_plugins.ai_chat import prompts


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """每个用例用独立的 webui_prompts.json，结束后恢复内置默认值。"""
    monkeypatch.setattr(prompts, "PROMPTS_FILE", tmp_path / "webui_prompts.json")
    prompts.reset()
    yield
    prompts.reset()


def _payload() -> dict:
    return prompts.defaults()


def test_defaults_render_placeholders() -> None:
    data = prompts.current()
    assert "{ai_name}" in data["system"]
    assert "{emotions}" in data["system"]
    assert data["ai_name"] == cfg.AI_NAME
    assert "ai_name" not in prompts.defaults()

    assert cfg.AI_NAME in prompts.SYSTEM_PROMPT
    assert cfg.EMOTIONS[0] in prompts.SYSTEM_PROMPT
    assert "{ai_name}" not in prompts.SYSTEM_PROMPT
    assert str(cfg.RESP_LEN_LIMIT) in prompts.TIPS_TOO_LONG
    assert prompts.SYSTEM_MESSAGES[0]["role"] == "system"
    assert prompts.SYSTEM_MESSAGES[0]["content"] == prompts.SYSTEM_PROMPT


def test_save_updates_runtime_immediately() -> None:
    payload = _payload()
    payload["system"] = payload["system"] + "\n另外：你说话要更简短一点喵。"
    saved = prompts.save(payload)

    assert prompts.PROMPTS_FILE.exists()
    assert prompts.current()["system"].endswith("更简短一点喵。")
    assert saved["system"] == payload["system"]
    assert "更简短一点喵。" in prompts.SYSTEM_PROMPT


def test_save_a_custom_tip_and_reqfail() -> None:
    payload = _payload()
    payload["tip_too_long"] = "太长了喵，缩到 {limit} 字以内喵。"
    payload["reqfail_tips"] = ["断网了喵", "再试一次喵"]
    prompts.save(payload)

    assert prompts.TIPS_TOO_LONG == f"太长了喵，缩到 {cfg.RESP_LEN_LIMIT} 字以内喵。"
    assert prompts.REQFAIL_TIPS == ["断网了喵", "再试一次喵"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.update(system="   "), "人设"),
        (lambda data: data.update(system="没有占位符的人设"), "占位符"),
        (lambda data: data.update(reqfail_tips=[]), "失败提示"),
        (lambda data: data.update(reqfail_tips=["a", "b", "c", "d", "e", "f"]), "最多 5 条"),
    ],
)
def test_invalid_payload_raises(mutate, message: str) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(prompts.PromptError) as excinfo:
        prompts.save(payload)
    assert message in str(excinfo.value)


def test_reset_removes_file_and_restores_defaults() -> None:
    payload = _payload()
    payload["system"] = payload["system"] + "\n临时改动喵。"
    prompts.save(payload)
    assert "临时改动喵。" in prompts.SYSTEM_PROMPT

    restored = prompts.reset()
    assert not prompts.PROMPTS_FILE.exists()
    assert set(restored) == set(prompts.defaults()) | {"ai_name"}
    assert all(restored[key] == value for key, value in prompts.defaults().items())
    assert "临时改动喵。" not in prompts.SYSTEM_PROMPT


def test_render_ignores_stray_braces() -> None:
    text = prompts.render("{ai_name} 说 {unknown} {} 喵", ai_name="tomato")
    assert text == "tomato 说 {unknown} {} 喵"
