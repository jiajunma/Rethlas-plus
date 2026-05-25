"""Slash-command templates bundled with rethlas-kb.

Files under this package are markdown templates the
``rethlas-kb install-commands`` subcommand copies into a target
agentic CLI's command directory (e.g. ``~/.claude/commands/``).
The dir layout mirrors the target CLI: ``claude/``, ``codex/``,
``opencode/``. Discoverable via :mod:`importlib.resources`.
"""
