# Spec: sys.path 解决方案规格

## 概述

定义如何解决测试文件中相对导入依赖工作目录的问题，使 pytest 能从工程根目录发现并运行测试。

## 问题描述

现有测试文件使用如下导入：

```python
from core.bot import Bot
from module.roll.expression import parse_expression
```

这种导入方式要求 `sys.path` 中包含 `src/plugins/DicePP` 目录。

## 解决方案

使用两级 `conftest.py` 文件注入路径。

### SPEC-P1-020: 根目录 conftest.py

**文件位置**: `conftest.py`（工程根目录）

**功能**:
1. 将 `src` 目录加入 `sys.path`
2. 标记 pytest 根目录

**接口规格**:

```python
import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
```

**验收标准**:
- pytest 从根目录运行时，能正确导入 `plugins.DicePP` 模块

### SPEC-P1-021: DicePP 目录 conftest.py

**文件位置**: `src/plugins/DicePP/conftest.py`

**功能**:
1. 将 `DicePP` 目录加入 `sys.path`
2. 使测试文件中的 `from core.xxx import` 语句生效

**接口规格**:

```python
import sys
import os

# 将 DicePP 目录加入 sys.path
dicepp_path = os.path.dirname(__file__)
if dicepp_path not in sys.path:
    sys.path.insert(0, dicepp_path)
```

**验收标准**:
- 测试文件中 `from core.bot import Bot` 能正确解析
- 测试文件中 `from module.roll.expression import` 能正确解析
- 无需修改现有测试文件的 import 语句

## 路径解析顺序

```
pytest (工程根目录)
    │
    ├─ 根 conftest.py: sys.path.insert(0, "src")
    │
    └─ DicePP conftest.py: sys.path.insert(0, "src/plugins/DicePP")
         │
         └─ 测试文件: from core.bot import Bot  ✅
```

## 验证方法

```bash
# 从工程根目录运行
cd d:\Workplace\nonebot-dicepp
pytest src/plugins/DicePP/core/data/unit_test.py -v

# 验收标准：
# 1. 无 ModuleNotFoundError
# 2. 测试正常执行
```

## 兼容性说明

| 场景 | 行为 |
|------|------|
| pytest 从根目录运行 | ✅ 两个 conftest.py 依次注入路径 |
| pytest 从 DicePP 目录运行 | ✅ 只有 DicePP conftest.py 生效 |
| 直接 python -m unittest | ⚠️ 需要手动 cd 到 DicePP 目录（保持现有行为） |
| IDE 运行单个测试 | ✅ conftest.py 自动被 pytest 加载 |
