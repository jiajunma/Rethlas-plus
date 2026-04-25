"""Dashboard HTML templates (vanilla HTML + minimal JS, ARCHITECTURE §6.7)."""

from pathlib import Path

INDEX_HTML: str = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

__all__ = ["INDEX_HTML"]
