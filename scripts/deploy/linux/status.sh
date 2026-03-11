#!/bin/bash
# status.sh - 查看 DicePP 和 LLOneBot 服务状态

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

echo "===== 服务状态检查 ====="
echo ""

# Docker 环境
step "Docker 环境"
if command -v docker &>/dev/null; then
    COMPOSE_CMD=$(get_compose_cmd)
    success "Docker 已安装 (Compose: $COMPOSE_CMD)"
else
    error "Docker 未安装"
fi
echo ""

# 网络状态
step "Docker 网络"
if check_network; then
    success "dice-net 网络存在"
else
    warn "dice-net 网络不存在"
fi
echo ""

# DicePP 容器状态
step "DicePP 容器"
if is_container_running "dicepp"; then
    success "运行中"
    echo "  容器信息:"
    docker ps --filter "name=dicepp" --format "  - 状态: {{.Status}}\n  - 端口: {{.Ports}}"
elif is_container_exists "dicepp"; then
    warn "已停止"
else
    info "未创建"
fi
echo ""

# LLOneBot 容器状态
step "LLOneBot 容器"
# LLOneBot 容器名可能不同，尝试几种常见名称
LLBOT_CONTAINER=""
for name in "llonebot" "llbot" "luckylilliabot"; do
    if is_container_exists "$name"; then
        LLBOT_CONTAINER="$name"
        break
    fi
done

if [ -n "$LLBOT_CONTAINER" ]; then
    if is_container_running "$LLBOT_CONTAINER"; then
        success "运行中 (容器: $LLBOT_CONTAINER)"
        echo "  容器信息:"
        docker ps --filter "name=$LLBOT_CONTAINER" --format "  - 状态: {{.Status}}\n  - 端口: {{.Ports}}"
    else
        warn "已停止 (容器: $LLBOT_CONTAINER)"
    fi
else
    info "未找到 LLOneBot 容器"
fi
echo ""

# 最近日志
step "DicePP 最近日志 (5 行)"
if is_container_exists "dicepp"; then
    docker logs dicepp --tail=5 2>&1 | sed 's/^/  /'
else
    info "  容器不存在"
fi

echo ""
echo "===== 检查完成 ====="