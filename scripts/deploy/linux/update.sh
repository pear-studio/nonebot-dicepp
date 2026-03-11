#!/bin/bash
# update.sh - 更新 DicePP 代码并重启
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== 更新 DicePP ====="

# 检查 Docker 环境
COMPOSE_CMD=$(check_docker)

cd "$PROJECT_ROOT"

# 1. 拉取最新代码
step "1/3 拉取最新代码..."
git pull
success "代码已更新"

# 2. 重新构建镜像
step "2/3 重新构建镜像..."
$COMPOSE_CMD build
success "镜像已更新"

# 3. 重启容器
step "3/3 重启容器..."
$COMPOSE_CMD up -d
success "容器已重启"

echo ""
echo "===== 更新完成 ====="
info "查看日志: make logs"