"""群记忆存储。

旧版在遍历 range 的同时 pop，会导致索引越界和裁错记录；这里统一重写为
"先插入、再按需裁剪"，并且裁剪时优先保留 persistence（持久化）记录。
"""

from __future__ import annotations

from dataclasses import dataclass

import bot_config as cfg

DEFAULT_MARK = "default"
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


class MemoryStore:
    """按群保存的对话记忆。所有方法都是同步的，单事件循环下无需加锁。"""

    def __init__(self, max_record: int = cfg.MAX_MEMORY) -> None:
        self._max_record = max_record
        self._records: dict[str, list[Record]] = {}

    # ------------------------------------------------------------------ 基础
    @property
    def max_record(self) -> int:
        return self._max_record

    def set_max_record(self, max_record: int) -> None:
        self._max_record = max(1, max_record)
        for group_id in list(self._records):
            self._trim(group_id, self._max_record)

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
        return record

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
        return records.pop()

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
        previous.content = _truncate(f"{previous.content}\n{last.content}")
        records.pop()
        return True

    def clear(self, group_id: str) -> int:
        """清空某群记忆，返回清掉的条数。"""
        return len(self._records.pop(group_id, []))
