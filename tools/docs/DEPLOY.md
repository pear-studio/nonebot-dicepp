# DicePP 部署指南

DicePP 是一个基于 NoneBot2 的 TRPG 骰娘机器人，需要配合 go-cqhttp 使用。

> **核心原则**：无论开发、测试还是部署，项目均通过 `uv`（本地）或 `Docker`（服务器）管理 Python 环境，**不依赖系统中已安装的任何 Python 环境**。

---

## 环境要求

| 场景 | 工具 | 说明 |
|------|------|------|
| Windows 本地开发/运行 | [uv](https://github.com/astral-sh/uv) | 替代 pip+venv，一键隔离环境 |
| Linux 服务器部署 | Docker + Docker Compose | 完全容器化，无需手动配 Python |
| go-cqhttp | go-cqhttp | QQ 机器人客户端，两种场景均需要 |

---

## 部署方式

### 方式一：Windows 本地运行（开发 / 测试）

适用于本地开发和调试，所有依赖隔离在项目目录下的 `.venv/` 中。

#### 前置：安装 uv（仅需一次）

在 **PowerShell** 中执行：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装完成后**重启终端**，验证：

```bat
uv --version
```

---

#### 第 1 步：克隆项目

```bat
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
```

#### 第 2 步：初始化虚拟环境并安装依赖

```bat
tools\dev\install.bat
```

该脚本会自动：
- 在项目根目录创建 `.venv/` 虚拟环境
- 从清华镜像安装所有运行时依赖和测试依赖

> 所有包仅安装在 `.venv/` 内，**不影响系统 Python**。

#### 第 3 步：配置 go-cqhttp

1. 从 [go-cqhttp Releases](https://github.com/Mrs4s/go-cqhttp/releases) 下载 Windows 版本
2. 解压到项目根目录的 `go-cqhttp\` 文件夹
3. 复制配置模板：
   ```bat
   copy tools\templates\config.gocqhttp.yml go-cqhttp\config.yml
   ```
4. 编辑 `go-cqhttp\config.yml`，将 `uin` 改为机器人的 QQ 号，确认 WebSocket 地址为：
   ```yaml
   servers:
     - ws-reverse:
         universal: ws://127.0.0.1:8080/onebot/v11/ws
   ```

#### 第 4 步：启动 go-cqhttp

```bat
cd go-cqhttp
go-cqhttp.exe
```

首次运行会弹出二维码，使用绑定 QQ 扫码登录，成功后保持此窗口运行。

#### 第 5 步：启动 DicePP

新开一个终端窗口，在项目根目录执行：

```bat
tools\dev\run.bat
```

等价命令：`uv run python bot.py`

---

### 方式二：Linux 服务器部署（Docker）

适用于生产服务器，完全容器化，无需在服务器上安装 Python。

#### 前置：安装 Docker

```bash
# 安装 Docker Engine
curl -fsSL https://get.docker.com | bash

# 将当前用户加入 docker 组（免 sudo）
sudo usermod -aG docker $USER && newgrp docker

# 验证
docker --version && docker compose version
```

---

#### 第 1 步：克隆项目

```bash
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
```

#### 第 2 步：配置环境变量

```bash
cp .env.linux .env
```

按需编辑 `.env`：

```env
HOST=0.0.0.0
PORT=8080
# SECRET=your_secret        # 可选：go-cqhttp 通信密钥
# ACCESS_TOKEN=your_token   # 可选：访问令牌
```

#### 第 3 步：配置 go-cqhttp

```bash
mkdir -p go-cqhttp
cp tools/templates/config.gocqhttp.yml go-cqhttp/config.yml
```

编辑 `go-cqhttp/config.yml`，关键字段：

```yaml
account:
  uin: 123456789   # ← 改为机器人 QQ 号

servers:
  - ws-reverse:
      universal: ws://dicepp_nonebot_bot:8080/onebot/v11/ws  # ← 指向容器名
```

#### 第 4 步：构建并启动 DicePP

```bash
docker compose up --build -d
```

确认启动成功：

```bash
docker compose logs -f bot
# 看到 "Running on http://0.0.0.0:8080" 即表示正常
```

#### 第 5 步：启动 go-cqhttp

```bash
cp tools/templates/docker-compose.gocqhttp.yml go-cqhttp/docker-compose.yml
cd go-cqhttp && docker compose up -d
# 首次需扫码：docker compose logs -f 查看二维码
```

---

## 运行测试（Windows）

```bat
tools\dev\test.bat

REM 带覆盖率报告：
uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing
```

---

## 配置说明

### 运行时配置文件位置

- **Linux（Docker）**: volume `dicepp_data` → 容器内 `/app/src/plugins/DicePP/data/`
- **Windows（本地）**: 项目目录 `src/plugins/DicePP/data/`（首次启动自动创建）

### 主要配置项

| 配置项 | 说明 |
|--------|------|
| BOT_ADMIN | 机器人管理员 QQ 号 |
| SUPER_USERS | 超级用户 QQ 号列表 |
| NICKNAME | 机器人昵称 |

### 环境变量（`.env`）

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| HOST | 监听地址 | 0.0.0.0 |
| PORT | 监听端口 | 8080 |
| SECRET | go-cqhttp 通信密钥 | - |
| ACCESS_TOKEN | 访问令牌 | - |
| MAX_WORKERS | 异步 Worker 数量 | 1 |

---

## 常用操作速查

### 查看日志

```bash
# Docker
docker compose logs -f bot
bash tools/deploy/linux/logs.sh
```

### 重启服务

```bash
# Docker
docker compose restart bot
bash tools/deploy/linux/restart.sh
```

### 更新代码并重启

```bash
git pull
docker compose up --build -d
```

### 停止服务

```bash
docker compose down
```

### 查看数据持久化位置（Docker）

```bash
docker volume inspect dicepp_data
```

---

## 目录结构

```
nonebot-dicepp/
├── bot.py                    # 主入口
├── Dockerfile                # 多阶段构建（uv 安装依赖，精简运行镜像）
├── docker-compose.yml        # 容器编排
├── pyproject.toml            # 统一依赖声明（uv / pip / poetry 兼容）
├── requirements.txt          # 备用依赖列表
├── Makefile                  # Linux/Mac 快捷命令
├── .env                      # 环境变量（不提交 Git）
├── src/plugins/DicePP/
│   ├── core/                 # 核心框架
│   ├── module/               # 功能模块
│   └── data/                 # 运行时数据（不提交 Git）
├── tools/
│   ├── dev/
│   │   ├── install.bat       # Windows：一键初始化环境
│   │   ├── run.bat           # Windows：启动 Bot
│   │   └── test.bat          # Windows：运行测试
│   ├── deploy/linux/         # Linux 运维脚本
│   ├── templates/            # 配置模板
│   └── docs/                 # 本文档所在处
└── go-cqhttp/                # go-cqhttp 目录（需手动创建）
```

---

## 故障排除

### Bot 无法连接 go-cqhttp

1. 检查 `go-cqhttp/config.yml` 中 WebSocket 地址是否正确
2. Docker 部署时确认两个容器在同一网络（`docker network ls`）
3. 检查防火墙是否放行 8080 端口

### go-cqhttp 扫码登录失败

1. 确保该 QQ 可正常登录网页版
2. 尝试切换为密码登录方式
3. 删除 `go-cqhttp/session.token` 后重试

### 依赖安装失败（Windows）

- 确认 `uv` 已正确安装：`uv --version`
- 若清华镜像超时，去掉 `--index-url` 参数改用官方源

### Docker 构建失败

```bash
docker compose build --no-cache
```

---

## 相关链接

- [NoneBot2 文档](https://v2.nonebot.dev/)
- [go-cqhttp](https://github.com/Mrs4s/go-cqhttp)
- [uv 文档](https://docs.astral.sh/uv/)
- [DicePP 项目](https://github.com/pear-studio/nonebot-dicepp)
