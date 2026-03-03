# Phase 1: 任务清单

## T1.1 添加测试依赖
- [ ] 在 `pyproject.toml` 中添加 `pytest >= 7.4`、`pytest-asyncio >= 0.23`、`pytest-cov >= 4.1`
- [ ] 同步更新 `requirements.txt`（如使用）

## T1.2 配置 pytest
- [ ] 在 `pyproject.toml` 中添加 `[tool.pytest.ini_options]` 节
  - `asyncio_mode = "auto"`
  - `testpaths = ["src/plugins/DicePP"]`
  - `python_files = ["unit_test.py"]`
  - `python_classes = ["MyTestCase"]`

## T1.3 创建 conftest.py
- [ ] 在工程根目录创建空 `conftest.py`
- [ ] 在 `src/plugins/DicePP/` 创建 `conftest.py`，注入 `sys.path`

## T1.4 配置覆盖率
- [ ] 在工程根目录创建 `.coveragerc`
- [ ] 配置 source、omit、exclude_lines

## T1.5 验证现有测试
- [ ] 运行 `pytest src/plugins/DicePP/module/roll/unit_test.py`，确认通过
- [ ] 运行 `pytest src/plugins/DicePP/core/data/unit_test.py`，确认通过
- [ ] 运行 `pytest src/plugins/DicePP/core/command/unit_test.py`，确认通过
- [ ] 运行 `pytest --cov`，确认覆盖率报告生成

## T1.6 文档更新
- [ ] 在 `README.md` 中添加"开发者-运行测试"章节，说明命令
