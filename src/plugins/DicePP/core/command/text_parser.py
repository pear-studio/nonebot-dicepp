"""
CommandTextParser — 通用文本解析器 (Task 2.2)

职责（词法层 + 语义层）：
  1. 剥离命令前缀，提取 command_name
  2. 按空白切分生成 tokens（词法层）
  3. 识别全局 flags（-q / --quiet / --help）和 kwargs（--key=value）
  4. 识别命令适配层声明的私有 flags（支持从 token 开头剥离连续私有 flag 字符）
  5. 切分 tail_text（尾部自由文本，默认第一个空格后全为 tail_text）
  6. 剩余为 args（语义层位置参数）

不涉及数据库、上下文读取（纯函数，可单元测试）。
CQ 码 / AT 提及的抽取由 Task 2.3 的 CqExtractor 负责。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from core.command.parse_result import CommandParseResult, ParseIssue
from core.command.const import (
    GLOBAL_FLAG_TABLE, GLOBAL_KWARG_TABLE,
    PARSE_ERR_PREFIX_MISMATCH, PARSE_WARN_KWARG_MISSING_VALUE,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 长参数前缀模式：--key=value 或 --flag
_LONG_ARG_RE = re.compile(r'^--([a-zA-Z][\w-]*)(?:=(.*))?$')

# 短参数前缀模式：-q
_SHORT_ARG_RE = re.compile(r'^-([a-zA-Z])$')


class CommandTextParser:
    """
    通用命令文本解析器。

    用法示例::

        parser = CommandTextParser(
            command_prefix="r",
            private_flags={"h", "s", "a", "n"},
        )
        result = parser.parse(".rhd20 攻击地精")
        # result.command_name == "r"
        # result.flags == {"h"}
        # result.args == ["d20"]
        # result.tail_text == "攻击地精"

    私有 flag 规则：
        - 支持从 token 开头剥离连续私有 flag 字符（如 "hsd20" → flags={"h","s"}, arg="d20"）
        - 第一个非 flag 字符起视为 arg
    """

    def __init__(
        self,
        command_prefix: str,
        *,
        private_flags: Optional[Set[str]] = None,
        tail_separator: Optional[str] = None,
        strip_prefix_len: Optional[int] = None,
    ):
        """
        Args:
            command_prefix:
                命令名（不含 "."），如 "r" / "mode" / "help"。
            private_flags:
                该命令的私有单字符 flag 集合（命令适配层注册，不进全局命名表）。
                例如 roll 命令的 {"h", "s", "a", "n"}。
                解析时从每个 token 的开头剥离连续 flag 字符，余下部分作为 arg。
            tail_separator:
                尾部自由文本分隔标记。默认（None）以第一个空格为界：
                第一个 token 为 main，之后全为 tail_text，与 DicePP 历史行为一致。
            strip_prefix_len:
                手动指定前缀长度（含 "."），通常不需要设置，解析器自动计算。
        """
        self.command_prefix = command_prefix
        self.private_flags: Set[str] = private_flags or set()
        self.tail_separator = tail_separator
        self._prefix_len = strip_prefix_len  # None 时自动计算

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def parse(self, msg_str: str) -> CommandParseResult:
        """
        解析完整的消息字符串（含前缀 "."），返回 CommandParseResult。

        流程：
          1. 剥离命令前缀 → raw
          2. 空白切分 → tokens
          3. 分离 flags / kwargs / args / tail_text
        """
        result = CommandParseResult(command_name=self.command_prefix)

        # ── 1. 剥离前缀 ─────────────────────────────────────────────────
        prefix_len = self._get_prefix_len(msg_str)
        if prefix_len < 0:
            result.add_error(
                PARSE_ERR_PREFIX_MISMATCH,
                f"Expected prefix '.{self.command_prefix}', got: {msg_str!r}",
                recoverable=False,
            )
            return result

        result.raw = msg_str[prefix_len:]

        # ── 2. 词法层：空白切分 → tokens ─────────────────────────────────
        result.tokens = result.raw.split() if result.raw.strip() else []

        # ── 3. 语义层：识别 flags / kwargs / tail_text / args ─────────────
        self._extract_semantic(result)

        return result

    def parse_body(self, body: str, command_name: str = "") -> CommandParseResult:
        """
        仅解析命令体（已剥离前缀后的部分），主要用于命令适配层直接提供 hint。

        Args:
            body: 已剥离前缀的命令体字符串
            command_name: 命令名称（可选，默认使用 self.command_prefix）
        """
        result = CommandParseResult(
            command_name=command_name or self.command_prefix,
            raw=body,
        )
        result.tokens = body.split() if body.strip() else []
        self._extract_semantic(result)
        return result

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_prefix_len(self, msg_str: str) -> int:
        """计算并返回前缀长度（含 "."），返回 -1 表示不匹配。"""
        expected = f".{self.command_prefix}"
        if self._prefix_len is not None:
            return self._prefix_len if msg_str.startswith(expected) else -1
        return len(expected) if msg_str.startswith(expected) else -1

    def _extract_semantic(self, result: CommandParseResult) -> None:
        """
        从 result.raw 中提取语义层字段。

        tail_text 切分策略：
          - 有 tail_separator → 以分隔符为界
          - 无（默认）→ 以第一个空格为界：第一个 token 为主参数区，之后为 tail_text
        私有 flag 策略：
          - 从每个主参数 token 的开头剥离连续 flag 字符（如 "hsd20" → flags=h,s, arg="d20"）
        """
        text = result.raw.strip()
        if not text:
            return

        # ── 切出 tail_text ────────────────────────────────────────────────
        main_part, tail_part = self._split_tail(text)
        result.tail_text = tail_part

        # ── 解析 main_part 中的 flags / kwargs / args ─────────────────────
        remaining_args: List[str] = []
        for i, token in enumerate(main_part.split()):
            # 全局长/短参数（--flag / --key=value / -q）
            if token.startswith("-"):
                consumed = self._try_consume_global_option(token, result, i)
                if consumed:
                    continue

            # 私有 flag 前缀剥离（如 "hsd20" → flags={h,s}, rest="d20"）
            rest = self._strip_private_flags_prefix(token, result)
            if rest:
                remaining_args.append(rest)
            # 若 rest 为空（token 全为 flags），不追加 arg

        result.args = remaining_args

    def _split_tail(self, text: str) -> Tuple[str, str]:
        """切分 main_part 和 tail_text。"""
        if self.tail_separator and self.tail_separator in text:
            idx = text.index(self.tail_separator)
            return text[:idx].strip(), text[idx + len(self.tail_separator):].strip()

        # 默认：第一个空格分割（与 DicePP 历史行为一致）
        parts = text.split(" ", 1)
        if len(parts) == 1:
            return parts[0].strip(), ""
        return parts[0].strip(), parts[1].strip()

    def _try_consume_global_option(
        self, token: str, result: CommandParseResult, idx: int
    ) -> bool:
        """尝试识别全局 flag 或 kwarg（--flag / --key=value / -q），消费成功返回 True。

        long 参数的匹配规则（优先级依次）：
          1. key 直接命中 GLOBAL_FLAG_TABLE（如 --help → key="help" ✓）
          2. key 命中某条记录的 long 别名去掉 "--" 后的值（如 --quiet → key="quiet"
             → info["long"]="--quiet" → 规范 key="q" ✓）
          3. key 命中 GLOBAL_KWARG_TABLE（同上两步）
        短参数同样先查 flag_key 等于 short，再查 info["short"]。
        """
        m = _LONG_ARG_RE.match(token)
        if m:
            key = m.group(1).lower()
            value = m.group(2)

            # ── 全局 flag：直接 key 或 long 别名映射 ──────────────────────
            # 1. 直接命中规范 key（如 --help）
            if key in GLOBAL_FLAG_TABLE:
                result.flags.add(key)
                return True
            # 2. 通过 long 别名查找规范 key（如 --quiet → "q"）
            for flag_key, info in GLOBAL_FLAG_TABLE.items():
                long_alias = info.get("long", "")
                if long_alias and long_alias.lstrip("-").lower() == key:
                    result.flags.add(flag_key)
                    return True

            # ── 全局 kwarg：直接 key 或 long 别名映射 ─────────────────────
            kwarg_key = None
            if key in GLOBAL_KWARG_TABLE:
                kwarg_key = key
            else:
                for kw_key, info in GLOBAL_KWARG_TABLE.items():
                    long_alias = info.get("long", "")
                    if long_alias and long_alias.lstrip("-").lower() == key:
                        kwarg_key = kw_key
                        break

            if kwarg_key is not None:
                if value is not None:
                    result.kwargs[kwarg_key] = value
                else:
                    result.add_warning(
                        PARSE_WARN_KWARG_MISSING_VALUE,
                        f"Global kwarg '--{kwarg_key}' requires a value (--{kwarg_key}=VALUE).",
                        token_index=idx,
                    )
                return True
            return False

        m = _SHORT_ARG_RE.match(token)
        if m:
            short = m.group(1).lower()
            for flag_key, info in GLOBAL_FLAG_TABLE.items():
                if flag_key == short or info.get("short") == short:
                    result.flags.add(flag_key)
                    return True
        return False

    def _strip_private_flags_prefix(
        self, token: str, result: CommandParseResult
    ) -> str:
        """
        从 token 开头剥离连续私有 flag 字符，将识别到的 flags 写入 result.flags，
        返回剩余的字符串（作为 arg）。

        例如：
          token="hsd20", private_flags={"h","s"} → flags.add(h,s), return "d20"
          token="d20",   private_flags={"h","s"} → return "d20"
          token="h",     private_flags={"h","s"} → flags.add(h), return ""
        """
        if not self.private_flags:
            return token

        i = 0
        while i < len(token) and token[i] in self.private_flags:
            result.flags.add(token[i])
            i += 1

        return token[i:]