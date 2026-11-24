# Contributing

Thanks for taking a look. This is a small project, so keep changes focused and explain the
reason in the pull request.

## Development setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

`requirements-dev.txt` installs the runtime requirements plus `pytest`, `pytest-asyncio` and
`ruff`. You do not need a real model to run the tests: providers are mocked with
`httpx.MockTransport` and the plugin tests use a fake bot.

## Before opening a pull request

- `pytest -q` and `ruff check .` must pass (CI runs both on Python 3.11 and 3.13).
- Add or update tests for behaviour changes; bug fixes should include a regression test.
- Keep configuration backward compatible. New settings belong in `bot_config.py`, with a
  matching entry in `webui/config_store.py` so the console can edit them.
- Do not commit `resources/emotions/`, `webui_config.json`, `webui_prompts.json`, `data/`,
  `logs/` or `.venv/` — they are ignored on purpose.

## Code layout

| Path | Contents |
| --- | --- |
| `bot_plugins/ai_chat/__init__.py` | NoneBot matchers and the group message entry point |
| `bot_plugins/ai_chat/pipeline.py` | prompt assembly, splitting, stickers, reply generation |
| `bot_plugins/ai_chat/providers/` | ollama and OpenAI-compatible backends |
| `bot_plugins/ai_chat/prompts.py` | editable persona/prompt defaults and overlay file |
| `bot_plugins/ai_chat/memory.py` | per-group memory with token budget and persistence |
| `bot_plugins/ai_chat/queue.py` | per-user coalescing and reply serialisation |
| `webui/` | FastAPI routes and the static console |
| `tests/` | pytest suite |

## Commit messages

Conventional prefixes are welcome: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
Write the subject in the imperative mood; put the "why" in the body if it is not obvious.

## License

By contributing you agree that your contribution is released under the MIT license.
