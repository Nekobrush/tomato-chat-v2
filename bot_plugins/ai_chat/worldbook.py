"""世界书 / 角色书：读取酒馆（SillyTavern）格式，按关键词在每轮尾部注入。

设计取舍
--------

* **只注入尾部**：LM Studio 的 Qwen 模板不允许中途出现 system 消息
  （`System message must be at the beginning`），所以命中的条目一律拼进
  最后一条 user 消息后面，而不是真的插在「角色定义之前/之后」。
  酒馆条目的 `position` / `depth` / `role` 会原样保存下来，导入导出不丢，
  但运行时只当作「尾部注入」处理——界面上会提示这一点。
* **不做计时器**：`sticky` / `cooldown` / `delay` 需要跨轮状态，第一版先忽略。
* **无损导出**：每条 entry 的未知字段收进 `extra`，导出时铺回去；
  整个文件的未知顶层字段收进 `Book.extra`。

关键词匹配遵循酒馆的 `selectiveLogic`：

===== ========== ====================================================
逻辑  名称       命中条件
===== ========== ====================================================
0     AND ANY   主关键词任一命中，且副关键词任一命中（无副关键词则只看主）
1     NOT ALL   主关键词任一命中，且副关键词**并非全部**命中
2     NOT ANY   主关键词任一命中，且副关键词**一个都不**命中
3     AND ALL   主关键词任一命中，且副关键词**全部**命中
===== ========== ====================================================
"""

from __future__ import annotations

import json
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

LOGIC_AND_ANY = 0
LOGIC_NOT_ALL = 1
LOGIC_NOT_ANY = 2
LOGIC_AND_ALL = 3

POSITION_NAMES = {
    0: "角色定义之前",
    1: "角色定义之后",
    2: "作者注释之前",
    3: "作者注释之后",
    4: "按深度插入",
    5: "示例之前",
    6: "示例之后",
    7: "Outlets",
}

ROLE_NAMES = {0: "System", 1: "User", 2: "Assistant"}

# 酒馆 newWorldInfoEntryDefinition 的字段顺序，导出时按它排
ENTRY_KEYS = [
    "uid",
    "key",
    "keysecondary",
    "comment",
    "content",
    "constant",
    "vectorized",
    "selective",
    "selectiveLogic",
    "addMemo",
    "order",
    "position",
    "disable",
    "excludeRecursion",
    "preventRecursion",
    "delayUntilRecursion",
    "probability",
    "useProbability",
    "depth",
    "group",
    "groupOverride",
    "groupWeight",
    "scanDepth",
    "caseSensitive",
    "matchWholeWords",
    "useGroupScoring",
    "automationId",
    "role",
    "sticky",
    "cooldown",
    "delay",
    "triggers",
    "displayIndex",
]

_KNOWN_KEYS = set(ENTRY_KEYS)


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_keys(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Entry:
    """一条世界书条目（字段名对齐酒馆，语义见模块文档）。"""

    uid: int = 0
    keys: list[str] = field(default_factory=list)
    keys_secondary: list[str] = field(default_factory=list)
    title: str = ""
    content: str = ""
    constant: bool = False
    enabled: bool = True
    selective: bool = True
    logic: int = LOGIC_AND_ANY
    order: int = 100
    position: int = 0
    depth: int = 4
    role: int = 0
    probability: int = 100
    use_probability: bool = True
    scan_depth: int | None = None
    case_sensitive: bool = False
    match_whole_words: bool = False
    display_index: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- 匹配
    def _hits(self, key: str, texts: list[str]) -> bool:
        needle = key if self.case_sensitive else key.lower()
        for text in texts:
            haystack = text if self.case_sensitive else text.lower()
            if self.match_whole_words:
                if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
                    return True
            elif needle in haystack:
                return True
        return False

    def matches(self, texts: list[str]) -> bool:
        if not self.enabled:
            return False
        if self.constant:
            return True
        if not self.keys:
            return False
        if not any(self._hits(key, texts) for key in self.keys):
            return False
        if not self.selective or not self.keys_secondary:
            return True
        secondary = [self._hits(key, texts) for key in self.keys_secondary]
        if self.logic == LOGIC_NOT_ALL:
            return not all(secondary)
        if self.logic == LOGIC_NOT_ANY:
            return not any(secondary)
        if self.logic == LOGIC_AND_ALL:
            return all(secondary)
        return any(secondary)  # LOGIC_AND_ANY


@dataclass
class Book:
    id: str
    name: str
    kind: str = "world"  # world | character
    entries: list[Entry] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def sorted_entries(self) -> list[Entry]:
        return sorted(self.entries, key=lambda item: (item.order, item.display_index))


def entry_from_st(raw: dict[str, Any], index: int = 0) -> Entry:
    extra = {key: value for key, value in raw.items() if key not in _KNOWN_KEYS}
    return Entry(
        uid=_as_int(raw.get("uid"), index),
        keys=_as_keys(raw.get("key")),
        keys_secondary=_as_keys(raw.get("keysecondary")),
        title=_as_text(raw.get("comment")),
        content=_as_text(raw.get("content")),
        constant=_as_bool(raw.get("constant")),
        # 酒馆里是 disable，我们内部用 enabled，语义相反
        enabled=not _as_bool(raw.get("disable")),
        selective=_as_bool(raw.get("selective"), True),
        logic=_as_int(raw.get("selectiveLogic"), LOGIC_AND_ANY),
        order=_as_int(raw.get("order"), 100),
        position=_as_int(raw.get("position"), 0),
        depth=_as_int(raw.get("depth"), 4),
        role=_as_int(raw.get("role"), 0),
        probability=_as_int(raw.get("probability"), 100),
        use_probability=_as_bool(raw.get("useProbability"), True),
        scan_depth=(
            _as_int(raw.get("scanDepth"), 0) or None if raw.get("scanDepth") is not None else None
        ),
        case_sensitive=_as_bool(raw.get("caseSensitive")),
        match_whole_words=_as_bool(raw.get("matchWholeWords")),
        display_index=_as_int(raw.get("displayIndex"), index),
        extra=extra,
    )


def entry_to_st(entry: Entry) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "uid": entry.uid,
        "key": list(entry.keys),
        "keysecondary": list(entry.keys_secondary),
        "comment": entry.title,
        "content": entry.content,
        "constant": entry.constant,
        "vectorized": False,
        "selective": entry.selective,
        "selectiveLogic": entry.logic,
        "addMemo": True,
        "order": entry.order,
        "position": entry.position,
        "disable": not entry.enabled,
        "excludeRecursion": False,
        "preventRecursion": False,
        "delayUntilRecursion": False,
        "probability": entry.probability,
        "useProbability": entry.use_probability,
        "depth": entry.depth,
        "group": "",
        "groupOverride": False,
        "groupWeight": 100,
        "scanDepth": entry.scan_depth,
        "caseSensitive": entry.case_sensitive,
        "matchWholeWords": entry.match_whole_words,
        "useGroupScoring": None,
        "automationId": "",
        "role": entry.role,
        "sticky": 0,
        "cooldown": 0,
        "delay": 0,
        "triggers": [],
        "displayIndex": entry.display_index,
    }
    raw.update(entry.extra)
    return {key: raw[key] for key in ENTRY_KEYS if key in raw} | {
        key: value for key, value in raw.items() if key not in ENTRY_KEYS
    }


def book_from_st(raw: Any, fallback_name: str = "未命名世界书", kind: str = "world") -> Book:
    """接受酒馆 v2（dict 带 entries）与角色书（entries 是数组）两种形态。"""
    if isinstance(raw, list):
        payload: dict[str, Any] = {"entries": raw}
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ValueError("世界书内容必须是一个对象或数组。")

    entries_raw = payload.get("entries") or []
    if isinstance(entries_raw, dict):
        pairs = sorted(entries_raw.items(), key=lambda item: _as_int(item[0], 0))
        entries = [entry_from_st(value, index) for index, (_, value) in enumerate(pairs)]
    elif isinstance(entries_raw, list):
        entries = [entry_from_st(value, index) for index, value in enumerate(entries_raw)]
    else:
        raise ValueError("entries 必须是对象或数组。")

    known = {"entries", "name", "bookName"}
    book = Book(
        id=uuid.uuid4().hex[:12],
        name=_as_text(payload.get("name")) or _as_text(payload.get("bookName")) or fallback_name,
        kind=kind if kind in {"world", "character"} else "world",
        entries=entries,
        extra={key: value for key, value in payload.items() if key not in known},
    )
    reindex(book)
    return book


def book_to_st(book: Book) -> dict[str, Any]:
    """导出成酒馆 v2 世界书（entries 为对象、键是 uid 字符串）。"""
    entries: dict[str, Any] = {}
    for index, entry in enumerate(book.sorted_entries()):
        entry.uid = index
        entry.display_index = index
        entries[str(index)] = entry_to_st(entry)
    payload: dict[str, Any] = {"entries": entries}
    payload.update(book.extra)
    if book.name:
        payload.setdefault("name", book.name)
    return payload


def reindex(book: Book) -> None:
    for index, entry in enumerate(book.sorted_entries()):
        entry.uid = index
        entry.display_index = index


def add_entry(book: Book, entry: Entry | None = None) -> Entry:
    entry = entry or Entry(keys=[""], content="")
    book.entries.append(entry)
    reindex(book)
    return entry


def remove_entry(book: Book, uid: int) -> bool:
    for index, entry in enumerate(book.entries):
        if entry.uid == uid:
            book.entries.pop(index)
            reindex(book)
            return True
    return False


def match_entries(
    books: list[Book],
    scan_texts: list[str],
    *,
    max_chars: int = 1200,
    rng: random.Random | None = None,
) -> list[str]:
    """按优先级挑出本轮要注入的条目内容（纯文本片段）。"""
    rng = rng or random.Random()
    chosen: list[str] = []
    budget = max(0, max_chars)
    for book in books:
        for entry in book.sorted_entries():
            if budget <= 0:
                break
            if entry.use_probability and entry.probability < 100:
                if rng.randint(1, 100) > entry.probability:
                    continue
            if not entry.matches(scan_texts):
                continue
            text = entry.content.strip()
            if not text:
                continue
            if len(text) > budget:
                text = text[:budget]
            chosen.append(text)
            budget -= len(text)
    return chosen


def load_books(path) -> list[Book]:
    """从我们自己的存储文件读取（`{"books": [...]}`，条目用酒馆字段）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    books: list[Book] = []
    for item in raw.get("books", []) if isinstance(raw, dict) else []:
        try:
            book = book_from_st(item, fallback_name="未命名世界书", kind=item.get("kind", "world"))
        except ValueError:
            continue
        book.id = _as_text(item.get("id")) or book.id
        book.name = _as_text(item.get("name")) or book.name
        book.kind = item.get("kind") if item.get("kind") in {"world", "character"} else book.kind
        books.append(book)
    return books


def save_books(path, books: list[Book]) -> None:
    payload = {
        "version": 1,
        "books": [book_to_st(book) | {"id": book.id, "kind": book.kind} for book in books],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
