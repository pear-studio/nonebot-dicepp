# DicePP 开发命令集
# 依赖工具：uv（https://github.com/astral-sh/uv）
# 安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh（Linux/Mac）
#           或 powershell -c "irm https://astral.sh/uv/install.ps1 | iex"（Windows）

.PHONY: install install-dev test test-cov run clean help
.PHONY: deploy start stop restart logs update status
.PHONY: setup-llbot llbot-start llbot-stop llbot-restart llbot-logs
.PHONY: start-all stop-all

# ── 环境安装 ─────────────────────────────────────────────────────────────────
install:  ## 安装运行时依赖
	uv venv .venv
	uv pip install . --index-url https://pypi.tuna.tsinghua.edu.cn/simple

install-dev:  ## 安装开发依赖
	uv venv .venv
	uv pip install ".[dev]" --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ── 测试 ─────────────────────────────────────────────────────────────────────
test:  ## 运行测试
	uv run pytest

test-cov:  ## 运行测试（带覆盖率报告）
	uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing --cov-report=html

# ── 本地运行 ──────────────────────────────────────────────────────────────────
run:  ## 本地运行 Bot (Windows)
	uv run python bot.py

# ── 清理 ─────────────────────────────────────────────────────────────────────
clean:  ## 清理临时文件
	rm -rf .venv .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ══════════════════════════════════════════════════════════════════════════════
# Docker 部署 (Linux/WSL)
# ══════════════════════════════════════════════════════════════════════════════

# ── DicePP 控制 ───────────────────────────────────────────────────────────────
deploy:  ## 首次部署 DicePP（初始化环境、构建、启动）
	@bash scripts/deploy/linux/setup.sh

start:  ## 启动 DicePP 容器
	@bash scripts/deploy/linux/start.sh

stop:  ## 停止 DicePP 容器
	@bash scripts/deploy/linux/stop.sh

restart:  ## 重启 DicePP 容器
	@bash scripts/deploy/linux/restart.sh

logs:  ## 查看 DicePP 日志（实时）
	@bash scripts/deploy/linux/logs.sh -f

update:  ## 更新代码并重启 DicePP
	@bash scripts/deploy/linux/update.sh

status:  ## 查看服务状态
	@bash scripts/deploy/linux/status.sh

# ── LLOneBot 控制 ─────────────────────────────────────────────────────────────
LLBOT_DIR := $(shell cd .. 2>/dev/null && pwd)/llonebot

setup-llbot:  ## 安装 LLOneBot
	@bash scripts/deploy/linux/llonebot/setup.sh

llbot-start:  ## 启动 LLOneBot 容器
	@if [ -d "$(LLBOT_DIR)" ]; then \
		cd "$(LLBOT_DIR)" && docker compose up -d; \
	else \
		echo "LLOneBot 未安装，请先运行: make setup-llbot"; \
	fi

llbot-stop:  ## 停止 LLOneBot 容器
	@if [ -d "$(LLBOT_DIR)" ]; then \
		cd "$(LLBOT_DIR)" && docker compose down; \
	else \
		echo "LLOneBot 目录不存在"; \
	fi

llbot-restart:  ## 重启 LLOneBot 容器
	@if [ -d "$(LLBOT_DIR)" ]; then \
		cd "$(LLBOT_DIR)" && docker compose restart; \
	else \
		echo "LLOneBot 目录不存在"; \
	fi

llbot-logs:  ## 查看 LLOneBot 日志
	@if [ -d "$(LLBOT_DIR)" ]; then \
		cd "$(LLBOT_DIR)" && docker compose logs -f; \
	else \
		echo "LLOneBot 目录不存在"; \
	fi

# ── 全部服务 ──────────────────────────────────────────────────────────────────
start-all:  ## 启动全部服务（先 LLOneBot，再 DicePP）
	@echo "===== 启动全部服务 ====="
	@if [ -d "$(LLBOT_DIR)" ]; then \
		echo "[1/2] 启动 LLOneBot..."; \
		cd "$(LLBOT_DIR)" && docker compose up -d; \
	else \
		echo "[1/2] LLOneBot 未安装，跳过"; \
	fi
	@echo "[2/2] 启动 DicePP..."
	@bash scripts/deploy/linux/start.sh
	@echo "===== 全部服务已启动 ====="

stop-all:  ## 停止全部服务（先 DicePP，再 LLOneBot）
	@echo "===== 停止全部服务 ====="
	@echo "[1/2] 停止 DicePP..."
	@bash scripts/deploy/linux/stop.sh
	@if [ -d "$(LLBOT_DIR)" ]; then \
		echo "[2/2] 停止 LLOneBot..."; \
		cd "$(LLBOT_DIR)" && docker compose down; \
	else \
		echo "[2/2] LLOneBot 未安装，跳过"; \
	fi
	@echo "===== 全部服务已停止 ====="

# ── 帮助 ──────────────────────────────────────────────────────────────────────
help:  ## 显示帮助信息
	@echo "DicePP 命令集"
	@echo ""
	@echo "开发命令 (Windows/本地):"
	@grep -E '^(install|install-dev|test|test-cov|run|clean):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
	@echo ""
	@echo "部署命令 (Linux/WSL Docker):"
	@grep -E '^(deploy|start|stop|restart|logs|update|status):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
	@echo ""
	@echo "LLOneBot 命令:"
	@grep -E '^(setup-llbot|llbot-start|llbot-stop|llbot-restart|llbot-logs):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
	@echo ""
	@echo "全部服务:"
	@grep -E '^(start-all|stop-all):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
