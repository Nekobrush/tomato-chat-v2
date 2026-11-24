"""文本处理：消息段转文本、CQ 码清洗、emoji 过滤、情绪提取、指令解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from emoji import demojize

import bot_config as cfg

# --------------------------------------------------------------------------
# 消息段 → 纯文本
# --------------------------------------------------------------------------

_CQ_PATTERN = re.compile(r"\[CQ:([a-zA-Z0-9_.\-]+)((?:,[^\]]*)?)\]")


def _parse_cq_params(raw: str) -> dict[str, str]:
    """解析 CQ 码的参数串，例如 ',qq=123,text=abc'。"""
    params: dict[str, str] = {}
    for item in raw.strip(",").split(","):
        if not item:
            continue
        key, _, value = item.partition("=")
        # CQ 码里的 & 等字符会被转义，这里只做最小还原
        value = value.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]")
        value = value.replace("&amp;", "&")
        params[key.strip()] = value
    return params


def strip_cq(text: str, self_id: int | str | None = None) -> str:
    """把 raw_message 里的 CQ 码清洗成可读文本。

    - @机器人 丢弃；@其他人 变成 `@QQ号`
    - 图片、表情、语音、卡片等一律丢弃
    """
    self_id_str = str(self_id) if self_id is not None else None

    def _replace(match: re.Match[str]) -> str:
        cq_type = match.group(1).lower()
        data = _parse_cq_params(match.group(2))
        if cq_type == "at":
            qq = data.get("qq", "")
            if not qq or qq == "all" or qq == self_id_str:
                return ""
            return f"@{qq}"
        return ""

    return _CQ_PATTERN.sub(_replace, text)


def segments_to_text(message, self_id: int | str | None = None) -> str:
    """把 OneBot v11 的 Message（消息段列表）转成给模型看的纯文本。

    图片等无法理解的内容直接丢弃；纯图片消息会得到空字符串，由调用方决定不回复。
    """
    self_id_str = str(self_id) if self_id is not None else None
    parts: list[str] = []
    for segment in message:
        seg_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if qq and qq != "all" and qq != self_id_str:
                parts.append(f"@{qq}")
        # 其余类型（image / face / record / reply / ...）忽略
    return "".join(parts).strip()


# --------------------------------------------------------------------------
# emoji 过滤
# --------------------------------------------------------------------------

_EMOJI_NAME_PATTERN = re.compile(r":[a-zA-Z0-9_+\-]+:")


def remove_emojis(text: str) -> str:
    """把真正的 emoji 字符转成 `:rainbow:` 这类名字后删掉。"""
    return _EMOJI_NAME_PATTERN.sub("", demojize(text))


# --------------------------------------------------------------------------
# 情绪标记提取
# --------------------------------------------------------------------------

# 正常格式： --*happy*--  /  --*happy*--  （允许中间有空格）
_EMOTION_MARK_PATTERN = re.compile(r"[-—]{1,2}\s*\*\s*([^*\n]{1,32}?)\s*\*\s*[-—]{1,2}")
# 兜底：模型只写了 *happy*
_BARE_EMOTION_PATTERN = re.compile(r"\*\s*([a-zA-Z]{3,20})\s*\*")

# 常见同义词 → 配置里的情绪名
EMOTION_ALIASES = {
    "joy": "happy",
    "happy": "happy",
    "smile": "happy",
    "excited": "happy",
    "anger": "angry",
    "angry": "angry",
    "mad": "angry",
    "sad": "sad",
    "sadness": "sad",
    "cry": "sad",
    "confused": "confusion",
    "confusion": "confusion",
    "puzzled": "confusion",
    "disgust": "disgusted",
    "disgusted": "disgusted",
    "fear": "fear",
    "scared": "fear",
    "afraid": "fear",
    "calm": "calm",
    "relaxed": "calm",
    "suki": "suki",
    "love": "suki",
    "like": "suki",
    "curious": "curious",
    "interested": "curious",
    "surprised": "surprised",
    "surprise": "surprised",
    "shock": "surprised",
    "other": "other",
    "normal": "other",
    "neutral": "other",
}


def extract_emotions(text: str) -> tuple[list[str], str]:
    """从回复里提取情绪标记。

    返回 (合法情绪列表, 去掉情绪标记后的文本)。情绪名会去重并保持出现顺序。
    非法情绪会被删除但不算作有效情绪。
    """
    found: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        token = match.group(1).strip().lower()
        if token:
            found.append(token)
        return ""

    cleaned = _EMOTION_MARK_PATTERN.sub(_collect, text)
    if not found:
        cleaned = _BARE_EMOTION_PATTERN.sub(_collect, cleaned)

    emotions: list[str] = []
    for token in found:
        name = EMOTION_ALIASES.get(token, token)
        if name in cfg.EMOTIONS and name not in emotions:
            emotions.append(name)
    return emotions, cleaned.strip()


# --------------------------------------------------------------------------
# 其他清洗
# --------------------------------------------------------------------------

def collapse_whitespace(text: str) -> str:
    """把换行和连续空白折叠成单个空格，让回复保持"一整句"。"""
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# 指令解析
# --------------------------------------------------------------------------

# 指令名后面必须跟空白（或直接结束），避免 "chatxxx" 被误识别
_COMMAND_SPLIT_PATTERN = re.compile(r"([^\s\u3000]+)[\s\u3000]*(.*)", re.S)


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    args: str


def parse_command(text: str) -> ParsedCommand | None:
    """解析指令，返回 None 表示不是已知指令。

    兼容旧版写法：`ychat`、`y chat`、`!chat`、`！chat`、`tomato chat`、`/chat`。
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    # 长前缀优先，"tomato" 不会被 "y"/"/" 抢走
    for prefix in sorted(cfg.COMMAND_PREFIXES, key=len, reverse=True):
        if not stripped.startswith(prefix):
            continue
        rest = stripped[len(prefix):].lstrip()
        # 允许 "ychat" 这种紧贴写法，也允许 "y chat"
        match = _COMMAND_SPLIT_PATTERN.match(rest)
        if not match:
            continue
        name = match.group(1).lower()
        if name in cfg.COMMANDS:
            return ParsedCommand(name=name, args=match.group(2).strip())
    return None
