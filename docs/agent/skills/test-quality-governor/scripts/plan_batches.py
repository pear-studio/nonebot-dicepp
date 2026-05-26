#!/usr/bin/env python3
"""Plan review batches from an inventory."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory")
    parser.add_argument("--max-tests", type=int, default=120)
    parser.add_argument("--out")
    args = parser.parse_args()

    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = defaultdict(list)
    for file in inventory.get("test_files", []):
        top = file["path"].split("/", 2)
        key = "/".join(top[:2]) if len(top) > 1 else top[0]
        groups[key].append(file)

    batches = []
    for key, files in sorted(groups.items()):
        current = []
        count = 0
        for file in files:
            n = max(1, len(file.get("tests", [])))
            if current and count + n > args.max_tests:
                batches.append({"group": key, "files": current, "estimated_tests": count})
                current = []
                count = 0
            current.append(file["path"])
            count += n
        if current:
            batches.append({"group": key, "files": current, "estimated_tests": count})

    data = json.dumps({"schema_version": 1, "batches": batches}, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data + "\n", encoding="utf-8")
    else:
        print(data)


if __name__ == "__main__":
    main()
