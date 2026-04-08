# ── 构建阶段 ──────────────────────────────────────────────────────
FROM python:3.10-slim AS builder

# 构建参数：镜像源配置
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 使用国内镜像源加速（支持通过 build-arg 自定义）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s/deb.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources && \
        sed -i "s/security.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s/deb.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list && \
        sed -i "s/security.debian.org/${APT_MIRROR}/g" /etc/apt/sources.list; \
    fi

# 安装构建工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（使用指定 pip 源）
RUN pip install uv --index-url ${PIP_INDEX_URL} --no-cache-dir

# 复制依赖文件
COPY pyproject.toml .

# 创建虚拟环境并安装依赖（使用指定 uv 源）
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv pip install . --index-url ${UV_INDEX_URL}

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
ENV DICEPP_PROJECT_ROOT=/app

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["python", "bot.py"]
