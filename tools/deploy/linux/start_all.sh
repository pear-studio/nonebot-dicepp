#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
GOCQ_DIR="$PROJECT_DIR/../go-cqhttp"

echo "===== 启动 DicePP + go-cqhttp ====="
echo "项目目录: $PROJECT_DIR"
echo ""

cd "$PROJECT_DIR"

echo "[1/3] 检查 Docker 网络..."
if ! docker network ls | grep -q dice-net; then
    echo "创建 dice-net 网络..."
    docker network create dice-net
fi

echo "[2/3] 启动 DicePP..."
if [ -f "docker-compose.yml" ]; then
    docker-compose up --build -d
    echo "DicePP 已启动"
else
    echo "警告: 未找到 docker-compose.yml"
fi

echo "[3/3] 启动 go-cqhttp..."
if [ -d "$GOCQ_DIR" ] && [ -f "$GOCQ_DIR/docker-compose.yml" ]; then
    cd "$GOCQ_DIR"
    docker-compose up -d
    echo "go-cqhttp 已启动"
else
    echo "警告: 未找到 go-cqhttp 目录或 docker-compose.yml"
fi

echo ""
echo "===== 所有服务已启动 ====="
echo "查看 DicePP 日志: docker logs dicepp_nonebot_bot"
echo "查看 go-cqhttp 日志: docker logs gocqhttp"
