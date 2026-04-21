#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Review document manager for review skills."""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 encoding for stdout/stderr on Windows to avoid garbled Chinese characters
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

REVIEW_DIR = Path(".temp")


def get_path(filename: str) -> Path:
    path = Path(filename)
    if path.is_absolute():
        return path
    if REVIEW_DIR in path.parents or path.parts[:1] == (REVIEW_DIR.name,):
        return path
    return REVIEW_DIR / path


def cmd_create(args):
    filename = args.filename
    # If filename looks like a topic slug (no date pattern), auto-generate timestamped name
    if not re.match(r"review-\d{6}-\d{4}-", filename):
        slug = filename.rstrip(".md")
        timestamp = datetime.now().strftime("%y%m%d-%H%M")
        filename = f"review-{timestamp}-{slug}.md"
    path = get_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.file:
        tmp = Path(args.file)
        content = tmp.read_text(encoding="utf-8")
    elif args.content is not None:
        tmp = None
        content = args.content
    else:
        print("Error: must provide either content or --file", file=sys.stderr)
        sys.exit(1)
    path.write_text(content, encoding="utf-8")
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    print(path)


def cmd_append(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    if args.file:
        tmp = Path(args.file)
        content = tmp.read_text(encoding="utf-8")
        tmp.unlink(missing_ok=True)
    else:
        content = args.content
    existing = path.read_text(encoding="utf-8")
    path.write_text(existing.rstrip("\n") + "\n\n" + content.strip("\n") + "\n", encoding="utf-8")
    print(path)


def cmd_read(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    print(path.read_text(encoding="utf-8"), end="")


def cmd_update(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    new_text = _apply_update(text, args.rn, args.section, args.content)
    if new_text == text:
        print(f"Warning: {args.rn} not found, no change made.", file=sys.stderr)
        sys.exit(1)

    path.write_text(new_text, encoding="utf-8")
    print(path)


def main():
    parser = argparse.ArgumentParser(description="Review doc CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create a new review doc")
    p_create.add_argument("filename")
    p_create.add_argument("content", nargs="?", help="Document content (or use --file)")
    p_create.add_argument("--file", "-f", help="Path to a file containing the document content")
    p_create.set_defaults(func=cmd_create)

    p_append = sub.add_parser("append", help="Append raw markdown content to an existing review doc")
    p_append.add_argument("filename")
    p_append.add_argument("content", nargs="?", help="Content to append (or use --file)")
    p_append.add_argument("--file", "-f", help="Path to a file containing content to append")
    p_append.set_defaults(func=cmd_append)

    p_read = sub.add_parser("read", help="Read a review doc")
    p_read.add_argument("filename")
    p_read.set_defaults(func=cmd_read)

    p_update = sub.add_parser("update", help="Update a section of an Rn block")
    p_update.add_argument("filename")
    p_update.add_argument("rn", help="e.g. R1")
    p_update.add_argument("section", choices=["Review", "Reply", "Confirm", "Accept", "用户明确"])
    p_update.add_argument("content")
    p_update.set_defaults(func=cmd_update)

    p_batch = sub.add_parser("batch-update", help="Batch update sections for multiple Rn blocks from JSON")
    p_batch.add_argument("filename")
    p_batch.add_argument("json_payload", nargs="?", help='JSON list (or use --file)')
    p_batch.add_argument("--file", "-f", help="Path to a JSON file containing the batch payload")
    p_batch.set_defaults(func=cmd_batch_update)

    args = parser.parse_args()
    args.func(args)


def cmd_batch_update(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    if args.file:
        tmp = Path(args.file)
        json_payload = tmp.read_text(encoding="utf-8")
    elif args.json_payload is not None:
        tmp = None
        json_payload = args.json_payload
    else:
        print("Error: must provide either json_payload or --file", file=sys.stderr)
        sys.exit(1)

    try:
        items = json.loads(json_payload)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON payload: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(items, list):
        print("JSON payload must be a list", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    original_text = text
    not_found = []

    for item in items:
        rn = item.get("rn")
        section = item.get("section")
        content = item.get("content")
        if not rn or not section or content is None:
            print(f"Invalid item: {item}", file=sys.stderr)
            sys.exit(1)

        new_text = _apply_update(text, rn, section, content)
        if new_text == text:
            not_found.append(rn)
        else:
            text = new_text

    if not_found:
        print(f"Warning: the following Rn blocks were not found: {', '.join(not_found)}", file=sys.stderr)

    if text == original_text:
        print("No changes were made.", file=sys.stderr)
        sys.exit(1)

    path.write_text(text, encoding="utf-8")
    if tmp is not None:
        tmp.unlink(missing_ok=True)
    print(path)


def _apply_update(text: str, rn: str, section: str, content: str) -> str:
    pattern = re.compile(r"^(### R\d+ — .*?\n)(.*?)(?=^### R\d+ — |\Z)", re.DOTALL | re.MULTILINE)

    def replacer(m):
        header = m.group(1)
        body = m.group(2)
        if not re.match(rf"### {re.escape(rn)} — ", header):
            return m.group(0)

        sec_header = f"**{section}**"
        sec_pattern = re.compile(
            rf"(\n{re.escape(sec_header)}\n)(.*?)(?=\n\*\*(?:Review|Reply|Confirm|Accept|用户明确)\*\*\n|\Z)",
            re.DOTALL,
        )
        sec_match = sec_pattern.search(body)
        if sec_match:
            new_body = body[: sec_match.start(2)] + f"{content}\n" + body[sec_match.end(2):]
        else:
            new_body = body.rstrip("\n") + f"\n\n{sec_header}\n{content}\n"
        return header + new_body

    return pattern.sub(replacer, text)


if __name__ == "__main__":
    main()
