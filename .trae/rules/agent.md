# Agent Rules

## 依赖
- 唯一声明: `pyproject.toml`
- 安装: `uv pip install .` / `".[dev]"`
- 禁止 requirements.txt

## 命令
```bash
uv venv .venv && uv pip install ".[dev]"  # 初始化
uv run pytest                              # 测试
uv run python bot.py                       # 运行
```

## 测试
- 文件: `test_*.py`, `*_test.py`, `unit_test.py`
- 目录: `src/plugins/DicePP/`

## 风格
- 最小化变更
- 提交前 `uv run pytest`