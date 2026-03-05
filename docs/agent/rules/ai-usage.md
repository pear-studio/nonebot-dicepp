# Agent Rules

## 依赖
- 唯一声明: `pyproject.toml`
- 安装: `uv pip install .` / `".[dev]"`

## 命令
```bash
uv venv .venv && uv pip install ".[dev]"  # 初始化
uv run pytest                              # 测试
uv run pytest tests/module/roll/ -v        # 模块测试
uv run python bot.py                       # 运行
```

## 测试
- 文件: `test_*.py`
- 目录: `tests/`

## 风格
- 最小化变更, 不确定先询问
- 提交前 `uv run pytest`, 不自动push
- git comment主要用中文
- 有大的修改后主动更新`docs/agent/rules/dicepp.md`

## 配置文件
- 依赖: `pyproject.toml`
- 测试: `pyproject.toml` [tool.pytest.ini_options]
- 覆盖率: `.coveragerc`
- 环境变量: `.env`

## 规范文件
- `docs/agent/rules/dicepp.md` - DicePP 开发规范
