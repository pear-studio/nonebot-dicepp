# Spec: pytest 配置规格

## 概述

定义 pytest 在本项目中的配置行为，确保测试可从工程根目录一键运行。

## 配置位置

配置统一在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 节中定义。

## 必须配置项

### SPEC-P1-001: asyncio_mode

- **值**: `"auto"`
- **原因**: 项目使用 `IsolatedAsyncioTestCase`，需要自动处理异步测试
- **验收标准**: 异步测试方法无需额外装饰器即可运行

### SPEC-P1-002: testpaths

- **值**: `["src/plugins/DicePP"]`
- **原因**: 所有测试文件位于此目录下
- **验收标准**: `pytest` 命令自动发现此路径下的测试

### SPEC-P1-003: python_files

- **值**: `["unit_test.py", "test_*.py", "*_test.py"]`
- **原因**: 
  - 现有测试文件命名为 `unit_test.py`
  - 新测试遵循 pytest 标准命名 `test_*.py`
- **验收标准**: 两种命名方式的测试文件都能被发现

### SPEC-P1-004: python_classes

- **值**: `["MyTestCase", "Test*"]`
- **原因**:
  - 现有测试类命名为 `MyTestCase`
  - 新测试类遵循 pytest 标准命名 `Test*`
- **验收标准**: 两种命名方式的测试类都能被执行

### SPEC-P1-005: python_functions

- **值**: `["test*"]`
- **原因**: 测试方法以 `test` 开头
- **验收标准**: 所有以 `test` 开头的方法被识别为测试用例

### SPEC-P1-006: addopts

- **值**: `"--tb=short"`
- **原因**: 精简 traceback 输出，提高可读性
- **验收标准**: 测试失败时输出简短的错误信息

## 完整配置示例

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["src/plugins/DicePP"]
python_files = ["unit_test.py", "test_*.py", "*_test.py"]
python_classes = ["MyTestCase", "Test*"]
python_functions = ["test*"]
addopts = "--tb=short"
```

## 验证命令

```bash
# 验证配置生效
pytest --collect-only

# 应输出发现的测试文件数量 >= 3（现有三个 unit_test.py）
```
