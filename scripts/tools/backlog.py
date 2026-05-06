#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 Backlog 管理工具。"""

import argparse
import hashlib
import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

BACKLOG_PATH = Path("docs/dev/backlog.md")

# ID pattern: B-yymmdd-hex6
ID_RE = re.compile(r"^B-\d{6}-[0-9a-f]{6}$")

# H3 entry header: ### [B-260506-a3f9c1] Title
ENTRY_HEADER_RE = re.compile(r"^### \[(B-\d{6}-[0-9a-f]{6})\] (.*)$")

# List item line under an entry: `- 字段: 值`
FIELD_RE = re.compile(r"^- (来源|创建|触发条件|原始问题|暂缓原因):\s*(.*)$")


@dataclass
class BacklogItem:
    id: str
    module: str
    title: str
    source: str = ""
    created: str = ""
    trigger: str = ""
    problem: str = ""
    reason: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id:
            errors.append("缺少 ID")
        elif not ID_RE.match(self.id):
            errors.append(f"ID 格式非法: {self.id}")
        if not self.module:
            errors.append("缺少 module")
        if not self.title:
            errors.append("缺少 title")
        if not self.trigger:
            errors.append("缺少触发条件")
        if not self.reason:
            errors.append("缺少暂缓原因")
        return errors

    def to_md(self) -> str:
        lines = [
            f"### [{self.id}] {self.title}",
            f"- 来源: {self.source}",
            f"- 创建: {self.created}",
            f"- 触发条件: {self.trigger}",
            f"- 原始问题: {self.problem}",
            f"- 暂缓原因: {self.reason}",
            "",
        ]
        return "\n".join(lines)


def _resolve_path(path: Path | str | None) -> Path:
    """Return backlog path. Relative paths are resolved against cwd."""
    if path is None:
        return BACKLOG_PATH
    return Path(path)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _gen_id(module: str, title: str, problem: str) -> str:
    """Generate a backlog ID. Collision is possible if the same module/title/problem
    is added within the same second; the duplicate guard in cmd_add handles this."""
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    payload = f"{module}:{title}:{problem}:{timestamp}"
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:6]
    date_part = timestamp[:6]  # yymmdd
    return f"B-{date_part}-{h}"


def parse_backlog(path: Path) -> tuple[list[str], dict[str, list[BacklogItem]]]:
    """Parse the backlog file. Returns (preamble lines, {module: [items]})."""
    if not path.exists():
        return [], {}

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    preamble: list[str] = []
    modules: dict[str, list[BacklogItem]] = {}

    state = "preamble"
    current_module = ""
    current_item: BacklogItem | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        if state == "preamble":
            preamble.append(stripped)
            if stripped == "---":
                state = "body"
            i += 1
            continue

        if stripped.startswith("## "):
            current_module = stripped[3:].strip()
            if current_module not in modules:
                modules[current_module] = []
            i += 1
            continue

        m = ENTRY_HEADER_RE.match(stripped)
        if m:
            if current_item is not None:
                modules[current_module].append(current_item)
            current_item = BacklogItem(
                id=m.group(1),
                module=current_module,
                title=m.group(2),
            )
            i += 1
            continue

        if current_item is not None:
            fm = FIELD_RE.match(stripped)
            if fm:
                key = fm.group(1)
                val = fm.group(2)
                if key == "来源":
                    current_item.source = val
                elif key == "创建":
                    current_item.created = val
                elif key == "触发条件":
                    current_item.trigger = val
                elif key == "原始问题":
                    current_item.problem = val
                elif key == "暂缓原因":
                    current_item.reason = val
            elif stripped == "" and (i + 1 >= len(lines) or not FIELD_RE.match(lines[i + 1].strip())):
                # blank line separating entries
                if current_item is not None:
                    modules[current_module].append(current_item)
                    current_item = None
        i += 1

    if current_item is not None:
        modules[current_module].append(current_item)

    return preamble, modules


def _write_backlog(path: Path, preamble: list[str], modules: dict[str, list[BacklogItem]], dry_run: bool = False) -> None:
    lines: list[str] = list(preamble)
    if not lines or lines[-1] != "---":
        lines.append("---")
    lines.append("")

    # Sort modules alphabetically for stable output
    for mod in sorted(modules.keys()):
        items = modules[mod]
        if not items:
            continue
        lines.append(f"## {mod}")
        lines.append("")
        for item in items:
            lines.append(item.to_md().rstrip("\n"))
        lines.append("")

    content = "\n".join(lines) + "\n"

    if dry_run:
        print("[dry-run] 将要写入的内容预览（前 30 行）：")
        for line in content.splitlines()[:30]:
            print(line)
        if len(content.splitlines()) > 30:
            print("...")
        return

    # Atomic write via temp file
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="backlog-tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def _resolve_module_order(existing: dict[str, list[BacklogItem]], items: list[BacklogItem]) -> dict[str, list[BacklogItem]]:
    """Merge new items into existing module buckets."""
    merged: dict[str, list[BacklogItem]] = {}
    for mod, itms in existing.items():
        merged[mod] = list(itms)
    for item in items:
        merged.setdefault(item.module, []).append(item)
    return merged


def cmd_add(args):
    path = _resolve_path(args.file)
    item = BacklogItem(
        id=_gen_id(args.module, args.title, args.problem or ""),
        module=args.module,
        title=args.title,
        source=args.source,
        created=_today(),
        trigger=args.trigger,
        problem=args.problem,
        reason=args.reason,
    )
    errors = item.validate()
    if errors:
        print(f"校验失败: {'; '.join(errors)}", file=sys.stderr)
        sys.exit(1)

    preamble, modules = parse_backlog(path)
    # Deduplicate by ID (shouldn't happen with hash, but guard anyway)
    all_ids = {it.id for lst in modules.values() for it in lst}
    if item.id in all_ids:
        print(f"ID 已存在: {item.id}", file=sys.stderr)
        sys.exit(1)

    modules = _resolve_module_order(modules, [item])
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)
    print(item.id)


def _parse_batch_payload(text: str) -> list[dict[str, str]]:
    """Parse plain text batch payload separated by <<<END>>>."""
    items: list[dict[str, str]] = []
    blocks = re.split(r"(?m)^<<<END>>>\s*$", text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        item: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("Module:"):
                key = "module"
                item["module"] = stripped[7:].strip()
            elif stripped.startswith("Title:"):
                key = "title"
                item["title"] = stripped[6:].strip()
            elif stripped.startswith("Source:"):
                key = "source"
                item["source"] = stripped[7:].strip()
            elif stripped.startswith("Problem:"):
                key = "problem"
                item["problem"] = stripped[8:].strip()
            elif stripped.startswith("Trigger:"):
                key = "trigger"
                item["trigger"] = stripped[8:].strip()
            elif stripped.startswith("Reason:"):
                key = "reason"
                item["reason"] = stripped[7:].strip()
            elif key and not stripped.startswith("---"):
                # Continuation of previous field
                item[key] = item.get(key, "") + "\n" + line
        for k in item:
            item[k] = item[k].strip()
        items.append(item)
    return items


def cmd_batch_add(args):
    path = _resolve_path(args.file)

    if args.payload_file:
        if args.payload_file == "-":
            payload_text = sys.stdin.read()
        else:
            payload_text = Path(args.payload_file).read_text(encoding="utf-8")
    elif args.payload:
        payload_text = args.payload
    else:
        payload_text = sys.stdin.read()

    raw_items = _parse_batch_payload(payload_text)
    if not raw_items:
        print("没有解析到有效条目", file=sys.stderr)
        sys.exit(1)

    preamble, modules = parse_backlog(path)
    existing_ids = {it.id for lst in modules.values() for it in lst}

    new_items: list[BacklogItem] = []
    for idx, raw in enumerate(raw_items, start=1):
        item = BacklogItem(
            id=_gen_id(
                raw.get("module", ""),
                raw.get("title", ""),
                raw.get("problem", ""),
            ),
            module=raw.get("module", ""),
            title=raw.get("title", ""),
            source=raw.get("source", ""),
            created=_today(),
            trigger=raw.get("trigger", ""),
            problem=raw.get("problem", ""),
            reason=raw.get("reason", ""),
        )
        errors = item.validate()
        if errors:
            print(f"第 {idx} 条校验失败: {'; '.join(errors)}", file=sys.stderr)
            sys.exit(1)
        if item.id in existing_ids:
            print(f"第 {idx} 条 ID 已存在: {item.id}", file=sys.stderr)
            sys.exit(1)
        existing_ids.add(item.id)
        new_items.append(item)

    modules = _resolve_module_order(modules, new_items)
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)

    for item in new_items:
        print(item.id)


def cmd_list(args):
    path = _resolve_path(args.file)
    preamble, modules = parse_backlog(path)
    items: list[BacklogItem] = []
    for mod, itms in modules.items():
        if args.module and mod != args.module:
            continue
        items.extend(itms)
    if not items:
        print("无 backlog 项")
        return
    for item in items:
        print(f"[{item.id}] {item.module} — {item.title}")


def cmd_show(args):
    path = _resolve_path(args.file)
    _, modules = parse_backlog(path)
    for lst in modules.values():
        for item in lst:
            if item.id == args.id:
                print(f"ID:       {item.id}")
                print(f"模块:     {item.module}")
                print(f"标题:     {item.title}")
                print(f"来源:     {item.source}")
                print(f"创建:     {item.created}")
                print(f"触发条件: {item.trigger}")
                print(f"原始问题: {item.problem}")
                print(f"暂缓原因: {item.reason}")
                return
    print(f"未找到: {args.id}", file=sys.stderr)
    sys.exit(1)


def cmd_close(args):
    path = _resolve_path(args.file)
    preamble, modules = parse_backlog(path)
    removed = False
    for mod in list(modules):
        before = len(modules[mod])
        modules[mod] = [it for it in modules[mod] if it.id != args.id]
        if len(modules[mod]) < before:
            removed = True
        if not modules[mod]:
            del modules[mod]
    if not removed:
        print(f"未找到: {args.id}", file=sys.stderr)
        sys.exit(1)
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)
    print(args.id)


def cmd_prune(args):
    path = _resolve_path(args.file)
    preamble, modules = parse_backlog(path)
    removed_any = False
    for mod in list(modules):
        before = len(modules[mod])
        modules[mod] = [it for it in modules[mod] if it.id not in args.ids]
        removed = before - len(modules[mod])
        if removed:
            removed_any = True
            print(f"从 {mod} 移除 {removed} 条")
        if not modules[mod]:
            del modules[mod]
    if not removed_any:
        print("没有匹配的条目被移除")
    else:
        _write_backlog(path, preamble, modules, dry_run=args.dry_run)


def cmd_sort(args):
    path = _resolve_path(args.file)
    preamble, modules = parse_backlog(path)
    for mod in modules:
        # Sort by ID, which embeds date then hash
        modules[mod].sort(key=lambda it: it.id)
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)
    print("已按 ID 排序")


def cmd_validate(args):
    path = _resolve_path(args.file)
    preamble, modules = parse_backlog(path)
    errors: list[str] = []
    all_ids: set[str] = set()

    for mod, itms in modules.items():
        for idx, item in enumerate(itms, start=1):
            if not item.id:
                errors.append(f"模块 {mod} 第 {idx} 条: 缺少 ID")
                continue
            if not ID_RE.match(item.id):
                errors.append(f"模块 {mod} 第 {idx} 条: ID 格式非法 '{item.id}'")
            if item.id in all_ids:
                errors.append(f"重复 ID: {item.id}")
            all_ids.add(item.id)
            if not item.title:
                errors.append(f"[{item.id}] 缺少标题")
            if not item.trigger:
                errors.append(f"[{item.id}] 缺少触发条件")
            if not item.reason:
                errors.append(f"[{item.id}] 缺少暂缓原因")

    if not errors:
        total = sum(len(v) for v in modules.values())
        print(f"校验通过。共 {total} 条 backlog，{len(modules)} 个模块。")
        return

    print("校验失败:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="通用 Backlog 管理工具")
    parser.add_argument("--file", "-f", help="backlog 文件路径 (默认 docs/dev/backlog.md)")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="新增单条 backlog")
    p_add.add_argument("--module", "-m", required=True)
    p_add.add_argument("--title", "-t", required=True)
    p_add.add_argument("--source", "-s", default="")
    p_add.add_argument("--problem", default="")
    p_add.add_argument("--trigger", required=True)
    p_add.add_argument("--reason", "-r", required=True)
    p_add.set_defaults(func=cmd_add)

    p_batch = sub.add_parser("batch-add", help="批量新增 backlog")
    p_batch.add_argument("payload", nargs="?", help="payload 文本（或从 stdin/文件读取）")
    p_batch.add_argument("--payload-file", help="从文件读取 payload")
    p_batch.set_defaults(func=cmd_batch_add)

    p_list = sub.add_parser("list", help="列出 backlog")
    p_list.add_argument("--module", help="按模块过滤")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="显示单条 backlog")
    p_show.add_argument("id", help="backlog ID")
    p_show.set_defaults(func=cmd_show)

    p_close = sub.add_parser("close", help="删除单条 backlog")
    p_close.add_argument("id", help="backlog ID")
    p_close.set_defaults(func=cmd_close)

    p_prune = sub.add_parser("prune", help="批量删除 backlog")
    p_prune.add_argument("ids", nargs="+", help="一个或多个 backlog ID")
    p_prune.set_defaults(func=cmd_prune)

    p_sort = sub.add_parser("sort", help="按 ID 重排序")
    p_sort.set_defaults(func=cmd_sort)

    p_val = sub.add_parser("validate", help="校验 backlog 文件格式")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
