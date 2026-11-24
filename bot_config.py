"""番茄猫娘的业务配置。

这里只放机器人自身的业务配置（模型、概率、群号、生成参数……）。
服务端口、管理员、日志等级等 NoneBot 原生配置放在 `.env` 里。

注意：本文件在 NoneBot 初始化之前就会被导入，所以**不要**在这里导入 nonebot。
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ------------------------------- 身份 -------------------------------
# AI 的名字（同时用于提示词和提示语）
AI_NAME = "tomato"

# ------------------------------- 群与权限 -------------------------------
# 允许机器人工作的群号（字符串形式，必须是字符串）
ALLOW_GROUPS = {"893"}

# 管理员请在 `.env` 的 SUPERUSERS 里配置（NoneBot2 原生配置）。
# 群主/群管理员会自动获得群级指令权限（开关水群、清空记忆等）。

# ------------------------------- 模型 -------------------------------
# 模型源：key 是源名称，value 是连接信息。
#   type     "ollama"（ollama 原生协议）或 "openai"（OpenAI 兼容 API，含 LM Studio / DeepSeek / OneAPI 等）
#   label    显示用名称
#   url      服务地址；OpenAI 兼容源可以省略结尾的 /v1
#   api_key  可选，Bearer Token；本地服务留空即可
#   model    候选模型列表（可用 chatset ss 自动同步）
#   extra_body  可选，OpenAI 兼容源额外透传给接口的字段（如 top_k、repeat_penalty）
MODELS = {
    "local": {
        "type": "ollama",
        "label": "本地 ollama",
        "url": "http://localhost:11434",
        "model": ["llama3.1:latest"],
    },
    "lmstudio": {
        "type": "openai",
        "label": "LM Studio",
        "url": "http://127.0.0.1:1234",  # 也可以写成 http://127.0.0.1:1234/v1
        "api_key": "",
        "model": ["qwen3.5-9b@q6_k", "deepseek-r1-0528-qwen3-8b"],
        # 这两类模型默认会「先思考再回答」，128 token 会被思考内容吃光，
        # LM Studio 支持用 reasoning_effort=none 关闭思考（不支持的服务去掉这一行）
        "extra_body": {"reasoning_effort": "none"},
    },
    # 云端 API 示例：需要时取消注释，把 key 填进去（也可以写 os.getenv("DEEPSEEK_API_KEY", "")）
    # "deepseek": {
    #     "type": "openai",
    #     "label": "DeepSeek",
    #     "url": "https://api.deepseek.com",
    #     "api_key": "sk-xxxx",
    #     "model": ["deepseek-chat"],
    # },
}

# 默认使用的模型源 / 模型下标
# 装了 ollama 就改成 "local"；用 LM Studio 就保持 "lmstudio"（启动 LM Studio 的 server 模式）
DEFAULT_SOURCE = "lmstudio"
DEFAULT_MODEL_INDEX = 0

# ------------------------------- 生成参数 -------------------------------
GLOBAL_CTX = 8192          # 上下文长度
REPEAT_PENALTY = 1.55      # 不重复率（越高越不重复）
PREDICT = 128              # 最多生成的 token 数
TEMPERATURE = 0.85         # 越大感情越丰富
TOP_K = 70                 # k 越大答案越百花齐放
TOP_P = 0.975              # p 越大答案越百花齐放
REQUEST_TIMEOUT = 120.0    # 单次生成超时（秒）；本地小模型可能较慢

# 解码时直接停在这些字符串：提示词要求「不换行、结尾一个情绪标记」，
# 遇到换行说明它开始写多余内容了，本地每多生成一个 token 都要多等。
# 注意不要把 "--*" 放进来，那会把结尾的情绪标记一起切掉。
STOP_SEQUENCES = ("\n",)

# 每次调用后打印 prompt/completion token 与耗时，方便本地对比优化效果
LOG_USAGE = True

# ------------------------------- 行为概率 -------------------------------
# 自动水群时“开口说话”的概率（旧版用 1-p 表示，这里直接用真实概率，默认 3%）
DEFAULT_WATER_PROBABILITY = 0.03
# 回复附带一张情绪表情包的概率
SEND_IMAGE_PROBABILITY = 0.55
# 回复时带上“引用回复”的概率
REPLY_PROBABILITY = 0.5
# 普通群成员允许设置的最大水群概率
NORMAL_USER_MAX_WATER_PROBABILITY = 0.125

# ------------------------------- 记忆 -------------------------------
MAX_MEMORY = 12            # 每个群最多保留的记忆条数
MERGE_MAX_LEN = 128        # 未回复的连续消息合并时，单条内容的最大长度

# ------------------------------- 排队与合并 -------------------------------
# 本地生成一条要几秒，生成期间别人再 @ 就不再各排一条：
# 同一个人连发的消息合并成一条待办，不同的人各占一条，按顺序回复。
PENDING_DEBOUNCE = 0.4     # 收到第一条后先等这么久，把同一波连发合并（秒）
PENDING_TTL = 20.0         # 待办超过这么久还没轮到就丢弃（秒），别回过时的话
PENDING_TEXT_LIMIT = 200   # 合并后的待办文本最多保留多长（字符）

# ------------------------------- 表情包 -------------------------------
EMOTION_PIC_PATH = BASE_DIR / "resources" / "emotions"
# 允许的情绪（与 resources/emotions 下的文件夹名一致）
EMOTIONS = (
    "angry",
    "confusion",
    "disgusted",
    "fear",
    "happy",
    "calm",
    "sad",
    "suki",
    "curious",
    "surprised",
    "other",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
IMAGE_MAX_BYTES = 8 * 1024 * 1024  # 超过这个大小的图不发，避免 QQ 拒收

# ------------------------------- 指令与提示 -------------------------------
# 指令前缀（与旧版保持一致，支持 “ychat”“y chat”“!chat”“tomato chat”“/chat”）
COMMAND_PREFIXES = ("y", "!", "！", "tomato", "/")

# 已注册的指令名
COMMANDS = {"chat", "chatsys", "chatcls", "chatstart", "chatstop", "chatset"}

# 回复长度超过这个值时，往记忆里塞一条“缩短”提示
RESP_LEN_LIMIT = 75
# 每句话结尾必须带的口癖
KEYWORDS = ("喵",)


# ------------------------------- WebUI 覆盖 -------------------------------
# 网页控制台保存设置时会写这个文件；存在时用它覆盖上面的同名配置，
# 这样既不用改 Python 源码，也不用把配置写进 .env。
# 删掉 webui_config.json 就回到本文件的默认值。
import json  # noqa: E402  (放在最后，避免打乱上面的配置阅读顺序)

_WEBUI_OVERLAY = BASE_DIR / "webui_config.json"

_OVERLAY_SCALARS = {
    "AI_NAME": str,
    "DEFAULT_SOURCE": str,
    "DEFAULT_MODEL_INDEX": int,
    "GLOBAL_CTX": int,
    "REPEAT_PENALTY": float,
    "PREDICT": int,
    "TEMPERATURE": float,
    "TOP_K": int,
    "TOP_P": float,
    "REQUEST_TIMEOUT": float,
    "DEFAULT_WATER_PROBABILITY": float,
    "SEND_IMAGE_PROBABILITY": float,
    "REPLY_PROBABILITY": float,
    "NORMAL_USER_MAX_WATER_PROBABILITY": float,
    "MAX_MEMORY": int,
    "MERGE_MAX_LEN": int,
    "RESP_LEN_LIMIT": int,
}


def _apply_webui_overlay() -> None:
    """读取 webui_config.json 覆盖默认配置；文件损坏时保持默认值。"""
    if not _WEBUI_OVERLAY.exists():
        return
    try:
        data = json.loads(_WEBUI_OVERLAY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return

    for key, cast in _OVERLAY_SCALARS.items():
        if key not in data:
            continue
        try:
            globals()[key] = cast(data[key])
        except (TypeError, ValueError):
            continue

    groups = data.get("ALLOW_GROUPS")
    if isinstance(groups, list):
        globals()["ALLOW_GROUPS"] = {str(gid) for gid in groups if str(gid).strip()}

    models = data.get("MODELS")
    if isinstance(models, dict) and models:
        cleaned = {
            str(name): dict(spec)
            for name, spec in models.items()
            if isinstance(spec, dict) and spec.get("url")
        }
        if cleaned:
            globals()["MODELS"] = cleaned

    if DEFAULT_SOURCE not in MODELS:
        globals()["DEFAULT_SOURCE"] = next(iter(MODELS))


_apply_webui_overlay()
