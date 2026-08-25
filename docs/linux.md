# Linux 部署

Linux 版本使用一个 DicePP 容器同时运行 Dashboard 和 Bot。Dashboard 进程持有唯一的 Bot controller；没有独立服务、4091 端口、Docker Socket 或后台控制 token。

## 快速开始

准备 Docker Compose 和一个外部 `dice-net` 网络：

```bash
docker network create dice-net 2>/dev/null || true
mkdir -p config/bots data/backups content dashboard/data
curl -fsSLO https://raw.githubusercontent.com/pear-studio/nonebot-dicepp/main/docker-compose.yml
DICEPP_IMAGE_TAG=latest docker compose pull
DICEPP_IMAGE_TAG=latest docker compose up -d
```

Dashboard 地址是 `http://127.0.0.1:4090/dashboard`。NapCat/LLOneBot 从同一个 `dice-net` 网络连接：

```text
ws://dicepp:8080/onebot/v11/ws
```

Compose 会把配置、业务数据、内容、Dashboard 数据和存档库存挂载为可写目录：

```text
config/             配置
data/               Bot 运行数据库与日志
data/backups/       Dashboard 存档库存
content/            内容资源
dashboard/data/     Dashboard 管理数据库
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f dicepp
curl -fsS http://127.0.0.1:4090/api/health
```

## 首次配置

打开 Dashboard 前，先在唯一的 `dicepp` service 中初始化管理员密码：

```bash
docker compose run --rm --no-deps dicepp python -m dashboard admin init
```

然后打开 Dashboard 添加或编辑 Bot 配置，再按页面提示重启 Bot。配置保存只报告 `restart_required`，不会启动隐藏的配置热重载通道。

创建存档、清空业务数据和导入空实例前，必须在 Dashboard 停止 Bot。导入目标必须是业务数据为空的实例。管理数据库、Dashboard session、日志和程序文件不会被清空。

## 手工更新

Linux 更新是手工的：

```bash
docker compose down
cp docker-compose.yml docker-compose.yml.bak
curl -fsSLO https://raw.githubusercontent.com/pear-studio/nonebot-dicepp/<TAG>/docker-compose.yml
DICEPP_IMAGE_TAG=<TAG> docker compose pull
DICEPP_IMAGE_TAG=<TAG> docker compose up -d
```

如果新版本需要数据迁移，Dashboard 会在启动或首次访问时执行当前 schema migration。更新前请导出重要存档；不要在 Bot 运行时直接覆盖 `config/`、`data/` 或 `content/`。出现问题时停止容器，保留日志和目录，按目标版本说明手工处理，不依赖自动回滚。

## 构建本地镜像

需要从源码构建时：

```bash
docker build -f Dockerfile -t dicepp:local .
```

这是源码构建验证命令；正式部署仍使用上面的 GHCR 镜像 Compose 文件。

镜像启动命令是 `python -m dashboard`，它会在正常入口中启动 Dashboard 并 auto-start 同目录的 `bot.py` 子进程；导入 Python 模块或测试不会隐式启动 Bot。
