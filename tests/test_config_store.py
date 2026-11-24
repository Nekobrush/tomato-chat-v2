"""``webui.config_store``：字段校验、保存与覆盖文件回读。"""

from __future__ import annotations

import copy
import json

import pytest

import bot_config as cfg
from webui import config_store

KEYS = [field["key"] for field in config_store.FIELDS]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把覆盖文件写到临时目录，并在测试结束后恢复全局配置。"""
    saved = {key: copy.deepcopy(getattr(cfg, key)) for key in KEYS}
    monkeypatch.setattr(cfg, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_WEBUI_OVERLAY", tmp_path / config_store.OVERLAY_NAME)
    try:
        yield tmp_path
    finally:
        for key, value in saved.items():
            setattr(cfg, key, value)


def test_effective_exposes_every_field(sandbox):
    values = config_store.effective()

    assert set(values) == set(KEYS)
    assert values["ALLOW_GROUPS"] == sorted(values["ALLOW_GROUPS"])
    assert isinstance(values["MODELS"], dict)
    # 必须能直接 JSON 序列化（接口会原样返回给前端）
    json.dumps(values)


def test_save_writes_overlay_and_applies_values(sandbox):
    merged, restart = config_store.save(
        {"TEMPERATURE": 0.5, "ALLOW_GROUPS": ["893", "123456"], "MAX_MEMORY": 8}
    )

    assert cfg.TEMPERATURE == 0.5
    assert cfg.ALLOW_GROUPS == {"893", "123456"}
    assert cfg.MAX_MEMORY == 8
    assert restart == []

    overlay = json.loads((sandbox / config_store.OVERLAY_NAME).read_text(encoding="utf-8"))
    assert overlay["TEMPERATURE"] == 0.5
    assert overlay["ALLOW_GROUPS"] == ["123456", "893"]
    assert merged["MAX_MEMORY"] == 8


def test_split_settings_roundtrip(sandbox):
    merged, _ = config_store.save(
        {
            "SPLIT_PATTERN": r"[。！？]",
            "SPLIT_MAX_CHARS": 30,
            "SPLIT_DELAY_BASE": 0.5,
            "SPLIT_DELAY_PER_CHAR": 0.03,
            "SPLIT_DELAY_JITTER": 0.2,
        }
    )

    assert cfg.SPLIT_PATTERN == r"[。！？]"
    assert cfg.SPLIT_MAX_CHARS == 30
    assert cfg.SPLIT_DELAY_BASE == 0.5
    assert cfg.SPLIT_DELAY_PER_CHAR == 0.03
    assert cfg.SPLIT_DELAY_JITTER == 0.2
    assert merged["SPLIT_MAX_CHARS"] == 30


def test_split_pattern_can_be_empty_and_must_be_valid_regex(sandbox):
    config_store.save({"SPLIT_PATTERN": r"[。！？]"})
    merged, _ = config_store.save({"SPLIT_PATTERN": ""})
    assert merged["SPLIT_PATTERN"] == ""
    assert cfg.SPLIT_PATTERN == ""

    with pytest.raises(config_store.ConfigError):
        config_store.save({"SPLIT_PATTERN": "(["})


def test_split_max_chars_rejects_negative(sandbox):
    with pytest.raises(config_store.ConfigError):
        config_store.save({"SPLIT_MAX_CHARS": -1})


def test_ai_name_is_not_a_bot_config_field(sandbox):
    # AI 名字属于人格设定，存在 webui_prompts.json，不再走机器人配置
    assert "AI_NAME" not in [field["key"] for field in config_store.FIELDS]
    _, restart = config_store.save({"AI_NAME": "mikan"})

    assert restart == []
    assert cfg.AI_NAME != "mikan"


@pytest.mark.parametrize(
    "payload",
    [
        {"TEMPERATURE": 5},
        {"TOP_P": -0.1},
        {"MAX_MEMORY": 1.5},
        {"ALLOW_GROUPS": ["abc"]},
        {"DEFAULT_SOURCE": "not-exist"},
    ],
)
def test_save_rejects_bad_values(sandbox, payload):
    with pytest.raises(config_store.ConfigError):
        config_store.save(payload)


def test_models_need_url_and_known_type(sandbox):
    with pytest.raises(config_store.ConfigError):
        config_store.save({"MODELS": {"remote": {"type": "openai"}}})

    with pytest.raises(config_store.ConfigError):
        config_store.save({"MODELS": {"remote": {"type": "unknown", "url": "http://x"}}})

    with pytest.raises(config_store.ConfigError):
        config_store.save({"MODELS": {"bad name": {"url": "http://x"}}})


def test_models_keep_alias_type_and_index_is_clamped(sandbox):
    merged, _ = config_store.save(
        {
            "MODELS": {"remote": {"type": "lmstudio", "url": "http://127.0.0.1:1234", "model": ["a", "b"]}},
            "DEFAULT_SOURCE": "remote",
            "DEFAULT_MODEL_INDEX": 99,
        }
    )

    assert merged["MODELS"]["remote"]["type"] == "lmstudio"
    assert merged["DEFAULT_MODEL_INDEX"] == 1


def test_groups_are_deduplicated(sandbox):
    merged, _ = config_store.save({"ALLOW_GROUPS": ["111", "111", "222"]})
    assert merged["ALLOW_GROUPS"] == ["111", "222"]


def test_overlay_file_overrides_defaults(sandbox):
    overlay = sandbox / config_store.OVERLAY_NAME
    overlay.write_text(
        json.dumps(
            {
                "ALLOW_GROUPS": ["777"],
                "MAX_MEMORY": 5,
                "MODELS": {"remote": {"type": "openai", "url": "http://host:1"}},
                "DEFAULT_SOURCE": "remote",
                "DEFAULT_MODEL_INDEX": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cfg._apply_webui_overlay()

    assert cfg.ALLOW_GROUPS == {"777"}
    assert cfg.MAX_MEMORY == 5
    assert cfg.DEFAULT_SOURCE == "remote"
    assert cfg.MODELS["remote"]["url"] == "http://host:1"


def test_broken_overlay_is_ignored(sandbox):
    (sandbox / config_store.OVERLAY_NAME).write_text("{not json", encoding="utf-8")
    before = cfg.MAX_MEMORY

    cfg._apply_webui_overlay()

    assert cfg.MAX_MEMORY == before


def test_models_keep_vision_override(sandbox):
    merged, _ = config_store.save(
        {
            "MODELS": {
                "remote": {
                    "type": "openai",
                    "url": "http://127.0.0.1:1234",
                    "model": ["qwen3.5-9b@q6_k"],
                    "vision": ["qwen3.5-9b"],
                },
                "all-vision": {"type": "openai", "url": "http://127.0.0.2", "model": ["x"], "vision": "*"},
                "no-vision": {"type": "openai", "url": "http://127.0.0.3", "model": ["y"], "vision": "off"},
            },
            "DEFAULT_SOURCE": "remote",
        }
    )

    assert merged["MODELS"]["remote"]["vision"] == ["qwen3.5-9b"]
    assert merged["MODELS"]["all-vision"]["vision"] is True
    assert merged["MODELS"]["no-vision"]["vision"] is False


def test_memory_token_budget_roundtrip(sandbox) -> None:
    merged, restart = config_store.save({"MEMORY_TOKEN_BUDGET": 2048})
    assert merged["MEMORY_TOKEN_BUDGET"] == 2048
    assert cfg.MEMORY_TOKEN_BUDGET == 2048
    assert restart == []


def test_memory_token_budget_must_not_be_negative(sandbox) -> None:
    with pytest.raises(config_store.ConfigError):
        config_store.save({"MEMORY_TOKEN_BUDGET": -1})
