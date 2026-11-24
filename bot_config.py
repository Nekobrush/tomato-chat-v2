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
# 模型源：key 为源名称，value 为 ollama 地址与候选模型
MODELS = {
    "local": {
        "label": "本地",
        "url": "http://localhost:11434",  # ollama 服务地址
        "model": ["llama3.1:latest"],
    },
}

# 默认使用的模型源 / 模型下标
DEFAULT_SOURCE = "local"
DEFAULT_MODEL_INDEX = 0

# ------------------------------- 生成参数 -------------------------------
GLOBAL_CTX = 8192          # 上下文长度
REPEAT_PENALTY = 1.55      # 不重复率（越高越不重复）
PREDICT = 128              # 最多生成的 token 数
TEMPERATURE = 0.85         # 越大感情越丰富
TOP_K = 70                 # k 越大答案越百花齐放
TOP_P = 0.975              # p 越大答案越百花齐放
REQUEST_TIMEOUT = 120.0    # 单次生成超时（秒）；本地小模型可能较慢

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
