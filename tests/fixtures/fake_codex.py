"""Test-only emulator of ``codex exec`` (PHASE1 M5 deliverable).

Invoked exactly like the real codex CLI from generator/verifier
wrappers. Behaviour is selected at test time via the
``FAKE_CODEX_SCRIPT`` env var (a JSON-encoded :class:`Script`):

```json
{
  "stdout_lines": [{"text": "...", "delay_s": 0.0}, ...],
  "stderr_lines": [{"text": "...", "delay_s": 0.0}, ...],
  "silent_seconds": 0,
  "exit_code": 0,
  "malformed": false
}
```

Time scaling: every ``delay_s`` and ``silent_seconds`` is multiplied
by ``RETHLAS_TEST_TIME_SCALE`` (default ``1.0``) and
``FAKE_CODEX_TIME_SCALE`` (default ``1.0``) — the env vars are
multiplicative so tests can stack a global tempo with a per-fixture
tempo.

The fake honours **only the positional args it is given** and ignores
flags like ``-C``, ``-m``, ``--sandbox`` so existing wrappers can
invoke it unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass


def _scale() -> float:
    s1 = float(os.environ.get("RETHLAS_TEST_TIME_SCALE", "1.0") or "1.0")
    s2 = float(os.environ.get("FAKE_CODEX_TIME_SCALE", "1.0") or "1.0")
    return max(0.0, s1 * s2)


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    delay_s: float

    @classmethod
    def parse(cls, raw: dict) -> "_Line":
        return cls(text=str(raw.get("text", "")), delay_s=float(raw.get("delay_s", 0.0)))


def _load_script() -> dict:
    raw = os.environ.get("FAKE_CODEX_SCRIPT")
    if not raw:
        return {"exit_code": 0, "stdout_lines": [], "stderr_lines": []}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Unreadable script — exit with a recognisable failure code.
        sys.stderr.write(f"fake_codex: malformed FAKE_CODEX_SCRIPT: {raw!r}\n")
        sys.exit(127)


def main(argv: list[str] | None = None) -> int:
    # Accept and ignore the standard codex flags so wrappers don't break.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("-C", "--cwd", dest="cwd", default=None)
    parser.add_argument("-m", "--model", dest="model", default=None)
    parser.add_argument("--sandbox", dest="sandbox", default=None)
    parser.add_argument("--profile", dest="profile", default=None)
    parser.add_argument("prompt", nargs="?", default="")
    parser.parse_known_args(argv)

    script = _load_script()
    scale = _scale()

    stdout_lines = [_Line.parse(x) for x in script.get("stdout_lines", [])]
    stderr_lines = [_Line.parse(x) for x in script.get("stderr_lines", [])]
    silent_s = float(script.get("silent_seconds", 0.0)) * scale
    exit_code = int(script.get("exit_code", 0))
    malformed = bool(script.get("malformed", False))

    if silent_s > 0:
        time.sleep(silent_s)

    # Interleave stdout/stderr by their delay_s. Simpler: emit stderr
    # first (warnings / banner), then stdout body.
    for line in stderr_lines:
        if line.delay_s > 0:
            time.sleep(line.delay_s * scale)
        sys.stderr.write(line.text)
        if not line.text.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
    for line in stdout_lines:
        if line.delay_s > 0:
            time.sleep(line.delay_s * scale)
        sys.stdout.write(line.text)
        if not line.text.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    if malformed:
        # Emit a deliberately broken final blob so wrapper parsers see
        # the §7.5 "malformed output" path.
        sys.stdout.write("<node>this is not closed properly\n")
        sys.stdout.flush()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
