#!/bin/bash

LINES=${1:-50}

echo "===== DicePP 日志 (最后 $LINES 行) ====="
docker logs dicepp_nonebot_bot --tail=$LINES

echo ""
echo "===== 错误日志 ====="
docker logs dicepp_nonebot_bot --tail=$LINES 2>&1 | grep -i error
