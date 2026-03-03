# Phase 1: 技术设计

## 依赖添加

在 `pyproject.toml` 中新增 dev 依赖组（poetry dev group）：

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4"
pytest-asyncio = "^0.23"
pytest-cov = "^4.1"
```

## pytest 配置

在 `pyproject.toml` 中新增 `[tool.pytest.ini_options]` 节：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["src/plugins/DicePP"]
python_files = ["unit_test.py"]
python_classes = ["MyTestCase"]
python_functions = ["test*"]
addopts = "--tb=short"
```

## sys.path 解决方案

### 问题根源

三个测试文件都用相对导入（如 `from core.bot import Bot`），这依赖工作目录是 `src/plugins/DicePP`。

### 解决方案：多层 `conftest.py`

**根目录 `conftest.py`**（工程根）：
```python
# 空文件，标记 pytest 根
```

**`src/plugins/DicePP/conftest.py`**：
```python
import sys, os
# 将 DicePP 目录加入 sys.path，使所有内部相对导入生效
sys.path.insert(0, os.path.dirname(__file__))
```

这样 pytest 从根目录发现测试，但测试文件的 import 语境正确。

## 覆盖率配置

新建 `.coveragerc`：
```ini
[run]
source = src/plugins/DicePP
omit =
    */unit_test.py
    */template_cmd.py

[report]
show_missing = true
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
```

## 测试文件命名适配

pytest 默认发现 `test_*.py` 或 `*_test.py`，但现有文件名是 `unit_test.py`，已通过 `python_files = ["unit_test.py"]` 配置适配，**无需重命名**。

## 运行命令

```bash
# 运行所有测试
pytest

# 带覆盖率
pytest --cov --cov-report=html

# 只跑某个模块
pytest src/plugins/DicePP/module/roll/unit_test.py

# 只跑某个测试
pytest -k "test_basic_roll"
```

## 风险点

| 风险 | 说明 | 缓解 |
|------|------|------|
| `IsolatedAsyncioTestCase` 与 `pytest-asyncio` 兼容性 | pytest-asyncio 和 unittest async case 可能冲突 | 保持 asyncio_mode="auto" 并测试验证 |
| 测试数据路径 | 集成测试会在本地创建临时目录 | tearDownClass 已有清理逻辑，无需额外处理 |
| Python 3.8 兼容 | poetry group dev 语法需 poetry 1.2+ | 降级方案：直接写在主 dependencies 里并加注释 |
