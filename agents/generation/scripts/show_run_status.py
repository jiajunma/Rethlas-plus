#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("problem_id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = REPO_ROOT / "results" / args.problem_id / "run_state.json"
    if not state_path.exists():
        print(f"No run_state.json found for {args.problem_id}")
        return 1
    data = json.loads(state_path.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
