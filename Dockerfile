# ── 构建阶段 ──────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# 构建参数：镜像源配置（默认官方源，国内构建时通过 compose/--build-arg 覆盖）
ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG UV_INDEX_URL=https://pypi.org/simple

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

# 复制依赖文件（uv.lock 保证可复现构建）
COPY pyproject.toml uv.lock ./
COPY src/ src/

# 创建虚拟环境并按锁文件安装依赖
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --no-dev --frozen

# ── 运行阶段 ──────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# 镜像元数据（构建时注入）
ARG DICEPP_IMAGE_TAG=unknown
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.version="${DICEPP_IMAGE_TAG}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.source="https://github.com/pear-studio/nonebot-dicepp"

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
