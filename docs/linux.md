# Linux 部署

本页面面向想在 Linux 服务器上部署 DicePP 的骰主。

推荐使用 GitHub Release 发布的 Docker 镜像部署。源码构建只作为无法拉取镜像时的备用方案，见本文末尾附录。需要开发时请看 [dev/guide.md](./dev/guide.md)。

## 快速开始

核心流程：

1. 准备 Linux 服务器、Docker 和 Docker Compose。
2. 创建部署目录，下载 Release 附带的 `docker-compose.yml`。
3. 创建 Docker 网络并启动 DicePP。
4. 初始化网页管理面板管理员密码。
5. 配置 LLOneBot，让 QQ 机器人账号连接 DicePP。
6. 在网页管理面板中确认机器人状态，并完成账号配置。
7. 在 QQ 中向机器人发送 `.help` 验证。

## 准备

需要：

- 一台 Linux 服务器
- Docker 和 Docker Compose
- 一个可登录的 QQ 机器人账号
- 浏览器，用于访问网页管理面板

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

如果 `docker --version` 有输出，但 `docker compose version` 报错，先看本文末尾“docker compose 不存在”。

## 创建部署目录

```bash
mkdir -p ~/dicepp
cd ~/dicepp
mkdir -p config/bots data content dashboard/data
```

从 [DicePP Releases](https://github.com/pear-studio/nonebot-dicepp/releases) 下载目标版本附带的 `docker-compose.yml`，放到 `~/dicepp/docker-compose.yml`。

默认会拉取 GHCR 镜像：

```text
ghcr.io/pear-studio/nonebot-dicepp:latest
ghcr.io/pear-studio/dicepp-dashboard:latest
```

正式生产建议使用明确版本号，而不是长期使用 `latest`。例如：

```bash
DICEPP_IMAGE_TAG=v3.0.0 docker compose pull
DICEPP_IMAGE_TAG=v3.0.0 docker compose up -d
```

## 无法拉取镜像时使用离线包

如果服务器拉取 GHCR 很慢或失败，可以下载 Release 附带的 Linux 离线包。离线包包含 DicePP 的两个 Docker 镜像、对应版本的 `docker-compose.yml` 和常用文档：

```text
ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z
ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z
```

文件名类似：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
DicePP-v3.0.0-linux-amd64-offline.zip.sha256
```

下面示例用 `v3.0.0`，实际安装时请替换成你要部署的 Release 版本。

### 服务器可以访问 GitHub

在服务器上直接下载：

```bash
cd ~/dicepp
VERSION=v3.0.0
BASE_URL="https://github.com/pear-studio/nonebot-dicepp/releases/download/${VERSION}"

curl -L -O "${BASE_URL}/DicePP-${VERSION}-linux-amd64-offline.zip"
curl -L -O "${BASE_URL}/DicePP-${VERSION}-linux-amd64-offline.zip.sha256"
```

`curl` 正常下载时会显示进度，结束后能看到类似文件：

```bash
ls -lh
```

预期能看到：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
DicePP-v3.0.0-linux-amd64-offline.zip.sha256
```

### 服务器不能访问 GitHub

先在自己的电脑浏览器打开目标 Release 页面，下载这两个文件：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
DicePP-v3.0.0-linux-amd64-offline.zip.sha256
```

然后把文件上传到服务器的 `~/dicepp` 目录。Windows PowerShell、macOS 或 Linux 终端都可以用 `scp`：

```bash
scp DicePP-v3.0.0-linux-amd64-offline.zip* 用户名@服务器IP:~/dicepp/
```

例如服务器 IP 是 `203.0.113.10`，登录用户名是 `ubuntu`：

```bash
scp DicePP-v3.0.0-linux-amd64-offline.zip* ubuntu@203.0.113.10:~/dicepp/
```

如果第一次连接服务器，可能会询问是否信任主机，输入 `yes` 后回车。上传成功后，服务器上执行：

```bash
cd ~/dicepp
ls -lh
```

预期能看到刚上传的三个文件。

### 导入离线镜像

进入部署目录：

```bash
cd ~/dicepp
VERSION=v3.0.0
```

校验下载的 zip：

```bash
sha256sum -c "DicePP-${VERSION}-linux-amd64-offline.zip.sha256"
```

预期输出类似：

```text
DicePP-v3.0.0-linux-amd64-offline.zip: OK
```

如果提示 `sha256sum: command not found`，先安装基础工具；大多数 Debian / Ubuntu 系统默认已经带有。

安装解压工具：

```bash
sudo apt-get update
sudo apt-get install -y unzip zstd
```

解压离线包：

```bash
unzip -o "DicePP-${VERSION}-linux-amd64-offline.zip"
```

解压后会得到一个目录：

```text
DicePP-v3.0.0-linux-amd64-offline/
```

目录中包含：

```text
使用说明.md
docker-compose.yml
manifest.json
checksums.sha256
images/DicePP-v3.0.0-linux-amd64-images.tar.zst
docs/linux.md
docs/configuration.md
docs/persona.md
docs/persona-character-card.md
```

校验离线包内部文件：

```bash
cd "DicePP-${VERSION}-linux-amd64-offline"
sha256sum -c checksums.sha256
```

预期会看到多行 `OK`。

把离线包内的 `docker-compose.yml` 复制到部署目录：

```bash
cp docker-compose.yml ..
cd ..
```

解压镜像：

```bash
zstd -d -f "DicePP-${VERSION}-linux-amd64-offline/images/DicePP-${VERSION}-linux-amd64-images.tar.zst"
```

解压后会得到：

```text
DicePP-v3.0.0-linux-amd64-offline/images/DicePP-v3.0.0-linux-amd64-images.tar
```

导入 Docker：

```bash
docker load -i "DicePP-${VERSION}-linux-amd64-offline/images/DicePP-${VERSION}-linux-amd64-images.tar"
```

预期输出会包含两行 `Loaded image`，类似：

```text
Loaded image: ghcr.io/pear-studio/nonebot-dicepp:v3.0.0
Loaded image: ghcr.io/pear-studio/dicepp-dashboard:v3.0.0
```

确认镜像已经在本机：

```bash
docker image ls | grep dicepp
```

预期能看到：

```text
ghcr.io/pear-studio/nonebot-dicepp    v3.0.0
ghcr.io/pear-studio/dicepp-dashboard  v3.0.0
```

之后启动时必须指定同一个版本，并禁止 Compose 再去联网拉取：

```bash
docker network create dice-net
DICEPP_IMAGE_TAG=${VERSION} docker compose up -d --pull never
```

如果 `docker network create dice-net` 提示网络已经存在，这是正常的，继续执行下一条命令即可。

`docker compose up -d --pull never` 成功时会看到类似：

```text
Container dicepp-dashboard  Started
Container dicepp            Started
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

## 初始化网页管理面板

首次启动前，先在部署目录通过命令行设置管理员密码：

```bash
docker compose run --rm --no-deps dashboard python -m dashboard admin init
```

输入内容不会显示在终端中；按提示输入两次即可。Linux 不允许通过网页初始化，以免尚未设置密码的面板被公网访问者抢先初始化。

然后启动服务，并通过浏览器访问：

```text
http://服务器IP:4090/dashboard
```

网页管理面板用于配置编辑、运行状态查看、日志、数据浏览和存档管理。具体功能进入页面后按需要使用即可。

如果从外网访问，直接使用 HTTP 会暴露登录密码和会话信息，建议配置反向代理并开启 HTTPS。

## 配置 LLOneBot

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

## 配置机器人账号

推荐流程是：先启动 DicePP，等 LLOneBot 连接上来后，让 DicePP 根据机器人 QQ 号生成账号配置，再回到网页管理面板填写主人、昵称等常用配置。

这个自动生成依赖 `config/bots/_template.json`。如果 Release 包里已经带了这个模板，首次连接后会生成：

```text
config/bots/{机器人QQ号}.json
```

如果没有生成，可以手动创建这个文件。内容可以先写成：

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "DicePP"
}
```

保存后重启 DicePP，或在网页管理面板中保存配置并让机器人重新加载。

更多配置见 [configuration.md](./configuration.md)。

## 验证

登录 QQ 后，给机器人发送：

```text
.help
```

收到帮助信息即基本部署成功。具体群内指令以机器人内置 `.help` 为准。

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

离线更新到指定版本：

```bash
cd ~/dicepp
VERSION=v3.0.1

# 先按“无法拉取镜像时使用离线包”下载、校验、解压并导入新版本镜像
# 完成到 docker load 成功即可

# 再用新版本重建容器。config/data/content 挂载目录不会被删除。
DICEPP_IMAGE_TAG=${VERSION} docker compose up -d --pull never
```

如果以后网络恢复，可以从离线部署切回普通镜像更新：

```bash
DICEPP_IMAGE_TAG=v3.0.2 docker compose pull
DICEPP_IMAGE_TAG=v3.0.2 docker compose up -d
```

回滚到旧版本时，如果旧版本镜像还在本机：

```bash
DICEPP_IMAGE_TAG=v3.0.0 docker compose up -d --pull never
```

如果旧版本镜像已经被清理，先重新下载并 `docker load` 旧版本离线包，再执行上面的回滚命令。

如果目标版本的 Release 说明提到部署结构变化，先同步该版本附带的 `docker-compose.yml`，再执行 `pull` 和 `up -d`。

更新或回滚前，建议先在网页管理面板中创建并验证存档。版本风险说明见 [releases/](./releases/)。

### 运行管理

Linux Docker Compose 部署如需让网页管理面板直接管理 Bot 服务，需要在 `dashboard` service 中显式启用 Docker Compose runtime：

```yaml
environment:
  - DICEPP_MANAGER_RUNTIME=docker-compose
  - DICEPP_MANAGER_DOCKER_COMMAND=docker
  - DICEPP_MANAGER_DOCKER_SERVICE=bot
  - DICEPP_MANAGER_DOCKER_CWD=/你的/compose/工作目录
  - DICEPP_MANAGER_DOCKER_TIMEOUT=30
```

启用后，网页管理面板可以执行状态查询、启动、停止、重启和日志读取。它不会替你同步目标 Release 的 `docker-compose.yml` 拓扑；如果目标版本调整了 service、volume、network 或环境变量，仍需先按 Release 说明手动同步 compose。

### 国内拉取镜像很慢

DicePP 官方发布源目前是 GHCR。国内服务器首次拉取镜像可能较慢，但后续更新会复用 Docker 层缓存，普通代码更新通常只需要拉取变化层。

如果 GHCR 无法访问，优先使用 Release 附带的 `DicePP-vX.Y.Z-linux-amd64-offline.zip` 离线包，按前文“无法拉取镜像时使用离线包”导入镜像。

如果你有自己的国内镜像仓库，可以通过完整镜像地址覆盖：

```bash
DICEPP_IMAGE=registry.example.com/your-namespace/nonebot-dicepp:v3.0.0 docker compose pull
DICEPP_IMAGE=registry.example.com/your-namespace/nonebot-dicepp:v3.0.0 docker compose up -d
```

这个方式只替换镜像来源，不会改变 DicePP 配置目录、数据目录或网络配置。

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

优先在网页管理面板中保存配置并让机器人重新加载。手动编辑 JSON 后，可以重启 DicePP：

```bash
docker compose restart
```

如果 JSON 写错，DicePP 可能启动失败。查看日志：

```bash
docker compose logs -f
```

### 想启用 Persona AI

先让 `.help` 正常，再按 [persona.md](./persona.md) 配置 Persona。

## 附录：无法拉取镜像时从源码构建

普通用户不推荐源码构建。只有在 GHCR 长期无法拉取、且你能接受首次构建较慢时，再使用这一方式。

源码构建会在服务器上下载 Python 依赖并生成镜像。即使用国内镜像源，首次构建也可能花较久；后续如果 `pyproject.toml` 和 `uv.lock` 没有变化，Docker 会复用 `.venv` 依赖层，普通源码更新会快很多。

在部署目录外准备源码：

```bash
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
```

构建时可使用国内源：

```bash
APT_MIRROR=mirrors.tuna.tsinghua.edu.cn \
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
docker compose build
```

构建完成后再启动：

```bash
docker compose up -d
```

注意：

- 不要在生产更新时默认执行 `git pull` + 本地构建；正式发布仍以 Release 镜像 tag 为准。
- 源码构建更适合临时避开镜像拉取问题，或给开发者验证镜像构建。
- 如果以后恢复镜像拉取，建议切回发布镜像部署。
