"""让测试可以直接 import bot_plugins / bot_config。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch):
    """测试不依赖本机的 webui_config.json：把易变的运行期配置钉回默认值。"""
    import bot_config as cfg

    monkeypatch.setattr(cfg, "SPLIT_PATTERN", "")
    monkeypatch.setattr(cfg, "SPLIT_MAX_CHARS", 0)
    monkeypatch.setattr(cfg, "SPLIT_DELAY_BASE", 0.6)
    monkeypatch.setattr(cfg, "SPLIT_DELAY_PER_CHAR", 0.02)
    monkeypatch.setattr(cfg, "SPLIT_DELAY_JITTER", 0.3)
    yield
