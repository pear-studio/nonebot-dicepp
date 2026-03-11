#!/bin/bash
# setup.sh - DicePP 首次部署向导
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(get_project_root)"

echo "===== DicePP 首次部署向导 ====="
echo ""
info "项目目录: $PROJECT_ROOT"
echo ""

# 1. 检查 Docker 环境
step "1/5 检查 Docker 环境..."
COMPOSE_CMD=$(check_docker)
success "Docker 环境正常 (使用: $COMPOSE_CMD)"

# 2. 检查 dice-net 网络
step "2/5 检查 dice-net 网络..."
if ! check_network; then
    warn "dice-net 网络不存在"
    echo ""
    echo "请先运行 LLOneBot 安装脚本创建网络："
    echo "  bash $SCRIPT_DIR/llonebot/setup.sh"
    echo ""
    echo "或手动创建网络："
    echo "  docker network create dice-net"
    echo ""
    read -p "是否现在创建 dice-net 网络？[y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        create_network
    else
        error "请先创建 dice-net 网络后再运行此脚本"
        exit 1
    fi
else
    success "dice-net 网络已存在"
fi

# 3. 配置环境变量
step "3/5 配置环境变量..."
cd "$PROJECT_ROOT"

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        success "已从 .env.example 创建 .env"
        warn "请编辑 .env 文件配置必要的环境变量"
    else
        # 创建基本的 .env 文件
        cat > .env << 'EOF'
HOST=0.0.0.0
PORT=8080
# ACCESS_TOKEN=your_token_here
EOF
        success "已创建默认 .env 文件"
    fi
else
    success ".env 文件已存在"
fi

# 4. 构建 Docker 镜像
step "4/5 构建 Docker 镜像..."
$COMPOSE_CMD build
success "Docker 镜像构建完成"

# 5. 启动容器
step "5/5 启动 DicePP 容器..."
$COMPOSE_CMD up -d
success "DicePP 容器已启动"

echo ""
echo "===== 部署完成 ====="
echo ""
info "查看日志: make logs 或 bash $SCRIPT_DIR/logs.sh"
info "查看状态: make status 或 bash $SCRIPT_DIR/status.sh"
info "停止服务: make stop 或 bash $SCRIPT_DIR/stop.sh"
echo ""
echo "下一步："
echo "  1. 确保 LLOneBot 已安装并登录"
echo "  2. 在 LLOneBot 中配置反向 WebSocket: ws://dicepp:8080/onebot/v11/ws"
echo "  3. 查看日志确认连接成功"
echo ""
