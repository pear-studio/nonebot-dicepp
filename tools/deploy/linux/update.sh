#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "正在更新 DicePP 代码..."
git pull

echo ""
echo "正在重启服务以应用更新..."
docker-compose restart

echo "===== 更新完成 ====="
