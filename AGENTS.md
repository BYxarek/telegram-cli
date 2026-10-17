# Telegram CLI

Local Python CLI for a personal Telegram account, using Telethon. Entry point: `tg.py`.
Use `.agents/skills/telegram-cli/SKILL.md` for CLI operation and implementation tasks.
Shared command and architecture reference: `docs/agent-guide.md`.

Offline validation: `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`.
Development and tests do not require a live account or sending real messages.
Never store API secrets, login codes, passwords, or session files in source or documentation.
Use English for CLI output and developer instructions. Maintain `README.md` in English and `README.ru.md` in Russian.
Respond in the user's requested language; English CLI output does not require English conversation replies.

Use Semble search to locate implementations when available, then read the matching code directly.
Use `content="docs"` for documentation and `content="config"` for configuration.
Use literal repository-wide search when all occurrences are required, such as callers of a changed function.
