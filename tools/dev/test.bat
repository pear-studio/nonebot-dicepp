@echo off
REM 运行测试套件（使用 .venv 中的 pytest，不依赖系统环境）
uv run pytest %*
