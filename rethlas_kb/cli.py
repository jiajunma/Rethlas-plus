"""rethlas-kb CLI entry point.

v0.0.1 stub — subcommands land in issues #8–#15.
"""

from __future__ import annotations

import sys

__version__ = "0.0.1"


def main(argv: list[str] | None = None) -> int:
    """Stub CLI entry. Future: subcommand dispatch (verify-stmt / verify-proof / ...)."""
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in ("-h", "--help", "help"):
        print(_help_text())
        return 0
    if args and args[0] in ("-V", "--version", "version"):
        print(f"rethlas-kb {__version__}")
        return 0

    if not args:
        print(_help_text(), file=sys.stderr)
        return 0

    print(f"rethlas-kb: unknown command {args[0]!r}", file=sys.stderr)
    print("See `rethlas-kb --help`.", file=sys.stderr)
    return 2


def _help_text() -> str:
    return (
        f"rethlas-kb v{__version__} — agent layer on mdblueprint KB.\n"
        "\n"
        "Status: v1 scaffold only. Subcommands forthcoming per ROADMAP.md.\n"
        "\n"
        "Planned commands (v1):\n"
        "  verify-stmt <node-id>             (issue #8)\n"
        "  verify-proof <node-id>            (issue #9)\n"
        "  fill-gap <node-id>                (issue #10)\n"
        "  hunt-counterexample <node-id>     (issue #11)\n"
        "  audit-source <node-id>            (issue #12)\n"
        "\n"
        "See: https://github.com/jiajunma/Rethlas-plus/issues?q=label%3Arethlas-kb\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
