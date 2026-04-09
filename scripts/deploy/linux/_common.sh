#!/bin/bash
# _common.sh - 公共函数库
# 被其他部署脚本 source 使用

# ── 颜色定义 ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── 输出函数 ──────────────────────────────────────────────────────
info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }
step()    { echo -e "${CYAN}[STEP]${NC} $1"; }

# ── 路径检测 ──────────────────────────────────────────────────────
# 获取项目根目录（从 scripts/deploy/linux/ 往上 3 层）
get_project_root() {
    local script_dir
    # 使用调用者的 BASH_SOURCE
    if [ -n "${BASH_SOURCE[1]}" ]; then
        script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    else
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    fi
    # scripts/deploy/linux/ -> 往上 3 层到项目根目录
    echo "$(cd "$script_dir/../../.." && pwd)"
}

# 获取 LLOneBot 目录（与项目同级）
get_llonebot_dir() {
    local project_root
    project_root="$(get_project_root)"
    echo "$(dirname "$project_root")/llonebot"
}

# ── Docker Compose 命令兼容 ───────────────────────────────────────
# 自动检测使用 docker compose (v2) 还是 docker-compose (v1)
get_compose_cmd() {
    if docker compose version &>/dev/null; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null && docker-compose version &>/dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# ── Docker 环境检查 ───────────────────────────────────────────────
# 检查 Docker 是否安装并返回 compose 命令
# 如果检查失败则退出脚本
check_docker() {
    if ! command -v docker &>/dev/null; then
        error "Docker 未安装"
        echo "请先安装 Docker: https://docs.docker.com/engine/install/"
        exit 1
    fi
    
    if ! docker info &>/dev/null; then
        error "Docker 服务未运行或当前用户无权限"
        echo "尝试: sudo systemctl start docker"
        echo "或将用户加入 docker 组: sudo usermod -aG docker \$USER"
        exit 1
    fi
    
    local compose_cmd
    compose_cmd=$(get_compose_cmd)
    if [ -z "$compose_cmd" ]; then
        error "Docker Compose 未安装"
        echo ""
        echo "安装 Docker Compose V2 (推荐):"
        echo "  mkdir -p ~/.docker/cli-plugins"
        echo "  curl -SL https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 -o ~/.docker/cli-plugins/docker-compose"
        echo "  chmod +x ~/.docker/cli-plugins/docker-compose"
        echo ""
        echo "国内服务器可使用代理加速:"
        echo "  curl -SL https://ghfast.top/https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 -o ~/.docker/cli-plugins/docker-compose"
        echo ""
        echo "或安装旧版 V1: pip install docker-compose"
        exit 1
    fi
    
    echo "$compose_cmd"
}

# ── 网络检查 ──────────────────────────────────────────────────────
# 检查 dice-net 网络是否存在
check_network() {
    if docker network ls | grep -q "dice-net"; then
        return 0
    else
        return 1
    fi
}

# 创建 dice-net 网络
create_network() {
    if check_network; then
        info "dice-net 网络已存在"
    else
        step "创建 dice-net 网络..."
        docker network create dice-net
        success "dice-net 网络已创建"
    fi
}

# ── 容器状态检查 ──────────────────────────────────────────────────
# 检查容器是否在运行
is_container_running() {
    local container_name="$1"
    docker ps --format '{{.Names}}' | grep -q "^${container_name}$"
}

# 检查容器是否存在（包括已停止的）
is_container_exists() {
    local container_name="$1"
    docker ps -a --format '{{.Names}}' | grep -q "^${container_name}$"
}
