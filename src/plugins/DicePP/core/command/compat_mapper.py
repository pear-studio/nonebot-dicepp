"""
CompatMapper — 历史命令兼容映射层 (Task 2.4)

职责：
  - 为每个命令注册"旧参数写法 → 统一语义"的映射规则
  - 在 CommandParseResult 构建完成后执行映射，保持历史输入可用
  - 若检测到不可兼容冲突，记录到 issues 并由 Task 2.5 的冲突守卫处理

设计原则：
  - 兼容映射仅转换语义，不改变用户可见行为
  - 每个命令的私有映射规则通过 CommandCompatMapper.register() 注册
  - 全局映射规则（如长短参数别名）在 GLOBAL_COMPAT_RULES 中维护
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from core.command.parse_result import CommandParseResult
from core.command.const import PARSE_WARN_COMPAT_CONFLICT, PARSE_WARN_COMPAT_RULE_ERROR


# ---------------------------------------------------------------------------
# 兼容规则数据类
# ---------------------------------------------------------------------------

@dataclass
class CompatRule:
    """单条兼容映射规则"""

    #: 规则描述（用于冲突守卫日志）
    description: str

    #: 映射函数：接收 CommandParseResult，原地修改，返回是否发生了转换
    apply: Callable[[CommandParseResult], bool]

    #: 是否为全局规则（True = 对所有命令生效）
    is_global: bool = False

    #: 若发生映射后会产生不可兼容冲突，设为 True 并填写 conflict_code
    may_conflict: bool = False
    conflict_code: str = ""


# ---------------------------------------------------------------------------
# 全局兼容规则（所有命令共享）
# ---------------------------------------------------------------------------
# 注意：--quiet → q 的映射已由 CommandTextParser 在词法层通过 GLOBAL_FLAG_TABLE
# 的 long 别名机制直接处理，result.flags 中不会出现 "quiet"，无需兼容规则。

GLOBAL_COMPAT_RULES: List[CompatRule] = []


# ---------------------------------------------------------------------------
# 命令兼容映射器
# ---------------------------------------------------------------------------

class CommandCompatMapper:
    """
    管理命令级兼容映射规则，并在解析结果上执行规则。

    用法::

        mapper = CommandCompatMapper("r")
        mapper.register(CompatRule(
            description="旧式 .rh → flags.add('h')",
            apply=lambda r: ...
        ))
        mapper.apply(parse_result)

    """

    # 全局单例注册表（命令名 → 规则列表）
    _registry: Dict[str, "CommandCompatMapper"] = {}

    def __init__(self, command_name: str):
        self.command_name = command_name
        self._rules: List[CompatRule] = []

    @classmethod
    def get_or_create(cls, command_name: str) -> "CommandCompatMapper":
        """获取或创建命令的兼容映射器实例（单例）"""
        if command_name not in cls._registry:
            cls._registry[command_name] = cls(command_name)
        return cls._registry[command_name]

    def register(self, rule: CompatRule) -> None:
        """注册一条兼容规则"""
        self._rules.append(rule)

    def apply(self, result: CommandParseResult) -> None:
        """
        对 result 依次执行：全局规则 → 命令私有规则。
        映射过程中若触发 may_conflict，写入 issues 供冲突守卫检测。
        """
        # 全局规则
        for rule in GLOBAL_COMPAT_RULES:
            try:
                changed = rule.apply(result)
                if changed and rule.may_conflict:
                    result.add_warning(
                        rule.conflict_code or PARSE_WARN_COMPAT_CONFLICT,
                        f"[兼容映射冲突] {rule.description}",
                    )
            except Exception as e:
                result.add_warning(
                    PARSE_WARN_COMPAT_RULE_ERROR,
                    f"Global compat rule '{rule.description}' raised: {e}",
                )

        # 命令私有规则
        for rule in self._rules:
            try:
                changed = rule.apply(result)
                if changed and rule.may_conflict:
                    result.add_warning(
                        rule.conflict_code or PARSE_WARN_COMPAT_CONFLICT,
                        f"[兼容映射冲突] {rule.description}",
                    )
            except Exception as e:
                result.add_warning(
                    PARSE_WARN_COMPAT_RULE_ERROR,
                    f"Command '{self.command_name}' compat rule '{rule.description}' raised: {e}",
                )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def apply_compat(result: CommandParseResult) -> None:
    """
    对 result 执行对应命令的兼容映射（含全局规则）。
    如果该命令没有注册私有规则，仅执行全局规则。
    """
    mapper = CommandCompatMapper._registry.get(result.command_name)
    if mapper:
        mapper.apply(result)
    else:
        # 仅执行全局规则
        _apply_global_rules(result)


def _apply_global_rules(result: CommandParseResult) -> None:
    """仅执行全局兼容规则"""
    for rule in GLOBAL_COMPAT_RULES:
        try:
            rule.apply(result)
        except Exception as e:
            result.add_warning(
                PARSE_WARN_COMPAT_RULE_ERROR,
                f"Global compat rule '{rule.description}' raised: {e}",
            )
