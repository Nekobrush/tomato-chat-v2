"""运行期状态与权限判断。

- 每个群的开关（是否对话、是否自动聊天、水群概率）；
- 超级管理员 / 群管理员的判定。
"""

from __future__ import annotations

from dataclasses import dataclass

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import GroupMessageEvent

import bot_config as cfg


@dataclass
class GroupState:
    enabled: bool = True
    water_enabled: bool = False
    water_probability: float = cfg.DEFAULT_WATER_PROBABILITY


STATES: dict[str, GroupState] = {group_id: GroupState() for group_id in cfg.ALLOW_GROUPS}

_superuser_cache: set[str] | None = None


def superusers() -> set[str]:
    """延迟读取 `.env` 里的 SUPERUSERS，避免在 NoneBot 初始化前访问配置。"""
    global _superuser_cache
    if _superuser_cache is None:
        _superuser_cache = {str(user) for user in get_driver().config.superusers}
    return _superuser_cache


def state(group_id: str) -> GroupState:
    current = STATES.get(group_id)
    if current is None:
        current = GroupState()
        STATES[group_id] = current
    return current


def is_superuser(event: GroupMessageEvent) -> bool:
    return str(event.user_id) in superusers()


def is_group_admin(event: GroupMessageEvent) -> bool:
    return getattr(event.sender, "role", None) in {"owner", "admin"}


def can_manage(event: GroupMessageEvent) -> bool:
    return is_superuser(event) or is_group_admin(event)


def nickname(event: GroupMessageEvent) -> str:
    sender = event.sender
    return (
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or str(event.user_id)
    )
