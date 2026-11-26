"""世界书 / 角色书：酒馆格式导入导出与关键词匹配。"""

from __future__ import annotations

import json
import random

import pytest

from bot_plugins.ai_chat import worldbook


def _st_entry(**overrides) -> dict:
    base = {
        "uid": 0,
        "key": ["龙"],
        "keysecondary": [],
        "comment": "龙族设定",
        "content": "这个世界的龙会说人话。",
        "constant": False,
        "selective": True,
        "selectiveLogic": worldbook.LOGIC_AND_ANY,
        "order": 100,
        "position": 0,
        "disable": False,
        "probability": 100,
        "useProbability": True,
        "depth": 4,
        "role": 0,
        "displayIndex": 0,
    }
    base.update(overrides)
    return base


def test_import_v2_object_form() -> None:
    raw = {"name": "示例世界书", "entries": {"0": _st_entry(), "1": _st_entry(uid=1, comment="第二条")}}
    book = worldbook.book_from_st(raw)

    assert book.name == "示例世界书"
    assert book.kind == "world"
    assert [entry.title for entry in book.entries] == ["龙族设定", "第二条"]
    assert book.entries[0].content == "这个世界的龙会说人话。"
    assert book.entries[0].enabled is True


def test_import_character_book_array_and_disable_flag() -> None:
    raw = [_st_entry(disable=True), _st_entry(uid=1, constant=True)]
    book = worldbook.book_from_st(raw, fallback_name="角色卡", kind="character")

    assert book.kind == "character"
    assert book.entries[0].enabled is False
    assert book.entries[1].constant is True


def test_export_keeps_unknown_fields() -> None:
    entry = _st_entry(customField={"a": 1}, anotherThing="x")
    book = worldbook.book_from_st({"name": "b", "entries": [entry], "extraTop": True})
    exported = worldbook.book_to_st(book)

    stored = exported["entries"]["0"]
    assert stored["customField"] == {"a": 1}
    assert stored["anotherThing"] == "x"
    assert exported["extraTop"] is True
    assert stored["key"] == ["龙"]


def test_match_constant_and_keyword() -> None:
    constant = worldbook.Entry(uid=0, constant=True, content="常驻设定")
    keyword = worldbook.Entry(uid=1, keys=["龙"], content="龙族设定")
    miss = worldbook.Entry(uid=2, keys=["独角兽"], content="不会命中")

    hits = worldbook.match_entries(
        [worldbook.Book(id="b", name="b", entries=[constant, keyword, miss])],
        ["今天看到一条龙"],
    )
    assert hits == ["常驻设定", "龙族设定"]


def test_match_case_and_whole_word() -> None:
    entry = worldbook.Entry(uid=0, keys=["cat"], content="猫", case_sensitive=True)
    book = worldbook.Book(id="b", name="b", entries=[entry])
    assert worldbook.match_entries([book], ["a CAT here"]) == []
    assert worldbook.match_entries([book], ["a cat here"]) == ["猫"]

    whole = worldbook.Entry(uid=0, keys=["cat"], content="猫", match_whole_words=True)
    book = worldbook.Book(id="b", name="b", entries=[whole])
    assert worldbook.match_entries([book], ["concatenate"]) == []
    assert worldbook.match_entries([book], ["a cat"]) == ["猫"]


@pytest.mark.parametrize(
    ("logic", "secondary", "text", "expected"),
    [
        (worldbook.LOGIC_AND_ANY, ["夜"], "龙在夜里出现", True),
        (worldbook.LOGIC_AND_ANY, ["夜"], "龙在白天出现", False),
        (worldbook.LOGIC_NOT_ALL, ["夜", "白天"], "龙在夜里出现", True),
        (worldbook.LOGIC_NOT_ALL, ["夜", "白天"], "龙在夜里白天都出现", False),
        (worldbook.LOGIC_NOT_ANY, ["夜"], "龙在白天出现", True),
        (worldbook.LOGIC_NOT_ANY, ["夜"], "龙在夜里出现", False),
        (worldbook.LOGIC_AND_ALL, ["夜", "海"], "龙在夜里的海上出现", True),
        (worldbook.LOGIC_AND_ALL, ["夜", "海"], "龙在夜里出现", False),
    ],
)
def test_selective_logic(logic: int, secondary: list[str], text: str, expected: bool) -> None:
    entry = worldbook.Entry(uid=0, keys=["龙"], keys_secondary=secondary, logic=logic, content="击中")
    book = worldbook.Book(id="b", name="b", entries=[entry])
    assert bool(worldbook.match_entries([book], [text])) is expected


def test_probability_zero_never_hits() -> None:
    entry = worldbook.Entry(uid=0, keys=["龙"], content="低概率", probability=0)
    book = worldbook.Book(id="b", name="b", entries=[entry])
    assert worldbook.match_entries([book], ["龙"], rng=random.Random(1)) == []


def test_match_budget_truncates() -> None:
    entry = worldbook.Entry(uid=0, keys=["龙"], content="x" * 50)
    book = worldbook.Book(id="b", name="b", entries=[entry])
    hits = worldbook.match_entries([book], ["龙"], max_chars=10)
    assert len(hits[0]) == 10


def test_save_and_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "books.json"
    book = worldbook.book_from_st({"name": "剧本", "entries": [_st_entry()]})
    worldbook.save_books(path, [book])

    loaded = worldbook.load_books(path)
    assert len(loaded) == 1
    assert loaded[0].name == "剧本"
    assert loaded[0].entries[0].content == "这个世界的龙会说人话。"
    assert loaded[0].id == book.id
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_remove_entry_reindexes() -> None:
    book = worldbook.book_from_st(
        {"entries": [_st_entry(), _st_entry(uid=1), _st_entry(uid=2)]}, fallback_name="b"
    )
    assert worldbook.remove_entry(book, 1) is True
    assert [entry.uid for entry in book.entries] == [0, 1]
    assert worldbook.remove_entry(book, 99) is False
