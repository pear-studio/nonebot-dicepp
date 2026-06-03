#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 Backlog 管理工具。

字段说明：
    创建      自动填入的录入日期
    问题表现  详细症状、现场数据、错误日志、量化指标、复现路径
    开发备忘  历史背景、相关线索、可能的修复方向（仅供参考，agent 应独立诊断，
              允许推翻）

问题表现 / 开发备忘支持单行或多行写法：
    - 问题表现: 简短描述
    或
    - 问题表现:
      - 现象 1
      - 现象 2

向后兼容：旧条目中的「工作计划」字段仍可正确解析，写入时统一转为「开发备忘」。
"""

import argparse
import hashlib
import io
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

BACKLOG_PATH = Path("docs/dev/backlog.md")

ID_RE = re.compile(r"^B-\d{6}-[0-9a-f]{6}$")
ENTRY_HEADER_RE = re.compile(r"^### \[(B-\d{6}-[0-9a-f]{6})\] (.*)$")
FIELD_RE = re.compile(r"^- (创建|优先级|类型|改动量|问题表现|开发备忘|工作计划):\s*(.*)$")
INDENT_RE = re.compile(r"^  (.*)$")  # 2-space indent → 字段多行内容

FIELD_KEYS = ("创建", "优先级", "类型", "改动量", "问题表现", "开发备忘")

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
TYPE_ORDER = {"bug": 0, "feature": 1, "refactor": 2}
EFFORT_ORDER = {"S": 0, "M": 1, "L": 2, "XL": 3}

VALID_PRIORITIES = frozenset(PRIORITY_ORDER)
VALID_TYPES = frozenset(TYPE_ORDER)
VALID_EFFORTS = frozenset(EFFORT_ORDER)


@dataclass
class BacklogItem:
    id: str
    module: str
    title: str
    priority: str = ""
    type: str = ""
    effort: str = ""
    created: str = ""
    symptom: str = ""
    plan: str = ""

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        return (
            PRIORITY_ORDER.get(self.priority, 99),
            TYPE_ORDER.get(self.type, 99),
            EFFORT_ORDER.get(self.effort, 99),
            self.id,
        )

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
        if not self.priority:
            errors.append("缺少优先级")
        elif self.priority not in VALID_PRIORITIES:
            errors.append(f"优先级非法: {self.priority}（允许: P0/P1/P2）")
        if not self.type:
            errors.append("缺少类型")
        elif self.type not in VALID_TYPES:
            errors.append(f"类型非法: {self.type}（允许: bug/feature/refactor）")
        if not self.effort:
            errors.append("缺少改动量")
        elif self.effort not in VALID_EFFORTS:
            errors.append(f"改动量非法: {self.effort}（允许: S/M/L/XL）")
        if not self.symptom:
            errors.append("缺少问题表现")
        if not self.plan:
            errors.append("缺少开发备忘")
        return errors

    def to_md(self) -> str:
        lines = [f"### [{self.id}] {self.title}"]
        lines.append(f"- 创建: {self.created}")
        lines.append(f"- 优先级: {self.priority}")
        lines.append(f"- 类型: {self.type}")
        lines.append(f"- 改动量: {self.effort}")
        lines.append(_render_field("问题表现", self.symptom))
        lines.append(_render_field("开发备忘", self.plan))
        lines.append("")
        return "\n".join(lines)


def _render_field(label: str, value: str) -> str:
    """单行 → `- label: value`；多行 → `- label:` 后跟缩进块。"""
    value = value.rstrip()
    if not value:
        return f"- {label}:"
    if "\n" not in value:
        return f"- {label}: {value}"
    body_lines = ["  " + line if line.strip() else "" for line in value.splitlines()]
    return f"- {label}:\n" + "\n".join(body_lines)


def _resolve_path(path: Path | str | None) -> Path:
    if path is None:
        return BACKLOG_PATH
    return Path(path)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _gen_id(module: str, title: str, symptom: str) -> str:
    """同毫秒内同 module/title/symptom 才会撞 ID，cmd_add 内还有兜底校验。"""
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    payload = f"{module}:{title}:{symptom}:{timestamp}"
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:6]
    date_part = timestamp[:6]
    return f"B-{date_part}-{h}"


def parse_backlog(path: Path) -> tuple[list[str], dict[str, list[BacklogItem]]]:
    if not path.exists():
        return [], {}

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    preamble: list[str] = []
    modules: dict[str, list[BacklogItem]] = {}

    state = "preamble"
    current_module = ""
    current_item: BacklogItem | None = None
    current_field: str | None = None  # 正在累积多行 body 的字段名
    field_buffer: list[str] = []

    def flush_field() -> None:
        nonlocal current_field, field_buffer
        if current_item is None or current_field is None:
            current_field = None
            field_buffer = []
            return
        body = "\n".join(field_buffer).rstrip()
        if current_field == "问题表现":
            current_item.symptom = body
        elif current_field in ("开发备忘", "工作计划"):
            current_item.plan = body
        elif current_field == "优先级":
            current_item.priority = body
        elif current_field == "类型":
            current_item.type = body
        elif current_field == "改动量":
            current_item.effort = body
        current_field = None
        field_buffer = []

    def flush_item() -> None:
        nonlocal current_item
        flush_field()
        if current_item is not None:
            modules.setdefault(current_module, []).append(current_item)
            current_item = None

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
            flush_item()
            current_module = stripped[3:].strip()
            modules.setdefault(current_module, [])
            i += 1
            continue

        m = ENTRY_HEADER_RE.match(stripped)
        if m:
            flush_item()
            current_item = BacklogItem(
                id=m.group(1),
                module=current_module,
                title=m.group(2),
            )
            i += 1
            continue

        if current_item is None:
            i += 1
            continue

        fm = FIELD_RE.match(stripped)
        if fm:
            flush_field()
            key = fm.group(1)
            val = fm.group(2)
            if key == "创建":
                current_item.created = val
            elif key == "优先级":
                current_item.priority = val
            elif key == "类型":
                current_item.type = val
            elif key == "改动量":
                current_item.effort = val
            elif key in ("问题表现", "开发备忘", "工作计划"):
                if val:
                    if key == "问题表现":
                        current_item.symptom = val
                    else:
                        current_item.plan = val
                else:
                    current_field = key
                    field_buffer = []
            i += 1
            continue

        if current_field is not None:
            im = INDENT_RE.match(line)  # 用原始 line 保留缩进右侧空格无关紧要
            if im:
                field_buffer.append(im.group(1))
                i += 1
                continue
            if stripped == "":
                # 空行可能是字段 body 的一部分；只有当下一行不再缩进时才结束
                if i + 1 < len(lines) and INDENT_RE.match(lines[i + 1]):
                    field_buffer.append("")
                    i += 1
                    continue
            flush_field()
        i += 1

    flush_item()

    return preamble, modules


def _write_backlog(path: Path, preamble: list[str], modules: dict[str, list[BacklogItem]], dry_run: bool = False) -> None:
    lines: list[str] = list(preamble)
    if not lines or lines[-1] != "---":
        lines.append("---")
    lines.append("")

    for mod in sorted(modules.keys()):
        items = sorted(modules[mod], key=lambda it: it.sort_key)
        if not items:
            continue
        lines.append(f"## {mod}")
        lines.append("")
        for idx, item in enumerate(items):
            if idx > 0:
                lines.append("")
            lines.append(item.to_md().rstrip("\n"))
        lines.append("")

    content = "\n".join(lines) + "\n"

    if dry_run:
        print("[dry-run] 将要写入的内容预览（前 40 行）：")
        for line in content.splitlines()[:40]:
            print(line)
        if len(content.splitlines()) > 40:
            print("...")
        return

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="backlog-tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise


def _resolve_module_order(existing: dict[str, list[BacklogItem]], items: list[BacklogItem]) -> dict[str, list[BacklogItem]]:
    merged: dict[str, list[BacklogItem]] = {mod: list(itms) for mod, itms in existing.items()}
    for item in items:
        merged.setdefault(item.module, []).append(item)
    return merged


def cmd_add(args):
    path = _resolve_path(args.file)
    item = BacklogItem(
        id=args.id or _gen_id(args.module, args.title, args.symptom or ""),
        module=args.module,
        title=args.title,
        priority=args.priority,
        type=args.type,
        effort=args.effort,
        created=_today(),
        symptom=args.symptom,
        plan=args.plan,
    )
    errors = item.validate()
    if errors:
        print(f"校验失败: {'; '.join(errors)}", file=sys.stderr)
        sys.exit(1)

    preamble, modules = parse_backlog(path)
    all_ids = {it.id for lst in modules.values() for it in lst}
    if item.id in all_ids:
        print(f"ID 已存在: {item.id}", file=sys.stderr)
        sys.exit(1)

    modules = _resolve_module_order(modules, [item])
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)
    print(item.id)


def _parse_batch_payload(text: str) -> list[dict[str, str]]:
    """以 <<<END>>> 分隔多个条目。

    每个条目内部支持 ``Key: value`` 单行写法，和 ``Key:`` 后跟若干续行的多行写法：
        Symptom:
        - 现象 1
        - 现象 2
    续行直到下一个识别到的 Key 行或条目分隔符为止。
    """
    label_to_key = {
        "Module": "module",
        "Title": "title",
        "Priority": "priority",
        "Type": "type",
        "Effort": "effort",
        "Symptom": "symptom",
        "Plan": "plan",
    }

    entries: list[dict[str, str]] = []
    blocks = re.split(r"(?m)^<<<END>>>\s*$", text)

    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        item: dict[str, list[str]] = {}
        current_key: str | None = None
        for raw_line in block.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            label_match = None
            for label in label_to_key:
                if stripped.startswith(f"{label}:"):
                    rest = stripped[len(label) + 1 :]
                    label_match = (label_to_key[label], rest.lstrip())
                    break
            if label_match:
                current_key, rest = label_match
                item.setdefault(current_key, [])
                if rest:
                    item[current_key].append(rest)
            elif current_key:
                item[current_key].append(line)
        cleaned = {k: "\n".join(v).strip("\n") for k, v in item.items()}
        if any(cleaned.values()):
            entries.append(cleaned)
    return entries


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
                raw.get("symptom", ""),
            ),
            module=raw.get("module", ""),
            title=raw.get("title", ""),
            priority=raw.get("priority", ""),
            type=raw.get("type", ""),
            effort=raw.get("effort", ""),
            created=_today(),
            symptom=raw.get("symptom", ""),
            plan=raw.get("plan", ""),
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
    _, modules = parse_backlog(path)
    items: list[BacklogItem] = []
    for mod, itms in modules.items():
        if args.module and mod != args.module:
            continue
        items.extend(itms)
    if not items:
        print("无 backlog 项")
        return
    for item in items:
        print(f"[{item.id}] {item.module} {item.priority} {item.type} {item.effort} — {item.title}")


def cmd_show(args):
    path = _resolve_path(args.file)
    _, modules = parse_backlog(path)
    for lst in modules.values():
        for item in lst:
            if item.id == args.id:
                print(f"ID:       {item.id}")
                print(f"模块:     {item.module}")
                print(f"标题:     {item.title}")
                print(f"创建:     {item.created}")
                print(f"优先级:   {item.priority}")
                print(f"类型:     {item.type}")
                print(f"改动量:   {item.effort}")
                print("问题表现:")
                for line in item.symptom.splitlines() or [""]:
                    print(f"  {line}")
                print("开发备忘:")
                for line in item.plan.splitlines() or [""]:
                    print(f"  {line}")
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
        modules[mod].sort(key=lambda it: it.sort_key)
    _write_backlog(path, preamble, modules, dry_run=args.dry_run)
    print("已按 优先级→类型→改动量 排序")


def cmd_validate(args):
    path = _resolve_path(args.file)
    _, modules = parse_backlog(path)
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
            if not item.priority:
                errors.append(f"[{item.id}] 缺少优先级")
            elif item.priority not in VALID_PRIORITIES:
                errors.append(f"[{item.id}] 优先级非法: {item.priority}")
            if not item.type:
                errors.append(f"[{item.id}] 缺少类型")
            elif item.type not in VALID_TYPES:
                errors.append(f"[{item.id}] 类型非法: {item.type}")
            if not item.effort:
                errors.append(f"[{item.id}] 缺少改动量")
            elif item.effort not in VALID_EFFORTS:
                errors.append(f"[{item.id}] 改动量非法: {item.effort}")
            if not item.symptom:
                errors.append(f"[{item.id}] 缺少问题表现")
            if not item.plan:
                errors.append(f"[{item.id}] 缺少开发备忘")

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
    p_add.add_argument("--priority", "-p", required=True, choices=["P0", "P1", "P2"])
    p_add.add_argument("--type", required=True, choices=["bug", "feature", "refactor"])
    p_add.add_argument("--effort", "-e", required=True, choices=["S", "M", "L", "XL"])
    p_add.add_argument("--symptom", required=True, help="问题表现（必填，可含换行）")
    p_add.add_argument("--plan", required=True, help="开发备忘（必填，可含换行；历史背景、相关线索、可能方向，agent 应独立判断）")
    p_add.add_argument("--id", help="手动指定 ID（默认自动生成）")
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
