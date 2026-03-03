# DicePP 开发命令集
# 依赖工具：uv（https://github.com/astral-sh/uv）
# 安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh（Linux/Mac）
#           或 powershell -c "irm https://astral.sh/uv/install.ps1 | iex"（Windows）

.PHONY: install install-dev test test-cov run clean docker-build docker-up docker-down

# ── 环境安装 ─────────────────────────────────────────────────────────────────
install:
	uv venv .venv
	uv pip install . --index-url https://pypi.tuna.tsinghua.edu.cn/simple

install-dev:
	uv venv .venv
	uv pip install ".[dev]" --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ── 测试 ─────────────────────────────────────────────────────────────────────
test:
	uv run pytest

test-cov:
	uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing --cov-report=html

# ── 本地运行 ──────────────────────────────────────────────────────────────────
run:
	uv run python bot.py

# ── 清理 ─────────────────────────────────────────────────────────────────────
clean:
	rm -rf .venv .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── Docker ────────────────────────────────────────────────────────────────────
docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f bot
