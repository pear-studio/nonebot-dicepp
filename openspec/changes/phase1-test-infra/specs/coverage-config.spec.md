# Spec: 覆盖率配置规格

## 概述

定义 pytest-cov 和 coverage.py 的配置，确保覆盖率报告准确反映核心代码的测试情况。

## 配置文件

配置文件位置: 工程根目录 `.coveragerc`

## 必须配置项

### SPEC-P1-010: source 配置

- **路径**: `src/plugins/DicePP`
- **原因**: 只统计核心业务代码的覆盖率
- **验收标准**: 覆盖率报告只包含 DicePP 目录下的源文件

### SPEC-P1-011: omit 配置

应排除以下文件：

| 模式 | 原因 |
|------|------|
| `*/unit_test.py` | 测试文件本身不需要覆盖率 |
| `*/test_*.py` | 新测试文件 |
| `*/*_test.py` | 新测试文件 |
| `*/template_cmd.py` | 模板文件，不包含业务逻辑 |
| `*/conftest.py` | pytest 配置文件 |

### SPEC-P1-012: show_missing

- **值**: `true`
- **原因**: 显示未覆盖的行号，便于定位测试盲区
- **验收标准**: 报告中显示 `Missing` 列

### SPEC-P1-013: exclude_lines

应排除以下代码行：

```ini
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
    raise NotImplementedError
    if __name__ == .__main__.:
    @abc.abstractmethod
```

## 完整配置文件

```ini
[run]
source = src/plugins/DicePP
omit =
    */unit_test.py
    */test_*.py
    */*_test.py
    */template_cmd.py
    */conftest.py

[report]
show_missing = true
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
    raise NotImplementedError
    if __name__ == .__main__.:
    @abc.abstractmethod

[html]
directory = htmlcov
```

## 验证命令

```bash
# 生成覆盖率报告
pytest --cov --cov-report=term-missing

# 生成 HTML 报告
pytest --cov --cov-report=html

# 验收标准：
# 1. 输出中不包含 unit_test.py 等被排除的文件
# 2. 显示每个文件的 Missing 行号
# 3. htmlcov/ 目录生成 HTML 报告
```

## 覆盖率目标

| 模块 | 目标覆盖率 |
|------|------------|
| `core/` | >= 60% |
| `module/roll/` | >= 70% |
| `module/common/` | >= 50% |
| `module/character/` | >= 40% |
| 总体 | >= 50% |

注：这些目标将在 Phase 3 完成后评估。
