# ── 构建阶段 ──────────────────────────────────────────────────────
FROM python:3.10-slim AS builder

WORKDIR /app

# 安装构建工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install uv --index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir

# 复制依赖文件
COPY pyproject.toml .

# 创建虚拟环境并安装依赖
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv pip install . --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ── 运行阶段 ──────────────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制项目代码
COPY . .

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["python", "bot.py"]
