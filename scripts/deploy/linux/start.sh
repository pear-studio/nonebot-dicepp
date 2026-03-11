#!/bin/bash
# start.sh - 启动 DicePP 容器
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== 启动 DicePP ====="

# 检查 Docker 环境
COMPOSE_CMD=$(check_docker)

cd "$PROJECT_ROOT"

# 检查容器状态
if is_container_running "dicepp"; then
    info "DicePP 容器已在运行"
    exit 0
fi

# 检查网络
if ! check_network; then
    error "dice-net 网络不存在，请先运行 setup.sh"
    exit 1
fi

# 启动容器
step "启动 DicePP 容器..."
$COMPOSE_CMD up -d

success "DicePP 已启动"
info "查看日志: make logs"