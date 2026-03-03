@echo off
chcp 65001 >nul
REM DicePP 开发环境一键安装脚本（Windows）
REM 依赖：uv 已安装  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

echo [1/2] 创建虚拟环境 .venv ...
uv venv .venv

echo [2/2] 安装依赖（使用清华镜像）...
uv pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
uv pip install pytest pytest-asyncio pytest-cov --index-url https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo [OK] 安装完成！
echo      运行 Bot:   tools\dev\run.bat
echo      运行测试:   tools\dev\test.bat
pause
