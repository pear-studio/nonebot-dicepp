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
RUN pip install uv==0.11.16 --index-url ${PIP_INDEX_URL} --no-cache-dir

# 复制依赖文件（uv.lock 保证可复现构建）
COPY pyproject.toml uv.lock ./

# 先只安装第三方依赖，不安装当前项目。
# 这样普通源码变更不会让大体积的 .venv 依赖层失效。
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --no-dev --frozen --no-install-project

# 生成轻量项目元数据，保证 importlib.metadata.version('dicepp') 可用。
# 该层只依赖 pyproject.toml，不随普通源码变更失效。
RUN VERSION="$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")" && \
    mkdir -p "/app/project-meta/dicepp-${VERSION}.dist-info" && \
    printf "Metadata-Version: 2.1\nName: dicepp\nVersion: %s\n" "$VERSION" \
        > "/app/project-meta/dicepp-${VERSION}.dist-info/METADATA"

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
COPY --from=builder /app/project-meta /app/project-meta

# 复制运行所需文件。避免 COPY . . 把非运行文件变动带进镜像层。
COPY bot.py ./
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY config/global.json config/global.json
COPY config/bots/_template.json config/bots/_template.json
COPY templates/ templates/

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/project-meta"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DICEPP_PROJECT_ROOT=/app

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["python", "bot.py"]
