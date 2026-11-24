# Tomato AI Chat（NoneBot2 版）

一只会在 QQ 群里水群的猫娘聊天机器人：本地大模型生成回复，按情绪配一张猫娘表情包，带群聊记忆。

项目由早期的 NoneBot1 版本迁移而来（见文末「迁移说明」），运行在 **Python 3.14 + NoneBot2 2.5.0 + OneBot V11** 上。

## 功能

- **人设聊天**：每句结尾带「喵」，回复末尾用 `--*情绪*--` 标记情绪，机器人据此从对应文件夹抽图。
- **群聊记忆**：每个群独立保存最近 12 条对话；连续发消息会自动合并；`chatsys --p` 设定的内容不会被裁掉。
- **主动水群**：`@机器人` 必回；开启自动聊天后按概率随机接话，普通成员可调的概率有上限。
- **模型管理**：支持两类模型源——ollama 原生协议，以及 OpenAI 兼容 API（LM Studio、DeepSeek、OneAPI 等）；可多源、多模型热切换，可从当前源同步模型列表。
- **群级开关**：按群开启/关闭对话与自动聊天，支持群白名单。
- **权限分级**：超级管理员（`.env` 的 `SUPERUSERS`）、群主/群管理员、普通成员三级权限。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python | ≥ 3.10，本项目在 3.14.7 上开发与测试 |
| NoneBot2 | 2.5.0（`nonebot2[fastapi]`） |
| OneBot 适配器 | nonebot-adapter-onebot 2.4.6（OneBot V11） |
| ollama | 可选：本地模型服务，默认 `http://localhost:11434`；也可以用 OpenAI 兼容服务代替 |
| LM Studio / 云端 API | 可选：任何 OpenAI 兼容接口（LM Studio、DeepSeek、OneAPI 网关等） |
| QQ 机器人框架 | 支持 OneBot V11 反向 WebSocket 的实现（NapCat、LLOneBot、Lagrange.OneBot 等） |

## 安装

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
# 需要跑测试时再装
pip install -r requirements-dev.txt
```

## 配置

配置分两处，互不冲突：

- **`.env`**：NoneBot 自身配置（复制 `.env.example` 后修改）。
- **`bot_config.py`**：机器人业务配置，直接改 Python 常量即可。

### `.env`

| 键 | 说明 |
| --- | --- |
| `DRIVER` | 默认 `~fastapi`，提供反向 WebSocket 服务 |
| `HOST` / `PORT` | 监听地址，默认 `0.0.0.0:8765` |
| `LOG_LEVEL` | 日志级别 |
| `SUPERUSERS` | 超级管理员 QQ 列表（旧版 `super_admin_users` + `admin_users` 合并） |
| `COMMAND_START` | 指令前缀，必须与 `bot_config.COMMAND_PREFIXES` 一致 |
| `ONEBOT_ACCESS_TOKEN` / `ONEBOT_SECRET` | 反向 WS 校验用，留空表示不校验 |

### `bot_config.py` 常用项

| 常量 | 说明 |
| --- | --- |
| `AI_NAME` | 机器人自称，写进人设 |
| `ALLOW_GROUPS` | 允许工作的群号集合，**必须把群号写进来**；留空则机器人不理会任何群 |
| `MODELS` | 模型源表，每项支持 `type`（`ollama` / `openai`）、`label`、`url`、`api_key`、`model`、`extra_body` |
| `DEFAULT_SOURCE` / `DEFAULT_MODEL_INDEX` | 默认模型源与默认模型下标 |
| `GLOBAL_CTX` / `PREDICT` / `TEMPERATURE` / `TOP_K` / `TOP_P` / `REPEAT_PENALTY` | 生成参数 |
| `MAX_MEMORY` | 每群记忆条数 |
| `DEFAULT_WATER_PROBABILITY` | 自动聊天时被概率（0.03 表示约 3% 的消息会接话） |
| `SEND_IMAGE_PROBABILITY` | 回复附带表情包的概率 |
| `REPLY_PROBABILITY` | 回复时使用 QQ「引用回复」的概率 |
| `EMOTION_PIC_PATH` | 表情包根目录 |
| `EMOTIONS` | 情绪名列表，同时决定表情包文件夹名 |

### 模型源（ollama / OpenAI 兼容）

`bot_config.MODELS` 是一个字典，键是源名称，值描述连接方式：

```python
MODELS = {
    # ollama 原生协议
    "local": {
        "type": "ollama",
        "label": "本地 ollama",
        "url": "http://localhost:11434",
        "model": ["llama3.1:latest"],
    },
    # OpenAI 兼容：LM Studio
    "lmstudio": {
        "type": "openai",
        "label": "LM Studio",
        "url": "http://127.0.0.1:1234",  # 结尾的 /v1 可省略
        "api_key": "",
        "model": ["qwen3.5-9b@q6_k"],
        "extra_body": {"reasoning_effort": "none"},
    },
    # OpenAI 兼容：云端 API
    "deepseek": {
        "type": "openai",
        "label": "DeepSeek",
        "url": "https://api.deepseek.com",
        "api_key": "sk-...",
        "model": ["deepseek-chat"],
    },
}
```

- `type` 省略时按 `ollama` 处理，兼容旧配置；也接受 `openai-compatible`、`lmstudio` 等别名。
- OpenAI 兼容源会自动补齐地址结尾的 `/v1`。
- `api_key` 非空时自动带上 `Authorization: Bearer <api_key>`。
- `extra_body` 里的字段会原样合并进请求体，用来传各家的专有参数。
- 推理模型（DeepSeek-R1、Qwen3 思考模式）默认「先思考再回答」，128 token 会被思考内容吃光。LM Studio 可加 `"extra_body": {"reasoning_effort": "none"}` 关闭思考，或调大 `PREDICT`。
- 切换源：`chatset s <源>`；同步模型列表：`chatset ss [源]`（ollama 调 `/api/tags`，OpenAI 兼容调 `/v1/models`）。

### 准备表情包

按情绪分文件夹放图片即可，支持 `.png/.jpg/.jpeg/.gif/.webp`：

```
resources/emotions/
├── happy/
├── sad/
├── angry/
├── calm/
├── suki/
├── surprised/
├── confusion/
├── fear/
├── disgusted/
├── curious/
└── other/
```

单张图片默认上限 8 MB（`IMAGE_MAX_BYTES`），超出会被跳过。

## 启动

先让默认模型源可用。二选一：

```bash
# 方式 A：ollama（把 bot_config.DEFAULT_SOURCE 改成 "local"）
ollama serve
ollama pull llama3.1:latest

# 方式 B：LM Studio（默认配置用的就是它）
lms server start      # 或在 LM Studio 界面里开启 Local Server
```

再启动机器人：

```bash
python main.py
```

正常时日志会显示插件加载成功、适配器就绪，并监听 `http://0.0.0.0:8765`。
反向 WebSocket 地址为：

```
ws://127.0.0.1:8765/onebot/v11/ws
```

> 用 HTTP 直接访问该地址会返回 404，这是正常的——它是 WebSocket 专用路由。

## 接入 QQ

在机器人框架里新增一个**反向 WebSocket** 连接，地址填上面的 `ws://…/onebot/v11/ws`；若 `.env` 里设置了 `ONEBOT_ACCESS_TOKEN`，在框架侧填同一个 token。连接成功后，把机器人拉进群，发 `tomato chat 你好` 或 `@机器人 你好` 即可。

## 指令

前缀可用 `/`、`!`、`！`、`y`、`tomato`，例如 `/chat`、`y chat`、`tomato chat` 都行。

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `chat <内容>` | 所有人 | 让机器人回复一句 |
| `chatsys <内容>` | 超级管理员 | 追加群设定，`--p` 表示持久化（不参与裁剪） |
| `chatcls` | 群主/群管理员 | 清空本群记忆 |
| `chatstart` | 群主/群管理员 | 开启本群自动聊天（水群） |
| `chatstop` | 群主/群管理员 | 关闭本群自动聊天 |
| `chatset p <概率>` | 所有人（普通成员上限 0.125） | 设置本群水群概率，如 `chatset p 5%` |
| `chatset l` | 所有人 | 查看当前模型源与模型 |
| `chatset s <源>` | 超级管理员 | 切换模型源（`bot_config.MODELS` 里的键名） |
| `chatset m <序号>` | 超级管理员 | 切换模型 |
| `chatset ss [源]` | 超级管理员 | 从模型源同步模型列表 |
| `chatset c` / `chatset r` | 群主/群管理员 | 关闭 / 重新开启本群对话 |

`chatset ss` 会把模型源里可用的模型列表写回 `bot_config.MODELS`（仅本次运行有效），首次使用建议先同步一次。

## 目录结构

```
tomato-chat/
├── main.py                     # 入口：初始化 NoneBot、注册适配器、加载插件
├── bot_config.py               # 业务配置
├── .env / .env.example         # NoneBot 原生配置
├── requirements.txt
├── bot_plugins/
│   └── ai_chat/
│       ├── __init__.py         # matcher 接线、普通消息处理
│       ├── commands.py         # 指令分发与各指令实现
│       ├── runtime.py          # 群状态与权限判断
│       ├── messaging.py        # 发送辅助（记录 → 生成 → 发送）
│       ├── prompts.py          # 人设与纠错提示
│       ├── text.py             # CQ 码 / emoji / 情绪解析、指令解析
│       ├── memory.py           # 群记忆存储
│       ├── errors.py           # LLMError
│       ├── model.py            # 模型源管理、统一调用入口
│       ├── providers/          # 模型后端
│       │   ├── base.py         #   抽象基类与生成参数
│       │   ├── ollama.py       #   ollama 原生协议
│       │   └── openai.py       #   OpenAI 兼容协议
│       └── pipeline.py         # 生成流程：记忆 → 模型 → 表情包 → 消息
├── resources/emotions/         # 表情包
└── tests/                      # pytest 测试
```

## 测试

```bash
pytest -q
```

当前 111 个用例全部通过，覆盖指令解析、记忆裁剪/合并、模型切换与同步、两种模型后端的请求/错误处理、生成流程和插件冒烟。

## 迁移说明（NoneBot1 → NoneBot2）

- 依赖升级到 `nonebot2 2.5.0` + `nonebot-adapter-onebot 2.4.6`，运行在 Python 3.14。
- 单个 1090 行的插件拆成 `prompts` / `text` / `memory` / `model` / `pipeline` / `__init__` 六个模块，事件处理与生成逻辑分离。
- 指令分发改为自己解析前缀：`on_command` 只认「前缀+指令名」紧挨着的写法，会丢掉旧版的 `tomato chat` 这种带空格的用法。
- 修复了旧版的问题：
  - `sync_source` 里引用未定义的 `key` 导致 `NameError`；
  - 记忆时间戳单位不一致（提示词说毫秒、实际写秒）；
  - 遍历 `range` 时 `pop` 造成漏删 / 索引错位；
  - 新记录写入后立刻被裁掉；
  - `SESSION_EXPIRE_TIMEOUT` 重复定义；
  - 清空记忆、开关自动聊天等指令没有权限校验；
  - 缺少每群并发锁，多人同时触发会打乱记忆顺序。
- 行为变化：`chatset p` 现在表示「接话概率」本身（旧版内部存的是 `1-p`，容易配反）；管理员统一用 `SUPERUSERS` 配置；不再通过 `NICKNAME` 触发，避免把 `tomato` 指令前缀吃掉。

## 重构说明（兼容 OpenAI API）

- **模型层解耦**：`model.LLMService` 不再直接持有 ollama 客户端，而是通过 `providers/` 按 `type` 创建后端（`ollama` / `openai`），统一 `GenerationParams` 映射生成参数；provider 懒创建，NoneBot 退出时统一关闭连接。
- **新增 OpenAI 兼容后端**：自动补齐 `/v1`、支持 `api_key`（Bearer）、`extra_body` 透传；HTTP 错误会带上状态码与响应摘要，推理模型把 token 花在思考上时会给出可操作的提示。
- **插件拆分**：`__init__.py` 只负责 matcher 接线与普通消息处理；指令分发、群状态/权限、发送流程分别落到 `commands.py`、`runtime.py`、`messaging.py`。
- **提示词兼容性**：请求只保留一条开头的 `system` 消息，`chatsys` 追加的设定会合并进去；LM Studio 的 Qwen 模板会直接拒绝出现在对话中间的 `system`。
- **向后兼容**：旧写法 `MODELS = {源名: {"url": ..., "model": [...]}}` 不带 `type` 时仍按 ollama 处理。
- **测试**：从 75 个增加到 110 个，新增 `tests/test_providers.py`（用 `httpx.MockTransport` 验证请求体/鉴权头/错误映射，用假客户端验证 ollama 参数映射）。
- 已在 LM Studio（`qwen3.5-9b@q6_k`）上做过真实联调：`chatset ss` 能同步模型列表，`chat` 能正常拿到回复。
