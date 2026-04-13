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
step "1/6 检查 Docker 环境..."
COMPOSE_CMD=$(check_docker)
success "Docker 环境正常 (使用: $COMPOSE_CMD)"

# 1.5 配置镜像源（国内服务器自动检测）
step "2/6 检测并配置镜像源..."

# 检测是否在中国大陆（通过访问国内/国外镜像源速度）
detect_mirror() {
    # 尝试访问国内镜像源
    if curl -s --max-time 2 -o /dev/null "https://mirrors.tuna.tsinghua.edu.cn" 2>/dev/null; then
        echo "china"
    else
        echo "global"
    fi
}

MIRROR_REGION=$(detect_mirror)

if [ "$MIRROR_REGION" = "china" ]; then
    info "检测到国内网络环境，使用国内镜像源加速"
    # 设置环境变量供 docker-compose 使用
    export APT_MIRROR="mirrors.tuna.tsinghua.edu.cn"
    export PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
    export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"

    # 保存到 .env 文件供后续使用
    if [ -f ".env" ]; then
        # 检查是否已存在相关配置
        grep -q "^APT_MIRROR=" .env || echo "APT_MIRROR=mirrors.tuna.tsinghua.edu.cn" >> .env
        grep -q "^PIP_INDEX_URL=" .env || echo "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" >> .env
        grep -q "^UV_INDEX_URL=" .env || echo "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" >> .env
    fi

    success "已配置清华镜像源"
    info "如需使用其他镜像源，可设置环境变量:"
    info "  APT_MIRROR=mirrors.aliyun.com make deploy"
else
    info "检测到国际网络环境，使用官方镜像源"
    export APT_MIRROR="deb.debian.org"
    export PIP_INDEX_URL="https://pypi.org/simple"
    export UV_INDEX_URL="https://pypi.org/simple"
fi

# 2. 检查 dice-net 网络
step "3/6 检查 dice-net 网络..."
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
step "4/6 配置环境变量..."
cd "$PROJECT_ROOT"

if [ ! -f ".env" ]; then
    # 创建默认 .env 文件，关键：HOST 必须是 0.0.0.0 才能被外部访问
    cat > .env << 'EOF'
# DicePP 环境变量配置
HOST=0.0.0.0
PORT=8080
# ACCESS_TOKEN=your_token_here
EOF
    success "已创建默认 .env 文件 (HOST=0.0.0.0, PORT=8080)"
    info "提示: 如需修改配置，请编辑 .env 文件"
else
    # 检查 .env 是否包含 HOST 配置
    if ! grep -q "^HOST=" .env; then
        warn ".env 文件缺少 HOST 配置，正在添加..."
        echo "" >> .env
        echo "# 添加于 $(date)" >> .env
        echo "HOST=0.0.0.0" >> .env
        success "已添加 HOST=0.0.0.0 到 .env"
    elif grep -q "^HOST=127.0.0.1" .env || grep -q "^HOST=localhost" .env; then
        warn ".env 中的 HOST 设置为本地地址，可能导致外部无法连接"
        warn "建议修改为: HOST=0.0.0.0"
    else
        success ".env 文件已存在且包含 HOST 配置"
    fi
fi

# 4. 构建 Docker 镜像
step "5/6 构建 Docker 镜像..."
$COMPOSE_CMD build
success "Docker 镜像构建完成"

# 5.5 检查必要配置
step "检查必要配置..."
ACCOUNT_CONFIG_COUNT=$(ls -1 "$PROJECT_ROOT/config/bots/"*.json 2>/dev/null | grep -v "_template.json" | wc -l)
if [ "$ACCOUNT_CONFIG_COUNT" -eq 0 ]; then
    warn "未找到账号配置文件"
    info "请执行: cp config/bots/_template.json config/bots/你的QQ号.json"
    info "然后编辑 config/bots/你的QQ号.json 设置 master 等字段"
else
    success "找到 $ACCOUNT_CONFIG_COUNT 个账号配置"
fi

if [ ! -f "$PROJECT_ROOT/config/secrets.json" ]; then
    info "如需启用 Persona AI，请创建 config/secrets.json:"
    info "  cp config/secrets.json.example config/secrets.json"
    info "  然后编辑填写你的 API 密钥"
fi

# 5. 启动容器
step "6/6 启动 DicePP 容器..."
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
