# Linux 部署

本页面向想在 Linux 服务器上部署 DicePP 的普通用户。

这里采用 Docker 镜像部署，不讲源码开发。需要开发时请看 [dev/guide.md](./dev/guide.md)。

## 准备

需要：

- 一台 Linux 服务器
- Docker 和 Docker Compose
- 一个可登录的 QQ 机器人账号

先检查服务器是否已经有 Docker：

```bash
docker --version
docker compose version
```

如果两条命令都能输出版本号，直接进入下一步。

如果提示 `docker: command not found`，安装 Docker：

```bash
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker "$USER"
newgrp docker
```

安装后重新检查：

```bash
docker --version
docker compose version
```

如果 `docker --version` 有输出，但 `docker compose version` 报错，先继续看本文末尾“docker compose 不存在”。

## 创建部署目录

```bash
mkdir -p ~/dicepp
cd ~/dicepp
mkdir -p config/bots data content
```

从 [DicePP 最新 Release](https://github.com/pear-studio/nonebot-dicepp/releases/latest) 下载 `docker-compose.yml`，放到 `~/dicepp/docker-compose.yml`。

## 配置账号

推荐流程是：先启动 DicePP，等 LLOneBot 连接上来后，让 DicePP 根据机器人 QQ 号生成账号配置，再回来填写 master 和昵称。

这个自动生成依赖 `config/bots/_template.json`。如果 release 包里已经带了这个模板，首次连接后会生成：

```text
config/bots/{机器人QQ号}.json
```

如果没有生成，就手动创建这个文件。内容可以先写成：

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "骰娘"
}
```

更多配置见 [configuration.md](./configuration.md)。

保存后重启 DicePP：

```bash
docker compose restart
```

## 启动 DicePP

先创建 Docker 网络：

```bash
docker network create dice-net
```

`docker-compose.yml` 使用的是外部网络 `dice-net`。Docker Compose 不会自动创建 `external: true` 的网络，所以这里需要手动创建一次。

如果提示 `Error response from daemon: network with name dice-net already exists`，说明已经创建过，继续下一步即可。

启动 DicePP：

```bash
docker compose up -d
```

查看日志：

```bash
docker compose logs -f
```

## 安装并配置 LLOneBot

LLOneBot 可以单独部署在同一台服务器上，并和 DicePP 放在同一个 Docker 网络中。

按 LLOneBot 官方文档安装 Docker 版本即可：

- [LLBot 文档站](https://luckylillia.com)
- [LuckyLilliaBot GitHub](https://github.com/LLOneBot/LuckyLilliaBot)

关键是让 LLOneBot 容器加入 `dice-net` 网络，这样它才能通过容器名访问 DicePP。

向导里的关键选择：

| 项目 | 选择或填写 |
|------|------------|
| 协议 | OneBot 11 |
| 连接方式 | WebSocket 客户端 |
| WebSocket URL | `ws://dicepp:8080/onebot/v11/ws` |
| Token | 留空，除非你额外配置了访问令牌 |
| 无头模式 | 推荐开启 |

容器部署时 URL 使用 `dicepp`，不要写 `127.0.0.1`。

登录 QQ 后，给机器人发送：

```text
.help
```

收到帮助信息即部署成功。

## 日常操作

在 `~/dicepp` 目录下：

```bash
docker compose up -d
docker compose down
docker compose restart
docker compose logs -f
```

更新到最新镜像：

```bash
docker compose pull
docker compose up -d
```

更新到指定版本：

```bash
DICEPP_IMAGE_TAG=v3.0.0 docker compose pull
DICEPP_IMAGE_TAG=v3.0.0 docker compose up -d
```

版本风险说明见 [releases/](./releases/)。

## 常见问题

### docker compose 不存在

现象：

```text
docker: 'compose' is not a docker command
```

或：

```text
docker compose version
```

没有输出版本号。

处理：

```bash
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 \
  -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version
```

如果下载 GitHub 很慢，可换用服务器所在地区可访问的镜像源，或按 Docker 官方文档安装 Compose V2。

### Docker 权限不足

现象：

```text
permission denied while trying to connect to the Docker daemon socket
```

处理：

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker ps
```

如果还是不行，退出 SSH 后重新登录再试。

### dice-net 已存在

现象：

```text
Error response from daemon: network with name dice-net already exists
```

这是正常的，说明网络已经创建过，继续启动 DicePP 即可。

### LLOneBot 无法连接

常见现象：

- LLOneBot 日志反复出现 WebSocket 连接失败
- DicePP 日志没有收到连接
- QQ 发 `.help` 没反应

检查：

- DicePP 容器是否叫 `dicepp`
- DicePP 和 LLOneBot 是否都在 `dice-net` 网络中
- LLOneBot URL 是否是 `ws://dicepp:8080/onebot/v11/ws`

可用命令：

```bash
docker ps
docker network inspect dice-net
docker compose logs -f
```

### 修改配置后没有生效

现象：

- 改了 JSON，但机器人行为没变化
- Persona 仍然使用旧角色或旧配置

重启 DicePP：

```bash
docker compose restart
```

如果 JSON 写错，DicePP 可能启动失败。查看日志：

```bash
docker compose logs -f
```

### 想启用 Persona AI

先让 `.help` 正常，再按 [persona.md](./persona.md) 配置 Persona。
