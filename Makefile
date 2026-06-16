# DicePP 开发命令集
# 依赖工具：uv（https://github.com/astral-sh/uv）
# 安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh（Linux/Mac）
#           或 powershell -c "irm https://astral.sh/uv/install.ps1 | iex"（Windows）

.PHONY: install install-dev test test-fast test-slow test-integration test-e2e test-real-llm test-compat test-collect test-cov run clean help
.PHONY: bump-patch bump-minor bump-major

# ── 环境安装 ─────────────────────────────────────────────────────────────────
install:  ## 安装运行时依赖
	uv sync

install-dev:  ## 安装开发依赖（含 pytest、pytest-cov、pyinstaller）
	uv sync --group dev

# ── 测试 ─────────────────────────────────────────────────────────────────────
test:  ## 运行测试
	uv run pytest

test-fast:  ## 运行快速回归测试
	uv run pytest -m "not (slow or integration or e2e or real_llm)" --tb=short

test-slow:  ## 运行慢速测试
	uv run pytest -m "slow and not integration and not e2e and not real_llm" --tb=short

test-integration:  ## 运行集成测试
	uv run pytest -m "integration and not e2e and not real_llm" --tb=short

test-e2e:  ## 运行端到端测试
	uv run pytest -m "e2e and not real_llm" --tb=short

test-real-llm:  ## 运行真实 LLM 测试（需要本地 secret，可能产生费用）
	uv run pytest -m "real_llm" --tb=short -s

test-compat:  ## 运行兼容性语料测试
	uv run pytest -m compatibility -v --tb=long -x

test-collect:  ## 仅收集测试，验证导入、marker 与 fixture
	uv run pytest --collect-only -q

test-cov:  ## 运行测试（带覆盖率报告）
	uv run pytest -m "not real_llm" --cov --cov-report=term-missing --cov-report=html

# ── 本地运行 ──────────────────────────────────────────────────────────────────
run:  ## 本地运行 Bot (Windows)
	uv run python bot.py

# ── 清理 ─────────────────────────────────────────────────────────────────────
clean:  ## 清理临时文件
	rm -rf .venv .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── 版本号递增 ────────────────────────────────────────────────────────────────
bump-patch:  ## 递增 patch 版本 (3.0.0 → 3.0.1)
	uv run bump-my-version bump patch

bump-minor:  ## 递增 minor 版本 (3.0.0 → 3.1.0)
	uv run bump-my-version bump minor

bump-major:  ## 递增 major 版本 (3.0.0 → 4.0.0)
	uv run bump-my-version bump major

# ── 帮助 ──────────────────────────────────────────────────────────────────────
help:  ## 显示帮助信息
	@echo "DicePP 命令集"
	@echo ""
	@echo "开发命令 (Windows/本地):"
	@grep -E '^(install|install-dev|test|test-fast|test-slow|test-integration|test-e2e|test-real-llm|test-compat|test-collect|test-cov|run|clean):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
	@echo ""
	@echo "版本管理:"
	@grep -E '^(bump-patch|bump-minor|bump-major):.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'
	@echo ""
	@echo "生产部署请使用 version-deploy / deploy-docker agent 技能，或按 docs/linux.md 直接执行 docker compose。"
