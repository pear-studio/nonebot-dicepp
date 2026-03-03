# ── 构建阶段：安装依赖 ────────────────────────────────────────────────────────
FROM python:3.10-slim AS builder

# 安装 uv（Rust 实现的极速包管理器）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# 先只拷贝依赖声明文件，利用 Docker 层缓存
COPY pyproject.toml requirements.txt ./

# 使用 uv 安装生产依赖到 /app/.venv，离线不污染系统 Python
RUN uv venv .venv && \
    uv pip install --python .venv/bin/python \
        -r requirements.txt \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ── 运行阶段：精简镜像 ────────────────────────────────────────────────────────
FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 让 Python 直接使用 venv 中的包，不依赖系统环境
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

WORKDIR /app

# 只从 builder 阶段拷贝虚拟环境（不带 uv 本身）
COPY --from=builder /app/.venv /app/.venv

# 拷贝项目源码
COPY . .

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import psutil; exit(0)" || exit 1

CMD ["python", "bot.py"]