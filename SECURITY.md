# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use the repository's
[private vulnerability report](https://github.com/Nekobrush/tomato-chat-v2/security/advisories/new)
or contact the maintainer directly. You can expect an initial reply within a few days.

## Scope

This is a self-hosted chatbot. Interesting reports include, for example:

- authentication bypasses of the web console (`WEBUI_TOKEN`)
- remote code execution through configuration, prompt or message input
- paths where a remote visitor can reach the console once it is behind a reverse proxy
- token/secret leakage in logs

## Hardening notes for operators

- The web console is restricted to localhost unless `WEBUI_TOKEN` is set. A reverse proxy
  makes every request look local — always set `WEBUI_TOKEN` when exposing it.
- `.env`, `webui_config.json` and `webui_prompts.json` may contain API keys; they are
  git-ignored, keep them out of screenshots and issues.
- Set `ONEBOT_ACCESS_TOKEN` if the OneBot connection is not on loopback.
- Run the bot as an unprivileged user; it does not need elevated permissions.
- Keep dependencies updated (`pip install -U -r requirements.txt`).
