# Linux 部署

本页面面向想在 Linux 服务器上部署 DicePP 的骰主。

推荐使用 GitHub Release 发布的 Docker 镜像部署。源码构建只作为无法拉取镜像时的备用方案，见本文末尾附录。需要开发时请看 [dev/guide.md](./dev/guide.md)。

## 快速开始

核心流程：

1. 准备 Linux 服务器、Docker 和 Docker Compose。
2. 创建部署目录，下载 Release 附带的 `docker-compose.yml`。
3. 创建 Docker 网络并启动 Bot、Dashboard、Manager 三个标准服务。
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
mkdir -p config/bots data content dashboard/data manager/{state,packages,backups}
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

### 标准三服务拓扑

当前标准 `docker-compose.yml` 包含：

| Service | 作用 | 宿主机端口 |
|---|---|---|
| `bot` | 承载一个可以包含多个 QQ 账号的 RuntimeUnit | 不映射；在 `dice-net` 暴露 `8080` |
| `dashboard` | 用户登录、配置和操作界面 | `4090` |
| `manager` | RuntimeUnit 生命周期、operation 和维护操作 | 不映射；只在内部网络暴露 `4091` |

Dashboard 通过 `http://manager:4091` 调用 Manager。Manager 首次启动会在 `manager/state/api-token` 生成内部 API token，Dashboard 只读挂载同一文件。不要把 `4091` 映射到公网。

只有 Manager 挂载 `/var/run/docker.sock`。它仅执行固定的状态、启动、停止、重启和日志操作，并且只接受带有匹配 DicePP managed、RuntimeUnit 和 deployment schema 标签的 Bot 容器。Dashboard 不挂载 Docker Socket，也不直接控制容器。

目录所有权如下：

```text
~/dicepp/
├─ config/            # DicePP 配置
├─ data/              # 运行数据
├─ content/           # 用户内容
├─ dashboard/data/    # Dashboard 账号与会话
└─ manager/
   ├─ state/          # token、operation store、维护状态
   ├─ packages/       # 后续版本下载缓存
   └─ backups/        # 后续事务安全归档
```

Manager 对 `config/`、`data/`、`content/` 和 `manager/` 读写；Dashboard 只保留配置编辑、业务数据读取、Dashboard 本地状态写入以及 Manager token 只读权限。

## 无法拉取镜像时使用离线包

如果服务器拉取 GHCR 很慢或失败，可以下载 Release 附带的 Linux 离线包。离线包包含 DicePP 的两个 Docker 镜像、对应版本的三服务 `docker-compose.yml` 和常用文档。Manager 与 Dashboard 复用 Dashboard 镜像，因此不需要第三个镜像：

```text
ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z
ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z
```

文件名类似：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
```

下面示例用 `v3.0.0`，实际安装时请替换成你要部署的 Release 版本。

### 服务器可以访问 GitHub

在服务器上直接下载：

```bash
cd ~/dicepp
VERSION=v3.0.0
BASE_URL="https://github.com/pear-studio/nonebot-dicepp/releases/download/${VERSION}"

curl -L -O "${BASE_URL}/DicePP-${VERSION}-linux-amd64-offline.zip"
```

`curl` 正常下载时会显示进度，结束后能看到类似文件：

```bash
ls -lh
```

预期能看到：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
```

如果你想记录下载文件的校验值，可以执行：

```bash
sha256sum "DicePP-${VERSION}-linux-amd64-offline.zip"
```

预期会输出一行类似：

```text
780f7a2b40e9e121eded75ba0dd35cfa79c9167a437855c6230ab4c0e3f95791  DicePP-v3.0.0-linux-amd64-offline.zip
```

GitHub Release asset 本身也会记录 digest；如果你安装了 GitHub CLI，可以这样查看：

```bash
gh release view "${VERSION}" \
  --repo pear-studio/nonebot-dicepp \
  --json assets \
  --jq '.assets[] | select(.name == "DicePP-'${VERSION}'-linux-amd64-offline.zip") | .digest'
```

### 服务器不能访问 GitHub

先在自己的电脑浏览器打开目标 Release 页面，下载这个文件：

```text
DicePP-v3.0.0-linux-amd64-offline.zip
```

然后把文件上传到服务器的 `~/dicepp` 目录。Windows PowerShell、macOS 或 Linux 终端都可以用 `scp`：

```bash
scp DicePP-v3.0.0-linux-amd64-offline.zip 用户名@服务器IP:~/dicepp/
```

例如服务器 IP 是 `203.0.113.10`，登录用户名是 `ubuntu`：

```bash
scp DicePP-v3.0.0-linux-amd64-offline.zip ubuntu@203.0.113.10:~/dicepp/
```

如果第一次连接服务器，可能会询问是否信任主机，输入 `yes` 后回车。上传成功后，服务器上执行：

```bash
cd ~/dicepp
ls -lh
```

预期能看到刚上传的离线包。

### 导入离线镜像

进入部署目录：

```bash
cd ~/dicepp
VERSION=v3.0.0
```

可选：记录下载的 zip 校验值，方便与 GitHub Release asset digest 或你本地留存的校验值对照：

```bash
sha256sum "DicePP-${VERSION}-linux-amd64-offline.zip"
```

预期输出类似：

```text
780f7a2b40e9e121eded75ba0dd35cfa79c9167a437855c6230ab4c0e3f95791  DicePP-v3.0.0-linux-amd64-offline.zip
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
Container dicepp-manager    Started
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

## 配置 NapCat（推荐）

当前推荐使用 NapCat 作为 QQ 协议端。NapCat 也是基于 OneBot v11 协议，与 DicePP 通过 `dice-net` Docker 网络通信。

NapCat 独立部署在 DicePP 以外的目录（例如 `~/dicepp/napcat/`），不耦合到 DicePP 的 `docker-compose.yml`。

### 在线部署

如果服务器能访问 GitHub 和腾讯 CDN：

```bash
mkdir -p ~/dicepp/napcat && cd ~/dicepp/napcat

# 下载 NapCat 框架（约 28MB）
NAPCAT_VER=v4.18.9
curl -L -o NapCat.Shell.zip \
  "https://github.com/NapNeko/NapCatQQ/releases/download/${NAPCAT_VER}/NapCat.Shell.zip"

# 下载模板文件（docker-compose.yml, Dockerfile, entrypoint.sh）
# 方式一: 从 DicePP 最新 Release 的整合包中提取 napcat/ 目录
# 方式二: 从 DicePP 仓库 docs/agent/skills-dev/full-offline-bundle/templates/napcat/ 下载
```

然后编辑 `docker-compose.yml`，将 `ACCOUNT` 改为机器人 QQ 号：

```bash
# 用你熟悉的编辑器修改这一行:
#   - ACCOUNT=填写你的QQ号
```

构建并启动：

```bash
docker compose build
docker compose up -d
```

首次启动时，容器日志中会打印二维码，用手机 QQ 扫码登录。也可以浏览器访问 `http://服务器IP:6099/webui` 扫码。

构建说明：

- 首次构建会从腾讯 CDN 下载 QQNT（约 170MB），Docker 层缓存后不再重复下载。
- NapCat.Shell.zip 从本地复制（28MB），全离线。
- 首次启动自动生成 `onebot11.json`，预设连接 `ws://dicepp:8080/onebot/v11/ws`，无需手动配置。

### 离线部署

如果服务器不能访问外网，可以从 DicePP GitHub Release 下载 Linux 整合包（`DicePP-vX.Y.Z-linux-amd64-with-napcat.zip`）。整合包内包含 DicePP 离线镜像和 NapCat 部署文件（含预下载的 NapCat.Shell.zip）。

解压后：

```bash
cd napcat
# 编辑 docker-compose.yml，将 ACCOUNT 改为机器人 QQ 号
docker compose build
docker compose up -d
```

构建过程不需要联网（QQNT .deb 也支持离线：预下载到 napcat/ 目录后改为 COPY）。

### 日常操作

在 `~/dicepp/napcat` 目录下：

```bash
docker compose up -d
docker compose down
docker compose restart
docker compose logs -f
```

更新 NapCat 版本：

```bash
# 下载新版本 Shell.zip，替换旧文件
NAPCAT_VER=v4.x.x
curl -L -o NapCat.Shell.zip \
  "https://github.com/NapNeko/NapCatQQ/releases/download/${NAPCAT_VER}/NapCat.Shell.zip"
docker compose build --no-cache
docker compose up -d
```

### NapCat 无法连接

常见现象：

- NapCat 日志反复出现 WebSocket 连接失败
- DicePP 日志没有收到连接
- QQ 发 `.help` 没反应

检查：

- DicePP 容器是否叫 `dicepp`
- DicePP 和 NapCat 是否都在 `dice-net` 网络中
- NapCat 配置 `onebot11.json` 中 URL 是否是 `ws://dicepp:8080/onebot/v11/ws`

可用命令：

```bash
docker ps
docker network inspect dice-net
docker compose logs -f
```

## 配置机器人账号

推荐流程是：先启动 DicePP，等 NapCat 连接上来后，让 DicePP 根据机器人 QQ 号生成账号配置，再回到网页管理面板填写主人、昵称等常用配置。

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

标准 Compose 已默认启用独立 Manager。网页管理面板通过 Manager 查询 RuntimeUnit 状态、启动、停止、重启和读取日志；这些操作会作用于整个 Bot 容器，包括其中共享进程的所有 QQ 账号。

旧版把 Docker Compose runtime 配在 Dashboard 中的方式不再受支持。升级旧部署时，保留现有 `config/`、`data/`、`content/` 和 `dashboard/data/`，创建 `manager/state`、`manager/packages`、`manager/backups`，然后使用当前 Release 附带的完整三服务 `docker-compose.yml`。不要把 Docker Socket 重新挂回 Dashboard。

如果 Manager 不可用或容器标签、deployment schema 不匹配，Dashboard 会显示运行管理不受支持，不会退回到直接操作 Docker。Manager 不会替你同步目标 Release 的 Compose 拓扑；如果 Release 调整了 service、volume、network 或环境变量，仍需先按 Release 说明手动同步 Compose。

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

如果宿主机命令正常，但 Dashboard 显示 Manager 无法访问 Docker，请检查：

```bash
docker compose logs manager
docker compose config
```

标准 Compose 只把 `/var/run/docker.sock` 挂载给 `manager`。不要通过把 socket 挂到 Dashboard 来绕过错误；应确认 Manager 容器在运行，并检查 socket 挂载和 Bot 的三个 `io.dicepp.*` 标签。

### dice-net 已存在

现象：

```text
Error response from daemon: network with name dice-net already exists
```

这是正常的，说明网络已经创建过，继续启动 DicePP 即可。

### NapCat 无法连接

详见上方「配置 NapCat」一节末尾的检查清单。

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

## 备选方案：LLOneBot

> ⚠️ LLOneBot 在国内网络环境下，其 Docker 镜像（`linyuchen/llbot`、`linyuchen/pmhq`）可能无法拉取。仅推荐在能正常访问 Docker Hub 且成功拉取上述镜像的环境下使用。新用户建议优先使用上方的 NapCat 方案。

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
