"""对比「旧提示词组装」与「稳定前缀」的实际代价。

用法（LM Studio 或任意 OpenAI 兼容服务先启动）：

    .\\.venv\\Scripts\\python.exe tools\\bench_prompt.py

只做只读测量，不写入群记忆、不发消息。

测量方法：每次只生成 1 个 token（`max_tokens=1`），这样耗时基本等于 prefill。
- 旧方案：时间戳拼在第 0 条 system 里，两次请求的前缀不同 → 两次都要重算；
- 新方案：前缀固定，第二次请求应当明显更快（KV cache 命中）。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

import bot_config as cfg  # noqa: E402
from bot_plugins.ai_chat import pipeline  # noqa: E402
from bot_plugins.ai_chat.prompts import SYSTEM_MESSAGES  # noqa: E402

GROUP = "bench"
BOT_ID = 10000

SAMPLE = [
    ("小明", 123, "今天好累啊"),
    ("小红", 456, "tomato 你会唱歌吗"),
    ("小明", 123, "别理她，先安慰我喵"),
]


def build_old() -> list[dict[str, str]]:
    """旧实现：时间戳拼在第 0 条 system 里，前缀每轮都变。"""
    history = pipeline.store.messages(GROUP)
    parts = [SYSTEM_MESSAGES[0]["content"]]
    parts.extend(record["content"] for record in history if record["role"] == "system")
    parts.append(pipeline.system_context(BOT_ID)["content"])
    messages = [{"role": "system", "content": "\n\n".join(parts)}]
    messages.extend(SYSTEM_MESSAGES[1:])
    messages.extend(record for record in history if record["role"] != "system")
    return messages


def build_new() -> list[dict[str, str]]:
    return pipeline.build_messages(GROUP, BOT_ID)


async def call(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": cfg.TEMPERATURE,
        "top_p": cfg.TOP_P,
        "max_tokens": max_tokens,
        "stop": list(cfg.STOP_SEQUENCES),
    }
    started = time.perf_counter()
    response = await client.post("chat/completions", json=payload)
    response.raise_for_status()
    elapsed = time.perf_counter() - started
    data = response.json()
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "elapsed": elapsed,
    }


async def main() -> None:
    spec = cfg.MODELS[cfg.DEFAULT_SOURCE]
    if spec.get("type") != "openai":
        raise SystemExit(f"当前默认源 {cfg.DEFAULT_SOURCE!r} 不是 OpenAI 兼容源，无法测量 usage")
    model = spec["model"][cfg.DEFAULT_MODEL_INDEX]

    pipeline.reset_group(GROUP)
    for nickname, user_id, text in SAMPLE:
        pipeline.remember_user(GROUP, nickname, user_id, text, 1763000000000)
        pipeline.store.append(GROUP, "assistant", f"{nickname}说得对喵--*calm*--")

    base_url = str(spec["url"]).rstrip("/")
    if "/v1" not in base_url:
        base_url += "/v1"

    async with httpx.AsyncClient(base_url=base_url + "/", timeout=cfg.REQUEST_TIMEOUT) as client:
        print(f"模型源：{cfg.DEFAULT_SOURCE} / {model}")
        print("预热中（加载模型，不计入结果）…")
        await call(client, model, [{"role": "user", "content": "喵"}], max_tokens=1)

        # 先量 token 数（正常生成长度，各一次）
        old_messages = build_old()
        new_messages = build_new()
        for label, messages in (("旧方案", old_messages), ("新方案", new_messages)):
            stats = await call(client, model, messages, max_tokens=cfg.PREDICT)
            print(
                f"{label}：prompt={stats['prompt_tokens']} tok，"
                f"completion={stats['completion_tokens']} tok，耗时={stats['elapsed']:.2f}s"
            )

        # 再量「连续两轮」的 prefill 代价：旧方案每轮时间戳都变，新方案前缀不变
        print("\n连续两轮 prefill 耗时（max_tokens=1，近似纯 prefill）：")
        for label, builder in (("旧方案", build_old), ("新方案", build_new)):
            first = await call(client, model, builder(), max_tokens=1)
            second = await call(client, model, builder(), max_tokens=1)
            print(
                f"{label}：第一轮 {first['elapsed']:.2f}s → 第二轮 {second['elapsed']:.2f}s"
                f"（prompt={second['prompt_tokens']} tok）"
            )

        # 最后量真实长度的生成速度：本地 decode 才是主要等待来源
        print("\n真实生成长度（max_tokens=PREDICT）：")
        stats = await call(client, model, build_new(), max_tokens=cfg.PREDICT)
        completion = stats["completion_tokens"] or 0
        print(
            f"prompt={stats['prompt_tokens']} tok，completion={completion} tok，"
            f"耗时={stats['elapsed']:.2f}s"
            + (f"，约 {completion / stats['elapsed']:.1f} tok/s" if completion else "")
        )


if __name__ == "__main__":
    asyncio.run(main())
