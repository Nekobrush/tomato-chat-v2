"""群记忆存储。

旧版在遍历 range 的同时 pop，会导致索引越界和裁错记录；这里统一重写为
"先插入、再按需裁剪"，并且裁剪时优先保留 persistence（持久化）记录。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import bot_config as cfg

logger = logging.getLogger(__name__)

DEFAULT_MARK = "default"
# 中文约 1.7 字/token、英文约 4 字符/token；估算只用于裁剪，不要求精确
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")
PERSISTENCE_MARK = "persistence"
# 生成后追加的纠错提示：只对下一轮有意义，不需要每轮都堆在提示词里
TIP_MARK = "tip"


@dataclass
class Record:
    role: str
    content: str
    mark: str = DEFAULT_MARK

    def to_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def estimate_tokens(text: str) -> int:
    """粗略估算一段文本占多少 token。

    只用于"记忆该留多少条"这类预算判断，不追求与真实 tokenizer 一致：
    中日韩字符按 0.6 token/字，其它字符按 0.25 token/字（约 4 字符/token）。
    """
    cjk = len(_CJK_PATTERN.findall(text))
    others = max(0, len(text) - cjk)
    return int(cjk * 0.6 + others * 0.25) + 1


_HEADER_PATTERN = re.compile(r"^\[\d{10,16}\][^\n]*:\n")


def _split_header(content: str) -> tuple[str, str]:
    """拆出 `[时间戳]昵称(QQ号):\n` 头与正文；没有头时 head 为空。"""
    match = _HEADER_PATTERN.match(content)
    if not match:
        return "", content
    return match.group(0), content[match.end():]


def _truncate(content: str) -> str:
    """把内容截断到 MERGE_MAX_LEN：保留首行头部，正文保留最新的一段。"""
    limit = cfg.MERGE_MAX_LEN
    if len(content) <= limit:
        return content
    head, sep, body = content.partition("\n")
    if sep and len(body) > limit:
        return head + sep + "…" + body[-limit:]
    if sep:
        return content
    return "…" + content[-limit:]


class JsonMemoryStorage:
    """把每个会话的记忆落盘成一份 JSON。

    会话键既可能是群号，也可能是 `private:123456` 这类带符号的键，
    所以文件名做一次替换 + 哈希，避免路径穿越与重名。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, group_id: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_.\-]+", "_", group_id).strip("_")[:40] or "session"
        digest = hashlib.sha1(group_id.encode("utf-8")).hexdigest()[:8]
        return self.root / f"{safe}-{digest}.json"

    def load_all(self) -> dict[str, list[Record]]:
        loaded: dict[str, list[Record]] = {}
        if not self.root.is_dir():
            return loaded
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("读取记忆文件 %s 失败：%s", path.name, exc)
                continue
            if not isinstance(data, dict):
                continue
            group_id = str(data.get("group_id") or "").strip()
            items = data.get("records")
            if not group_id or not isinstance(items, list):
                continue
            records = [
                Record(
                    role=str(item.get("role") or "user"),
                    content=str(item.get("content") or ""),
                    mark=str(item.get("mark") or DEFAULT_MARK),
                )
                for item in items
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            ]
            if records:
                loaded[group_id] = records
        return loaded

    def save(self, group_id: str, records: list[Record]) -> None:
        path = self.path_for(group_id)
        payload = {
            "group_id": group_id,
            "records": [
                {"role": record.role, "content": record.content, "mark": record.mark}
                for record in records
            ],
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(path)
        except OSError as exc:
            logger.warning("写入记忆文件 %s 失败：%s", path.name, exc)

    def clear(self, group_id: str) -> None:
        try:
            self.path_for(group_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除记忆文件失败：%s", exc)


class MemoryStore:
    """按群保存的对话记忆。所有方法都是同步的，单事件循环下无需加锁。"""

    def __init__(
        self,
        max_record: int = cfg.MAX_MEMORY,
        storage: JsonMemoryStorage | None = None,
    ) -> None:
        self._max_record = max_record
        self._records: dict[str, list[Record]] = {}
        self._storage = storage
        if storage is not None:
            self._records.update(storage.load_all())

    # ------------------------------------------------------------------ 持久化
    @property
    def storage(self) -> JsonMemoryStorage | None:
        return self._storage

    def _persist(self, group_id: str) -> None:
        if self._storage is None:
            return
        records = self._records.get(group_id)
        if records:
            self._storage.save(group_id, records)
        else:
            self._storage.clear(group_id)

    # ------------------------------------------------------------------ 基础
    @property
    def max_record(self) -> int:
        return self._max_record

    def set_max_record(self, max_record: int) -> None:
        self._max_record = max(1, max_record)
        for group_id in list(self._records):
            self._trim(group_id, self._max_record)
            self._persist(group_id)

    def _ensure(self, group_id: str) -> list[Record]:
        return self._records.setdefault(group_id, [])

    def _trim(self, group_id: str, limit: int) -> None:
        """裁剪到 limit 条：优先删除最旧的普通记录，持久化记录尽量保留。"""
        records = self._records.get(group_id)
        if not records:
            return
        while len(records) > limit:
            for index, record in enumerate(records):
                if record.mark != PERSISTENCE_MARK:
                    del records[index]
                    break
            else:
                # 全部都是持久化记录，只能删最旧的一条
                records.pop(0)

    def append(
        self,
        group_id: str,
        role: str,
        content: str,
        mark: str = DEFAULT_MARK,
    ) -> Record:
        record = Record(role=role, content=content, mark=mark)
        records = self._ensure(group_id)
        # 先腾出位置再追加，保证新记录不会因为裁剪而立刻被删掉
        self._trim(group_id, max(self._max_record - 1, 0))
        records.append(record)
        self._trim(group_id, self._max_record)
        self._persist(group_id)
        return record

    # ------------------------------------------------------------------ 预算
    def tokens(self, group_id: str) -> int:
        """估算该群当前记忆占用的 token（含每条 4 token 的角色/分隔开销）。"""
        return sum(
            estimate_tokens(record.content) + 4
            for record in self._records.get(group_id, [])
        )

    def trim_to_tokens(self, group_id: str, budget: int) -> int:
        """按 token 预算裁剪，返回删掉的条数。

        `MAX_MEMORY` 是条数上限、这条是字节量上限：短句群里能留更多条，
        长句群里会自动少留几条。持久化记录仍然优先保留。
        """
        if budget <= 0:
            return 0
        records = self._records.get(group_id)
        removed = 0
        while records and self.tokens(group_id) > budget:
            for index, record in enumerate(records):
                if record.mark != PERSISTENCE_MARK:
                    del records[index]
                    removed += 1
                    break
            else:
                # 只剩持久化记录时保留最后一条，避免把群设定裁空
                if len(records) <= 1:
                    break
                records.pop(0)
                removed += 1
        if removed:
            self._persist(group_id)
        return removed

    # ------------------------------------------------------------------ 查询
    def messages(self, group_id: str) -> list[dict[str, str]]:
        return [record.to_message() for record in self._records.get(group_id, [])]

    def records(self, group_id: str) -> list[Record]:
        return list(self._records.get(group_id, []))

    def count(self, group_id: str) -> int:
        return len(self._records.get(group_id, []))

    # ------------------------------------------------------------------ 编辑
    def pop_last(self, group_id: str) -> Record | None:
        records = self._records.get(group_id)
        if not records:
            return None
        record = records.pop()
        self._persist(group_id)
        return record

    def merge_user_tail(self, group_id: str) -> bool:
        """把最后一条用户消息并入前一条用户消息，返回是否合并。

        只在"最后两条都是非持久化的用户消息"时合并，即上一条用户发言
        还没有得到回复（旧版行为）。
        """
        records = self._records.get(group_id)
        if not records or len(records) < 2:
            return False
        last, previous = records[-1], records[-2]
        if last.role != "user" or previous.role != "user":
            return False
        if last.mark != DEFAULT_MARK or previous.mark != DEFAULT_MARK:
            return False
        header, previous_body = _split_header(previous.content)
        _, last_body = _split_header(last.content)
        # 只拼正文：旧做法把整条格式化消息拼进去，合并记录里会重复出现
        # `[时间戳]昵称(QQ号):` 头，既占 token 又让模型认错人。
        body = previous_body if not last_body else f"{previous_body}\n{last_body}"
        previous.content = _truncate(f"{header}{body}" if header else body)
        records.pop()
        self._persist(group_id)
        return True

    def clear(self, group_id: str) -> int:
        """清空某群记忆，返回清掉的条数。"""
        removed = len(self._records.pop(group_id, []))
        self._persist(group_id)
        return removed
