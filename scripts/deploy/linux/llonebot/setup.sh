#!/bin/bash
# llonebot/setup.sh - LLOneBot 安装向导
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../_common.sh"

PROJECT_ROOT="$(get_project_root)"
LLONEBOT_DIR="$(get_llonebot_dir)"

echo "===== LLOneBot 安装向导 ====="
echo ""
info "LLOneBot 将安装到: $LLONEBOT_DIR"
echo ""

# 1. 检查 Docker 环境
step "1/5 检查 Docker 环境..."
COMPOSE_CMD=$(check_docker)
success "Docker 环境正常 (使用: $COMPOSE_CMD)"

# 2. 创建 dice-net 网络
step "2/5 创建 dice-net 网络..."
create_network

# 3. 创建 LLOneBot 目录
step "3/5 创建 LLOneBot 目录..."
if [ -d "$LLONEBOT_DIR" ]; then
    warn "LLOneBot 目录已存在: $LLONEBOT_DIR"
    read -p "是否继续？这可能会覆盖现有配置 [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "已取消"
        exit 0
    fi
else
    mkdir -p "$LLONEBOT_DIR"
    success "目录已创建"
fi

cd "$LLONEBOT_DIR"

# 4. 下载并运行官方安装脚本
step "4/5 下载并运行 LLOneBot 官方安装脚本..."
echo ""
info "将下载并执行官方安装脚本"
info "脚本来源: https://github.com/LLOneBot/LuckyLilliaBot"
echo ""

# 下载官方脚本
curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/LLOneBot/LuckyLilliaBot/refs/heads/main/script/install-llbot-docker.sh -o llbot-docker.sh
chmod u+x ./llbot-docker.sh

# 执行官方脚本
./llbot-docker.sh

# 5. 修改网络配置
step "5/5 配置 dice-net 网络..."

# 查找 docker-compose 文件
COMPOSE_FILE=""
for f in docker-compose.yaml docker-compose.yml; do
    if [ -f "$f" ]; then
        COMPOSE_FILE="$f"
        break
    fi
done

if [ -z "$COMPOSE_FILE" ]; then
    warn "未找到 docker-compose 文件，请手动添加网络配置"
else
    # 检查是否已经有网络配置
    if grep -q "dice-net" "$COMPOSE_FILE"; then
        info "网络配置已存在"
    else
        info "添加 dice-net 网络配置..."
        
        # 备份原文件
        cp "$COMPOSE_FILE" "${COMPOSE_FILE}.bak"
        
        # 追加网络配置
        # 这里使用简单的追加方式，因为 yaml 结构可能不同
        cat >> "$COMPOSE_FILE" << 'EOF'

# DicePP 网络配置 (自动添加)
networks:
  dice-net:
    external: true
EOF

        # 需要在 service 下也添加 networks，但这比较复杂
        # 提示用户手动检查
        warn "已追加网络配置到 $COMPOSE_FILE"
        warn "请手动编辑文件，在 service 下添加:"
        echo ""
        echo "  services:"
        echo "    llonebot:  # 或实际的服务名"
        echo "      networks:"
        echo "        - dice-net"
        echo ""
    fi
fi

echo ""
echo "===== 安装完成 ====="
echo ""
info "下一步操作:"
echo ""
echo "  1. 启动 LLOneBot:"
echo "     cd $LLONEBOT_DIR && $COMPOSE_CMD up -d"
echo ""
echo "  2. 查看日志并扫码登录:"
echo "     cd $LLONEBOT_DIR && $COMPOSE_CMD logs -f"
echo "     或访问 WebUI: http://localhost:3080"
echo ""
echo "  3. 在 LLOneBot WebUI 中配置反向 WebSocket:"
echo "     ┌────────────────────────────────────────────────┐"
echo "     │  地址: ws://dicepp:8080/onebot/v11/ws         │"
echo "     └────────────────────────────────────────────────┘"
echo ""
echo "  4. 完成后，返回部署 DicePP:"
echo "     cd $PROJECT_ROOT && make deploy"
echo ""
