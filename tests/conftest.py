"""让测试可以直接 import bot_plugins / bot_config。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试不要往仓库里写记忆文件：插件在 import 时就会按这个开关建存储，
# 所以必须在任何 bot_plugins 导入之前关掉（用户的 webui_config.json 可能开着）。
import bot_config as _cfg  # noqa: E402

_cfg.MEMORY_PERSIST_ENABLED = False
_cfg.MEMORY_STORE_PATH = ROOT / "data" / "__tests_should_not_write__"


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


@pytest.fixture(autouse=True, scope="session")
def _isolate_prompts():
    """测试也不依赖本机的 webui_prompts.json（用户可能改了人设、前后缀）。

    做法是让 prompts 去读一个不存在的文件，从而回落到代码默认值；
    不会删除或改写用户实际保存的提示词文件。
    """
    import bot_config as cfg
    from bot_plugins.ai_chat import prompts

    saved_file = prompts.PROMPTS_FILE
    saved_name = cfg.AI_NAME
    prompts.PROMPTS_FILE = saved_file.with_name("__no_prompts_for_tests__.json")
    prompts.reload()
    yield
    prompts.PROMPTS_FILE = saved_file
    cfg.AI_NAME = saved_name
    prompts.reload()

