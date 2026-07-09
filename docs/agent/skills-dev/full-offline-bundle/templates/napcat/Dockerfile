# DicePP 定制 NapCat Docker 镜像
#
# 设计:
#   - 基础依赖: apt 安装（在线，但依赖层极少变化，首次 build 后长期缓存）
#   - QQNT:      从腾讯 CDN 下载（在线，URL 不变的层永久缓存，不会反复下载）
#   - NapCat:    COPY 本地预下载的 NapCat.Shell.zip（离线，28MB）

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

# 系统依赖（与 NapCat 官方 base 镜像对齐，补充 ca-certificates）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 \
    libnotify4 \
    libsecret-1-0 \
    libgbm1 \
    xvfb \
    libasound2 \
    fonts-wqy-zenhei \
    gnutls-bin \
    libglib2.0-dev \
    libdbus-1-3 \
    libgtk-3-0 \
    libxss1 \
    libxtst6 \
    libatspi2.0-0 \
    libx11-xcb1 \
    ffmpeg \
    unzip \
    dbus-user-session \
    curl \
    jq \
    gosu \
    tzdata \
    && echo "${TZ}" > /etc/timezone \
    && ln -sf /usr/share/zoneinfo/${TZ} /etc/localtime \
    && update-ca-certificates \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*

# QQNT — 从腾讯 CDN 下载
# URL 不变时 Docker 层永久缓存，不会每次 build 都下载
ARG QQNT_VERSION=3.2.23-44343
RUN arch=$(arch | sed s/aarch64/arm64/ | sed s/x86_64/amd64/) && \
    case "${arch}" in \
      amd64) QQNT_FILE_ID="94704804" ;; \
      arm64) QQNT_FILE_ID="94704804" ;; \
      *)     echo "Unsupported arch: ${arch}"; exit 1 ;; \
    esac && \
    QQ_URL="https://dldir1.qq.com/qqfile/qq/QQNT/${QQNT_FILE_ID}/linuxqq_${QQNT_VERSION}_${arch}.deb" && \
    echo "Downloading QQNT from: ${QQ_URL}" && \
    for i in 1 2 3; do \
        curl --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 300 \
             -fL -o /tmp/linuxqq.deb "${QQ_URL}" && break || \
        (echo "Attempt $i failed, retrying in 10s..." && sleep 10); \
    done && \
    test -f /tmp/linuxqq.deb && \
    dpkg -i --force-depends /tmp/linuxqq.deb && \
    rm /tmp/linuxqq.deb

# 创建运行用户
RUN useradd --no-log-init -d /app napcat

WORKDIR /app

# NapCat Shell — 从本地预下载文件复制（离线）
COPY NapCat.Shell.zip /app/

# 注入 NapCat 启动脚本到 QQ
RUN echo "(async () => {await import('file:///app/napcat/napcat.mjs');})();" \
        > /opt/QQ/resources/app/loadNapCat.js && \
    sed -i 's|"main": "[^"]*"|"main": "./loadNapCat.js"|' \
        /opt/QQ/resources/app/package.json

# 入口
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

VOLUME ["/app/napcat/config", "/app/.config/QQ"]

ENTRYPOINT ["bash", "/app/entrypoint.sh"]
