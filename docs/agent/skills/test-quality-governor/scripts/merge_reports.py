#!/usr/bin/env python3
"""Merge JSONL report fragments from subagents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                yield {"type": "invalid", "source": path.as_posix(), "line": line_no, "error": str(exc), "raw": line}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--file-summary-out", required=True)
    parser.add_argument("--action-items-out", required=True)
    args = parser.parse_args()

    file_summaries = []
    action_items = []
    invalid = []

    seen_actions = set()
    for input_path in args.inputs:
        for item in read_jsonl(Path(input_path)):
            typ = item.get("type")
            if typ == "file-summary":
                file_summaries.append(item)
            elif typ == "action-item":
                key = (item.get("file"), tuple(item.get("tests", [])), item.get("action"), item.get("reason"))
                if key not in seen_actions:
                    seen_actions.add(key)
                    action_items.append(item)
            else:
                invalid.append(item)

    fs_out = Path(args.file_summary_out)
    ai_out = Path(args.action_items_out)
    fs_out.parent.mkdir(parents=True, exist_ok=True)
    ai_out.parent.mkdir(parents=True, exist_ok=True)
    fs_out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in file_summaries), encoding="utf-8")
    ai_out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in action_items + invalid), encoding="utf-8")
    print(f"wrote {len(file_summaries)} file summaries and {len(action_items)} action items")


if __name__ == "__main__":
    main()
