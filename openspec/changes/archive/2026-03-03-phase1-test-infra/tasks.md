# Phase 1: 任务清单

## T1.1 添加测试依赖
- [x] 在 `pyproject.toml` 的 `[tool.poetry.group.dev.dependencies]` 中添加：
  - `pytest = ">=7.4"`
  - `pytest-asyncio = ">=0.23"`
  - `pytest-cov = ">=4.1"`
- [x] 在 `[project.optional-dependencies]` 中同步添加 dev 依赖

## T1.2 配置 pytest
- [x] 在 `pyproject.toml` 添加 `[tool.pytest.ini_options]` 节
  - `asyncio_mode = "auto"`
  - `testpaths = ["src/plugins/DicePP"]`
  - `python_files = ["unit_test.py"]`
  - `python_classes = ["MyTestCase"]`
- [x] 补充 `python_files` 以支持新测试命名 `["unit_test.py", "test_*.py", "*_test.py"]`
- [x] 补充 `python_classes` 以支持新测试类 `["MyTestCase", "Test*"]`
- [x] 补充 `python_functions = ["test*"]`
- [x] 补充 `addopts = "--tb=short"`

## T1.3 解决模块路径
- [x] 在工程根目录创建 `conftest.py`，添加 `sys.path.insert(0, str(src_path))`
- [x] 在 `src/plugins/DicePP/conftest.py` 中添加 `sys.path.insert(0, dicepp_path)`

## T1.4 配置覆盖率
- [x] 在工程根目录创建 `.coveragerc`
- [x] 配置 `[run].source`、`[run].omit`、`[report].show_missing`

## T1.5 文档更新
- [x] 在 README.md 添加"开发者-运行测试"章节

## T1.6 验证
- [x] 执行 `pytest` 验证测试发现
- [x] 执行 `pytest --cov` 验证覆盖率收集