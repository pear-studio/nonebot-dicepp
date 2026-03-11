#!/bin/bash
# stop.sh - 停止 DicePP 容器
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== 停止 DicePP ====="

# 检查 Docker 环境
COMPOSE_CMD=$(check_docker)

cd "$PROJECT_ROOT"

# 停止容器
step "停止 DicePP 容器..."
$COMPOSE_CMD down

success "DicePP 已停止"