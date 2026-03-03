#!/bin/bash

echo "===== DicePP 状态检查 ====="
echo ""

echo "--- Docker 容器状态 ---"
docker ps -a --filter "name=dicepp" --filter "name=gocqhttp"

echo ""
echo "--- DicePP 容器日志 (最近 20 行) ---"
docker logs dicepp_nonebot_bot --tail=20 2>&1 | tail -20

echo ""
echo "--- Docker 网络 ---"
docker network ls | grep dice

echo ""
echo "--- 端口监听 ---"
netstat -tlnp 2>/dev/null | grep -E "8080|9000" || ss -tlnp 2>/dev/null | grep -E "8080|9000"

echo ""
echo "===== 检查完成 ====="
