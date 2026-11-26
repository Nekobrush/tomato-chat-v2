# 更新日志

## 未发布

### 人格与提示词

- 人设拆成三层：**常驻前缀**（身份/安全/格式）、**说话方式**、**人格设定**；旧的单段 `system` 会自动迁移进人格层，不丢配置。
- 新增**设定书**：导入酒馆 World Info v2 与角色书（`entries` 对象或数组都能吃），逐条编辑、导出回酒馆格式；命中条目拼在每轮消息尾部，`position` / `depth` 无法照搬、导入时保留原值。
- 新增**角色卡导入**：PNG（`tEXt` / `iTXt` 的 `chara` / `ccv3`）与 JSON，先预览再确认，拆成人格层、示例与角色书。
- 新增**调试预览**：输入一句话，列出本轮实际发给模型的每条消息、字数与 token 估算。
- 「模型与设置」「人格与提示词」的子选项卡改为**分页**：点左侧索引只显示该分类，不再滚动定位到长页面（未保存的编辑仍保留在各页里）。

### 记忆

- 合并连续消息时只保留一份消息头，不再出现嵌套的 `[时间戳]昵称(QQ号):`。
- 默认保留条数 12 → 48；真正限制长度的是 token 预算（`MEMORY_TOKEN_BUDGET`，默认按上下文的 35%）。
- 读取旧记录时去掉所有时间戳与截断残片，保留昵称与 QQ。
- 记忆可持久化到 `data/memory/`（默认关闭），每个会话一份 JSON。

### 工程化

- 记忆 token 估算、按源并发与上下文、视觉能力服务端探测（LM Studio `/api/v0/models` 的 `type=vlm`、ollama `/api/show` 的 `capabilities`）。
- 连发合并等待与待办过期时间可在网页里调；测试用 fixture 生成群号/机器人 QQ，不再写死。
- 测试 227 → 258。

## v2.0.0 — 2026-11-25

第一个公开发布的 v2 版本，替代上游的 NoneBot1 单文件插件。主要能力：NoneBot2 + OneBot v11、
ollama / OpenAI 兼容双模型后端、内嵌网页控制台、可编辑的人格与提示词、消息前后缀与分条发送、
群聊与私聊记忆（可持久化）、按人排队与聚焦、多模态图片识别，测试 227 个。

仓库层面：补齐 `pyproject.toml`、`LICENSE`(MIT)、CI、ruff、Issue / PR 模板、
Dockerfile 与 systemd unit，并把 README 拆成中英两份。

### 工程化

- 新增 `pyproject.toml`：项目元数据、依赖、`pytest` / `ruff` 配置集中管理；`pytest.ini` 并入后删除。
- 版本号单一来源：`bot_plugins/ai_chat/_version.py`，WebUI `/api/health` 从它读取。
- 新增 `ruff` 配置与 CI 中的 lint 步骤；`requirements-dev.txt` 增加 `ruff`。
- 提供 `tomato-chat` 命令（`pip install -e .[dev]`），`.gitignore` 补充 `build/`、`dist/`。

### 相对上游 v1（NoneBot1 时代）的主要变化

首个基于 NoneBot2 的版本。从原来的 NoneBot1 单文件插件整体重写：框架换代、模型层抽象、加了一整套网页控制台，并把「人设 / 提示词 / 消息装饰 / 分条发送」全部做成可配置项。

### 迁移

- **NoneBot1 → NoneBot2 2.5.0**，适配器 `nonebot-adapter-onebot` 2.4.6，Python 3.10+（实测 3.14.7）。
- 单个 `bot_plugins/ai_chat.py`（1090 行）拆成包：`prompts` / `memory` / `text` / `model` / `providers/` / `pipeline` / `queue` / `private` / `commands` / `runtime` / `messaging`。
- 新增 `main.py`、`.env`、`requirements.txt`、`pytest.ini` 与 `tests/`；删除旧的 `requirement.txt`。

### 模型源

- 模型层抽象为 `providers/`：支持 **ollama** 与 **OpenAI 兼容 API**（LM Studio、DeepSeek、OneAPI 等），配置里用 `type` 区分，旧配置（不带 `type`）仍按 ollama 处理。
- 多模型源之间可切换、可同步模型列表，默认源与默认模型可在配置里指定，也能在网页里点一下切换。
- OpenAI 兼容源自动补 `/v1`、支持 `api_key`、`extra_body`；同步模型时会过滤掉 embedding 类模型。
- 推理模型（Qwen3、DeepSeek-R1）思考占满 token 时给出明确提示，而不是回一条空消息。

### 网页控制台（WebUI）

- 内嵌在机器人自己的服务里，与 OneBot 共用 8765 端口，只跑一个进程；地址 `http://127.0.0.1:8765/webui/`。
- 页面：**所有群总览** / **模型与设置** / **人格与提示词** / **运行日志** / **关于**，以及单群的 **概览 / 记忆 / 试聊**。
- 深色与浅色两套主题。
- 设置写入 `webui_config.json`，提示词写入 `webui_prompts.json`，删掉文件即回默认值；保存即时生效（AI 名字也不再需要重启）。
- **增量刷新**：状态页每 8 秒只替换真正变了的区块（不打断选中与滚动），日志页 4 秒追加新行（可关闭自动刷新），设置页与人格页不自动刷新。
- 远程访问可用 `WEBUI_TOKEN` 保护（默认只允许本机）。

### 人格与提示词

- 人设、两条 few-shot 示例、三条纠正提示、失败兜底、私聊说明、聚焦指令、时间说明、空回复兜底、消息前后缀，全部可在网页里逐条编辑。
- 支持占位符：`{ai_name}` `{emotions}` `{limit}` `{who}` `{target}` `{quoted}` `{timestamp}` `{time}` `{self_id}`。
- **AI 名字**归入人格设定，保存后下一条回复即生效；「恢复默认」会连名字一起重置。

### 消息装饰与分条

- **起始符 / 前缀 / 后缀 / 结束符** 四层装饰：`起始符 + 前缀 + 正文 + 后缀 + 句末标点 + 结束符`。
- 后缀默认插在句末标点**之前**（`你好。` → `你好[后缀]。`），结束符永远在最末。
- **分条发送**：可按正则表达式切分，也可按字数硬切（留空则不切），多条气泡之间延迟发送；引用回复与表情包挂在第一条。
- 分条间隔为「基准 + 按上一条字数线性增加 + 随机浮动」，长句停得久、短句节奏快。

### 私聊

- 私聊复用同一套人设、记忆与表情包，但跳过群白名单、水群概率、群开关、排队合并这些群聊机制。
- 记忆独立（`private:<QQ>`），与群记忆互不污染；可用 `PRIVATE_CHAT_ENABLED` 关闭、`ALLOW_PRIVATE_USERS` 限定对象。

### 排队与聚焦

- 连续 `@` 机器人时，同一个人的消息会合并成一次生成，不同的人按先来后到各回一条。
- 生成时会点名「现在轮到回应谁」，减少把别人的话题混进回答。
- 过期的待办会丢弃（默认 20 秒），不会在很久之后突然回一句。

### 本地推理优化

- **稳定提示词前缀**：人设与持久化设定固定在最前面，时间戳、纠错提示等易变内容挪到末尾，老记录里的时间戳前缀会被剥掉，让 llama.cpp / LM Studio 的 prompt cache 能命中。
- 记录动作移到锁外，并使用消息的**事件时间**而不是处理时间。
- 默认用换行作停止符，减少无谓的生成；模型调用会打印 prompt / completion token 与耗时，便于对比。
- 附带 `tools/bench_prompt.py`，可对比新旧两种提示词组装的 token 数与两轮 prefill 耗时。

### 启动方式

- 双击 `start.bat` 会弹出可见的控制台窗口，实时打印日志；**关掉窗口即停止运行**。
- `launcher.py` 自动创建 `.venv`、按需安装依赖、等待服务就绪后打开浏览器；支持 `--no-browser`、`--port N`、`--reinstall`。

### 修复

迁移过程中一并修掉的旧问题：

- 记忆裁剪把刚写入的记录立刻删掉。
- `sync_source` 里引用了未定义的变量（`NameError`）。
- 提示词声明毫秒时间戳、实际写入秒级浮点，单位不一致。
- 边遍历边 `pop` 导致索引偏移。
- `SESSION_EXPIRE_TIMEOUT` 重复定义。
- `chatcls` / `chatstart` / `chatstop` 没有权限校验。
- 「自动刷新」勾选框没有绑定事件，是个死控件。
- 推理模型返回空正文时直接发出空消息。

### 升级注意

- `.env` 需要按新结构重写（`DRIVER` / `HOST` / `PORT` / `SUPERUSERS` / `COMMAND_START` / `ONEBOT_ACCESS_TOKEN` 等），参考 `.env.example`。
- 旧的 `requirement.txt` 已删除，改用 `requirements.txt`。
- `ALLOW_GROUPS` 是严格白名单：留空则任何群都不响应。
- 默认模型源是 `lmstudio`（本机没装 ollama）；用 ollama 请把 `DEFAULT_SOURCE` 改回 `"local"`。
- 新增的 `webui_config.json` / `webui_prompts.json` 都是「可选覆盖」，不放进版本库。

### 测试

- `pytest -q` → **180 passed**，覆盖指令解析、记忆裁剪与合并、模型源切换与同步、两种 provider 的请求与错误处理、提示词前缀稳定性、排队合并、私聊、分条发送、WebUI 接口与配置校验。

### 已知限制

- 群记忆只在内存里，重启即清空。
- 单张显卡上串行生成是物理下限，并发只会让每条都变慢；多人群聊靠「排队 + 聚焦」缓解，不保证完全不串话题。
- 水群路径不经过排队合并。
- 表情包目录（约 390MB）需要单独拷贝，不随仓库分发。
