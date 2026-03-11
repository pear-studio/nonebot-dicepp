#!/bin/bash
# restart.sh - 重启 DicePP 容器
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== 重启 DicePP ====="

# 检查 Docker 环境
COMPOSE_CMD=$(check_docker)

cd "$PROJECT_ROOT"

# 重启容器
step "重启 DicePP 容器..."
$COMPOSE_CMD restart

success "DicePP 已重启"
info "查看日志: make logs"