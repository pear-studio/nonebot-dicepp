# LLOneBot 配置说明

LLOneBot 是基于 NTQQ 的 QQ 机器人框架，作为 DicePP 和 QQ 之间的协议转换层。

## 自动安装

运行安装向导脚本：

```bash
bash scripts/deploy/linux/llonebot/setup.sh
```

脚本会自动：
1. 检查 Docker 环境
2. 创建 dice-net 网络
3. 下载并运行 LLOneBot 官方安装脚本
4. 配置网络（部分需手动）

## 手动安装

如果自动安装失败，可以手动操作：

### 1. 创建网络

```bash
docker network create dice-net
```

### 2. 创建目录

```bash
mkdir -p ~/deploy/llonebot
cd ~/deploy/llonebot
```

### 3. 下载官方脚本

```bash
curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/LLOneBot/LuckyLilliaBot/refs/heads/main/script/install-llbot-docker.sh -o llbot-docker.sh
chmod u+x ./llbot-docker.sh
./llbot-docker.sh
```

### 4. 配置网络

编辑 `docker-compose.yaml`，添加网络配置：

```yaml
services:
  llonebot:  # 服务名可能不同
    # ... 其他配置 ...
    networks:
      - dice-net

networks:
  dice-net:
    external: true
```

### 5. 启动

```bash
docker compose up -d
```

## 配置反向 WebSocket

1. 访问 WebUI: http://localhost:3080
2. 登录后进入设置
3. 启用 **OneBot11 协议**
4. 启用 **反向 WebSocket**
5. 添加反向 WebSocket 地址：

```
ws://dicepp:8080/onebot/v11/ws
```

> **注意**: 使用容器名 `dicepp` 而不是 `localhost` 或 IP 地址

## 常用命令

```bash
# 进入 LLOneBot 目录
cd ~/deploy/llonebot

# 启动
docker compose up -d

# 停止
docker compose down

# 查看日志
docker compose logs -f

# 重启
docker compose restart
```

## 常见问题

### 无法连接到 DicePP

1. 确认 DicePP 容器正在运行: `docker ps | grep dicepp`
2. 确认两个容器在同一网络: `docker network inspect dice-net`
3. 确认 WebSocket 地址正确: `ws://dicepp:8080/onebot/v11/ws`

### 扫码登录失败

1. 查看日志获取二维码: `docker compose logs -f`
2. 或使用 WebUI 登录: http://localhost:3080

### 网络不通

确认 dice-net 网络配置正确：

```bash
# 检查网络
docker network ls | grep dice-net

# 检查容器是否在网络中
docker network inspect dice-net
```

## 相关链接

- [LLOneBot 官方文档](https://github.com/LLOneBot/LLOneBot)
- [LuckyLilliaBot (Docker 部署)](https://github.com/LLOneBot/LuckyLilliaBot)
