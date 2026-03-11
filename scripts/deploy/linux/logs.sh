#!/bin/bash
# logs.sh - 查看 DicePP 容器日志
# 用法: logs.sh [-f] [行数]
#   -f: 实时跟踪日志
#   行数: 显示最近N行，默认50

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

# 解析参数
FOLLOW=false
LINES=50

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow)
            FOLLOW=true
            shift
            ;;
        *)
            if [[ $1 =~ ^[0-9]+$ ]]; then
                LINES=$1
            fi
            shift
            ;;
    esac
done

echo "===== DicePP 日志 ====="

# 检查容器是否存在
if ! is_container_exists "dicepp"; then
    error "DicePP 容器不存在，请先运行 setup.sh"
    exit 1
fi

if [ "$FOLLOW" = true ]; then
    info "实时日志 (Ctrl+C 退出)..."
    docker logs dicepp --tail=$LINES -f
else
    info "最近 $LINES 行日志："
    docker logs dicepp --tail=$LINES
fi
