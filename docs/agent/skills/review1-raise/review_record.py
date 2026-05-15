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

# ── Stage management ──────────────────────────────────────────────
STAGE_BLOCK_HEADER = "**阶段状态**"
STAGE_DEFS = {
    1: "评审发起 (review1-raise)",
    2: "作者回复 (review2-reply)",
    3: "审阅者确认 (review3-confirm)",
    4: "实施 (review4-execute)",
    5: "验收 (review5-accept)",
}
SECTION_TO_STAGE = {"Reply": 2, "Confirm": 3, "Accept": 5}
GATE_REQUIRES = {2: 1, 3: 2, 4: 3, 5: 4}


def _parse_stages(text: str) -> dict:
    """Parse stage checklist, return {stage_num: checked}."""
    block = re.search(
        rf"{re.escape(STAGE_BLOCK_HEADER)}\n((?:- \[.\] \d+\..*\n?)+)",
        text, re.MULTILINE,
    )
    if not block:
        return {}
    stages = {}
    for m in re.finditer(r"- \[(.)\] (\d+)\.", block.group(1)):
        stages[int(m.group(2))] = m.group(1) != " "
    return stages


def _ensure_stage_block(text: str, stage1_done: bool = False) -> str:
    """Ensure stage block exists. Adds missing stage 1 to legacy blocks."""
    if STAGE_BLOCK_HEADER in text:
        stages = _parse_stages(text)
        if 1 not in stages:
            mark = "x" if stage1_done else " "
            text = re.sub(
                rf"({re.escape(STAGE_BLOCK_HEADER)}\n)",
                rf"\1- [{mark}] 1. {STAGE_DEFS[1]}\n",
                text, count=1,
            )
        return text
    block_lines = [STAGE_BLOCK_HEADER]
    for i in range(1, 6):
        checked = "x" if (stage1_done and i == 1) else " "
        block_lines.append(f"- [{checked}] {i}. {STAGE_DEFS[i]}")
    block_str = "\n".join(block_lines) + "\n"
    m = re.search(r"^(## .*)$", text, re.MULTILINE)
    if m:
        return text[: m.end()] + "\n\n" + block_str + "\n" + text[m.end():]
    return block_str + "\n" + text


def _check_gate(path: Path, text: str, target_stage: int):
    """Check prerequisite stage is done. Exit 1 if not."""
    required = GATE_REQUIRES.get(target_stage)
    if required is None or required == 1:
        return
    stages = _parse_stages(text)
    if not stages.get(required):
        print(
            f"Gate check failed: stage {required} ({STAGE_DEFS[required]}) "
            f"not completed in {path}",
            file=sys.stderr,
        )
        sys.exit(1)


def _set_stage(text: str, stage: int, done: bool) -> str:
    """Set a stage checkbox to checked or unchecked."""
    text = _ensure_stage_block(text)
    stages = _parse_stages(text)
    if stages.get(stage) == done:
        return text
    mark = "x" if done else " "
    new_text = re.sub(
        rf"^( *- \[)(.)\] {stage}\. ",
        rf"\g<1>{mark}] {stage}. ",
        text, flags=re.MULTILINE,
    )
    if new_text == text:
        print(f"Warning: stage {stage} not found in stage block", file=sys.stderr)
    return new_text


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
        tmp = None
        content = sys.stdin.read()
    content = _ensure_stage_block(content, stage1_done=True)
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


def cmd_set_stage(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    _check_gate(path, text, args.stage)
    text = _set_stage(text, args.stage, done=True)
    path.write_text(text, encoding="utf-8")
    print(path)


def cmd_rollback(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    for stage in args.stages:
        text = _set_stage(text, stage, done=False)
    path.write_text(text, encoding="utf-8")
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

    p_batch = sub.add_parser("batch-update", help="Batch update sections for multiple Rn blocks from JSON or plain text")
    p_batch.add_argument("filename")
    p_batch.add_argument("payload", nargs="?", help='Payload (JSON list or plain text, or use --file)')
    p_batch.add_argument("--file", "-f", help="Path to a file containing the batch payload")
    p_batch.add_argument("--format", choices=["json", "plain"], default="json", help="Payload format (default: json)")
    p_batch.set_defaults(func=cmd_batch_update)

    p_set_stage = sub.add_parser("set-stage", help="Mark a review stage as completed (with gate check)")
    p_set_stage.add_argument("filename")
    p_set_stage.add_argument("stage", type=int, choices=[2, 3, 4, 5])
    p_set_stage.set_defaults(func=cmd_set_stage)

    p_rollback = sub.add_parser("rollback", help="Uncheck one or more review stages")
    p_rollback.add_argument("filename")
    p_rollback.add_argument("stages", type=int, nargs="+", choices=[2, 3, 4, 5])
    p_rollback.set_defaults(func=cmd_rollback)

    args = parser.parse_args()
    args.func(args)


def _parse_plain_payload(text: str) -> list[dict]:
    items = []
    blocks = re.split(r'(?m)^<<<END>>>\s*$', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        rn = None
        section = None
        content_lines = []
        state = "header"

        for line in lines:
            if state == "header":
                if line.startswith("Rn:"):
                    rn = line[3:].strip()
                elif line.startswith("Section:"):
                    section = line[8:].strip()
                elif line.startswith("Content:"):
                    state = "content"
                    remainder = line[8:].strip()
                    if remainder:
                        content_lines.append(remainder)
            else:
                content_lines.append(line)

        if rn and section:
            while content_lines and content_lines[0].strip() == "":
                content_lines.pop(0)
            while content_lines and content_lines[-1].strip() == "":
                content_lines.pop()
            items.append({
                "rn": rn,
                "section": section,
                "content": "\n".join(content_lines)
            })
    return items


def cmd_batch_update(args):
    path = get_path(args.filename)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    if args.file:
        tmp = Path(args.file)
        payload_text = tmp.read_text(encoding="utf-8")
    elif args.payload is not None:
        tmp = None
        payload_text = args.payload
    else:
        tmp = None
        payload_text = sys.stdin.read()

    if args.format == "plain":
        items = _parse_plain_payload(payload_text)
        if not items:
            print("Error: no valid items found in plain payload", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            items = json.loads(payload_text)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON payload: {e}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(items, list):
            print("JSON payload must be a list", file=sys.stderr)
            sys.exit(1)

    text = path.read_text(encoding="utf-8")

    # Gate check: verify prerequisite stage for each section type
    target_stages = set()
    for item in items:
        s = SECTION_TO_STAGE.get(item.get("section", ""))
        if s:
            target_stages.add(s)
    for stage in target_stages:
        _check_gate(path, text, stage)

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

    # Auto-mark target stages as done
    for stage in target_stages:
        text = _set_stage(text, stage, done=True)

    # Auto-rollback on special content patterns
    if 3 in target_stages:
        # Check if any Confirm contains 需补充回复
        if re.search(r"共识状态:\s*需补充回复", text):
            text = _set_stage(text, 2, done=False)
    if 5 in target_stages:
        # Check if any Accept contains 验收退回
        if re.search(r"验收结论:\s*验收退回", text):
            text = _set_stage(text, 4, done=False)
            text = _set_stage(text, 5, done=False)

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
