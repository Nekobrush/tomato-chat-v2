"""记忆存储测试：裁剪、持久化、未回复消息合并。"""

import bot_config as cfg
from bot_plugins.ai_chat.memory import DEFAULT_MARK, PERSISTENCE_MARK, MemoryStore


def test_trim_keeps_newest() -> None:
    store = MemoryStore(3)
    for content in "abcd":
        store.append("g", "user", content)
    assert [r.content for r in store.records("g")] == ["b", "c", "d"]


def test_persistence_survives_trim() -> None:
    store = MemoryStore(2)
    store.append("g", "system", "p1", mark=PERSISTENCE_MARK)
    store.append("g", "system", "p2", mark=PERSISTENCE_MARK)
    store.append("g", "user", "u1")
    contents = [r.content for r in store.records("g")]
    assert "u1" in contents
    assert len(contents) == 2
    assert "p2" in contents


def test_set_max_record_trims() -> None:
    store = MemoryStore(5)
    for index in range(5):
        store.append("g", "user", str(index))
    store.set_max_record(2)
    assert [r.content for r in store.records("g")] == ["3", "4"]


def test_merge_user_tail() -> None:
    store = MemoryStore(10)
    store.append("g", "user", "[1]a(1):\n第一句")
    store.append("g", "user", "[2]a(1):\n第二句")
    assert store.merge_user_tail("g") is True
    records = store.records("g")
    assert len(records) == 1
    assert records[0].content == "[1]a(1):\n第一句\n[2]a(1):\n第二句"


def test_merge_user_tail_skips_after_assistant() -> None:
    store = MemoryStore(10)
    store.append("g", "user", "u1")
    store.append("g", "assistant", "a1")
    store.append("g", "user", "u2")
    assert store.merge_user_tail("g") is False
    assert [r.role for r in store.records("g")] == ["user", "assistant", "user"]


def test_merge_truncates_but_keeps_latest() -> None:
    store = MemoryStore(10)
    head = "[1]a(1):\n"
    store.append("g", "user", head + "旧" * cfg.MERGE_MAX_LEN)
    store.append("g", "user", "最新的一条")
    assert store.merge_user_tail("g") is True
    content = store.records("g")[0].content
    assert content.startswith(head)
    assert content.endswith("最新的一条")
    assert "…" in content


def test_clear() -> None:
    store = MemoryStore(5)
    store.append("g", "user", "x")
    assert store.clear("g") == 1
    assert store.count("g") == 0
    assert store.clear("g") == 0


def test_messages_shape() -> None:
    store = MemoryStore(5)
    store.append("g", "system", "设定", mark=DEFAULT_MARK)
    store.append("g", "user", "你好")
    assert store.messages("g") == [
        {"role": "system", "content": "设定"},
        {"role": "user", "content": "你好"},
    ]
