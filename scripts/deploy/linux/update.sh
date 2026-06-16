#!/bin/bash
# update.sh - 更新 DicePP
# 镜像模式（有 DICEPP_IMAGE_TAG）: pull 镜像 + 重启
# 源码模式（无 DICEPP_IMAGE_TAG）: git pull + build + 重启
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== 更新 DicePP ====="

# 检查 Docker 环境
COMPOSE_CMD=$(check_docker)

cd "$PROJECT_ROOT"

if [ -n "$DICEPP_IMAGE_TAG" ]; then
    # ── 镜像模式 ──────────────────────────────────────────
    info "镜像模式: DICEPP_IMAGE_TAG=$DICEPP_IMAGE_TAG"

    step "1/2 拉取镜像..."
    DICEPP_IMAGE_TAG="$DICEPP_IMAGE_TAG" $COMPOSE_CMD pull
    success "镜像已拉取"

    step "2/2 重启容器..."
    DICEPP_IMAGE_TAG="$DICEPP_IMAGE_TAG" $COMPOSE_CMD up -d
    success "容器已重启"

else
    # ── 源码模式（dev 开发环境）──────────────────────────
    info "源码模式: git pull + 本地构建"

    step "1/3 拉取最新代码..."
    git pull
    success "代码已更新"

    step "2/3 重新构建镜像..."
    $COMPOSE_CMD build
    success "镜像已更新"

    step "3/3 重启容器..."
    $COMPOSE_CMD up -d
    success "容器已重启"

fi

echo ""
echo "===== 更新完成 ====="
info "查看日志: make logs"
