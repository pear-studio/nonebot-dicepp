#!/usr/bin/env python3
"""Sample tests from an inventory for calibration."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out")
    args = parser.parse_args()

    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    rows = []
    for file in inventory.get("test_files", []):
        for test in file.get("tests", []):
            rows.append({"file": file["path"], **test})

    random.seed(args.seed)
    random.shuffle(rows)
    sample = rows[: args.count]
    data = json.dumps(sample, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data + "\n", encoding="utf-8")
    else:
        print(data)


if __name__ == "__main__":
    main()
