#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "===== DicePP 部署脚本 ====="
echo "项目目录: $PROJECT_DIR"

cd "$PROJECT_DIR"

if [ ! -f "docker-compose.yml" ]; then
    echo "错误: 未找到 docker-compose.yml 文件"
    exit 1
fi

echo "正在构建并启动 DicePP 容器..."
docker-compose up --build -d

echo ""
echo "===== 部署完成 ====="
echo "查看日志: docker logs dicepp_nonebot_bot"
echo "停止服务: docker-compose down"
