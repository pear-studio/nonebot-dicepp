"""
Task 2.5: Golden File 快照兼容测试

冲突守卫工作方式：
  1. 读取 tests/snapshots/cmd_<name>_compat.json 中的 cases
  2. 对每个 case，使用对应命令的 CommandTextParser 解析 input
  3. 将解析结果与 expected 对比
  4. 若不一致则 fail，阻断该迁移提交（确保行为等价）

新增命令快照的步骤：
  - 在 tests/snapshots/ 新建 cmd_<name>_compat.json
  - 在 SNAPSHOT_PARSERS 字典中注册对应的解析器
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

# 动态导入路径（conftest.py 已添加 DicePP 到 sys.path）
from core.command.text_parser import CommandTextParser
from core.command.parse_result import CommandParseResult

# ---------------------------------------------------------------------------
# 注册表：命令名 → 对应的 CommandTextParser 实例
# 迁移每个命令后，在此注册其解析器
# ---------------------------------------------------------------------------

# 命令名 → 解析器工厂（延迟创建，避免导入顺序问题）
SNAPSHOT_PARSER_FACTORIES: Dict[str, Any] = {
    "r": lambda: CommandTextParser(
        command_prefix="r",
        private_flags={"h", "s", "a", "n"},
    ),
    "mode": lambda: CommandTextParser(
        command_prefix="mode",
        strip_prefix_len=5,  # ".mode" 长度
    ),
}

# ---------------------------------------------------------------------------
# 快照目录
# ---------------------------------------------------------------------------
SNAPSHOTS_DIR = Path(__file__).parent.parent.parent / "snapshots"


def _load_snapshot(cmd_name: str) -> List[Dict]:
    """加载快照文件，返回 cases 列表"""
    path = SNAPSHOTS_DIR / f"cmd_{cmd_name}_compat.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def _normalize_result(result: CommandParseResult) -> Dict:
    """将 CommandParseResult 转为可与 expected 对比的字典。

    issues 仅比较 code 列表（排序），不比较 message 文本，
    这样修改错误描述措辞不会导致快照失败，但新增/删除错误类型会被捕获。
    kwargs 纳入断言，防止 --key=value 类解析退化漏检。
    """
    return {
        "command_name": result.command_name,
        "raw": result.raw,
        "flags": sorted(result.flags),
        "kwargs": result.kwargs,
        "args": result.args,
        "tail_text": result.tail_text,
        "issue_codes": sorted(i.code for i in result.issues),
    }


def _normalize_expected(expected: Dict) -> Dict:
    """规范化 expected（flags / issue_codes 排序）"""
    return {
        "command_name": expected.get("command_name", ""),
        "raw": expected.get("raw", ""),
        "flags": sorted(expected.get("flags", [])),
        "kwargs": expected.get("kwargs", {}),
        "args": expected.get("args", []),
        "tail_text": expected.get("tail_text", ""),
        "issue_codes": sorted(expected.get("issue_codes", [])),
    }


# ---------------------------------------------------------------------------
# 参数化测试：遍历所有已注册命令的快照
# ---------------------------------------------------------------------------

def _collect_snapshot_params():
    """收集所有已注册命令的快照 case 作为 pytest 参数"""
    params = []
    for cmd_name in SNAPSHOT_PARSER_FACTORIES:
        cases = _load_snapshot(cmd_name)
        for case in cases:
            params.append(pytest.param(
                cmd_name,
                case,
                id=f"{cmd_name}::{case.get('id', 'unknown')}",
            ))
    return params


@pytest.mark.parametrize("cmd_name,case", _collect_snapshot_params())
def test_snapshot_compat(cmd_name: str, case: Dict):
    """
    Golden File 冲突守卫：
    验证命令解析器的输出与快照文件中的 expected 完全一致。
    若不一致，说明迁移后行为发生了变化，需要人工审批并更新快照。
    """
    factory = SNAPSHOT_PARSER_FACTORIES.get(cmd_name)
    assert factory is not None, f"未找到命令 '{cmd_name}' 的解析器注册，请在 SNAPSHOT_PARSER_FACTORIES 中注册"

    parser: CommandTextParser = factory()
    input_str = case["input"]
    expected = _normalize_expected(case["expected"])

    result = parser.parse(input_str)

    # 遇到前缀不匹配的 fatal error，直接报告
    if result.has_errors:
        error_msgs = [i.message for i in result.issues if i.issue_type == "error"]
        pytest.fail(
            f"[{cmd_name}:{case.get('id')}] 解析发生致命错误：{error_msgs}\n"
            f"  input: {input_str!r}"
        )

    actual = _normalize_result(result)

    assert actual == expected, (
        f"\n[Golden File 冲突守卫] 命令 '{cmd_name}' case '{case.get('id')}' 解析结果与快照不符！\n"
        f"  input    : {input_str!r}\n"
        f"  actual   : {actual}\n"
        f"  expected : {expected}\n"
        f"\n若此变更为有意行为，请：\n"
        f"  1. 更新 tests/snapshots/cmd_{cmd_name}_compat.json 中对应 case 的 expected\n"
        f"  2. 在 PR 描述中记录：旧行为 / 新行为 / 影响范围 / 确认人"
    )
