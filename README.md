# Tomato AI Chat（NoneBot2 版）

一只会在 QQ 群里水群的猫娘聊天机器人：本地大模型生成回复，按情绪配一张猫娘表情包，带群聊记忆。

项目由早期的 NoneBot1 版本迁移而来（见文末「迁移说明」），运行在 **Python 3.14 + NoneBot2 2.5.0 + OneBot V11** 上。

## 功能

- **人设聊天**：每句结尾带「喵」，回复末尾用 `--*情绪*--` 标记情绪，机器人据此从对应文件夹抽图。
- **群聊记忆**：每个群独立保存最近 12 条对话；连续发消息会自动合并；`chatsys --p` 设定的内容不会被裁掉。
- **主动水群**：`@机器人` 必回；开启自动聊天后按概率随机接话，普通成员可调的概率有上限。
- **模型管理**：支持多个模型源（不同 ollama 地址）与多个模型，可热切换、可从 ollama 同步模型列表。
- **群级开关**：按群开启/关闭对话与自动聊天，支持群白名单。
- **权限分级**：超级管理员（`.env` 的 `SUPERUSERS`）、群主/群管理员、普通成员三级权限。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python | ≥ 3.10，本项目在 3.14.7 上开发与测试 |
| NoneBot2 | 2.5.0（`nonebot2[fastapi]`） |
| OneBot 适配器 | nonebot-adapter-onebot 2.4.6（OneBot V11） |
| ollama | 本地模型服务，默认 `http://localhost:11434` |
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
| `MODELS` | 模型源表：`{源名: {"label": ..., "url": ..., "model": [...]}}` |
| `DEFAULT_SOURCE` / `DEFAULT_MODEL_INDEX` | 默认模型源与默认模型下标 |
| `GLOBAL_CTX` / `PREDICT` / `TEMPERATURE` / `TOP_K` / `TOP_P` / `REPEAT_PENALTY` | 生成参数 |
| `MAX_MEMORY` | 每群记忆条数 |
| `DEFAULT_WATER_PROBABILITY` | 自动聊天时被概率（0.03 表示约 3% 的消息会接话） |
| `SEND_IMAGE_PROBABILITY` | 回复附带表情包的概率 |
| `REPLY_PROBABILITY` | 回复时使用 QQ「引用回复」的概率 |
| `EMOTION_PIC_PATH` | 表情包根目录 |
| `EMOTIONS` | 情绪名列表，同时决定表情包文件夹名 |

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

先确保 ollama 正在运行并已拉取模型：

```bash
ollama serve
ollama pull llama3.1:latest
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
| `chatset ss [源]` | 超级管理员 | 从 ollama 同步模型列表 |
| `chatset c` / `chatset r` | 群主/群管理员 | 关闭 / 重新开启本群对话 |

启动时 `chatset ss` 会把 ollama 里已安装的模型写回 `bot_config.MODELS`，所以首次使用建议先同步一次。

## 目录结构

```
tomato-chat/
├── main.py                     # 入口：初始化 NoneBot、注册适配器、加载插件
├── bot_config.py               # 业务配置
├── .env / .env.example         # NoneBot 原生配置
├── requirements.txt
├── bot_plugins/
│   └── ai_chat/
│       ├── __init__.py         # 事件入口、指令分发、权限判断
│       ├── prompts.py          # 人设与纠错提示
│       ├── text.py             # CQ 码 / emoji / 情绪解析、指令解析
│       ├── memory.py           # 群记忆存储
│       ├── model.py            # ollama 模型服务
│       └── pipeline.py         # 生成流程：记忆 → 模型 → 表情包 → 消息
├── resources/emotions/         # 表情包
└── tests/                      # pytest 测试
```

## 测试

```bash
pytest -q
```

当前 75 个用例全部通过，覆盖指令解析、记忆裁剪/合并、模型切换、生成流程和插件冒烟。

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
