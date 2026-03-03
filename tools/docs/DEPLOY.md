# DicePP 部署指南

DicePP 是一个基于 NoneBot2 的 TRPG 骰娘机器人。NoneBot2 是一个现代、跨平台、可扩展的 Python 聊天机器人框架，基于 Python 类型注解和异步优先特性开发。

部署 DicePP 需要配合 QQNT（新版 QQ）与 LLOneBot 使用：LLOneBot 作为协议转换层，将 QQ 的消息转换为标准的 OneBot11 协议，与 DicePP 进行通信。

> **核心原则**：无论开发、测试还是部署，项目均通过 `uv`（本地）或 `Docker`（服务器）管理 Python 环境，**不依赖系统中已安装的任何 Python 环境**。

---

## 环境要求

| 场景 | 工具 | 说明 |
|------|------|------|
| Python | Python 3.9+ | NoneBot2 需要 Python 3.9 及以上版本 |
| Windows 本地开发/运行 | [uv](https://github.com/astral-sh/uv) | 替代 pip+venv，一键隔离环境 |
| Linux 服务器部署 | Docker + Docker Compose | 完全容器化，无需手动配 Python |
| QQ 客户端 | QQNT + LLOneBot | 新版 QQ 机器人客户端方案，使用 OneBot11 协议 |

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

#### 第 3 步：准备 QQNT 与 LLOneBot

> LLOneBot（又称 LLBot）是一个基于 NTQQ 的 QQ 机器人框架，它作为中间层负责与 QQ 客户端通信，并将消息转换为标准协议格式（OneBot11），供机器人框架使用。

1. 下载并安装新版 QQ（QQNT）版本
2. 安装 LLOneBot 插件（用于接收 QQ 消息并转发给 DicePP）

> LLOneBot 支持 OneBot11、Milky、Satori 等协议，DicePP 通过 OneBot11 协议与其通信。

#### 第 4 步：配置 LLOneBot

1. 登录机器人 QQ 账号
2. 打开设置界面，找到 LLOneBot 设置
3. 勾选**启用反向 WebSocket 协议**
4. 添加新的反向 WebSocket 地址：
   ```
   ws://127.0.0.1:8080/onebot/v11/ws
   ```

#### 第 5 步骤：启动 DicePP

在项目根目录执行：

```bat
tools\dev\run.bat
```

等价命令：`uv run python bot.py`

当看到类似以下输出时，表示 DicePP 启动成功：
```
成功读取本地化文件
...
Running on http://127.0.0.1:8080
```

#### 第 6 步：确认连接

确保：
1. QQNT 已登录机器人账号
2. LLOneBot 反向 WebSocket 已正确配置
3. DicePP 已启动

保持 QQNT 和 DicePP 两个窗口运行，骰娘即可正常工作。

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
# ACCESS_TOKEN=your_token   # 可选：访问令牌
```

#### 第 3 步：构建并启动 DicePP

```bash
docker compose up --build -d
```

确认启动成功：

```bash
docker compose logs -f bot
# 看到 "Running on http://0.0.0.0:8080" 即表示正常
```

#### 第 4 步：配置 QQNT（Linux）

Linux 服务器部署需要在本地运行 QQNT 并配置 LLOneBot 连接到服务器：

1. 在本地 Windows 机器安装 QQNT
2. 配置 LLOneBot 反向 WebSocket 地址指向服务器：
   ```
   ws://服务器IP:8080/onebot/v11/ws
   ```
3. 确保服务器 8080 端口已开放

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
├── pyproject.toml            # 统一依赖声明（uv / pip 兼容）
├── Makefile                  # Linux/Mac 快捷命令
├── .env                      # 环境变量（不提交 Git）
├── src/plugins/DicePP/
│   ├── core/                 # 核心框架
│   ├── module/               # 功能模块
│   └── data/                 # 运行时数据（不提交 Git）
└── tools/
    ├── dev/
    │   ├── install.bat       # Windows：一键初始化环境
    │   ├── run.bat           # Windows：启动 Bot
    │   └── test.bat          # Windows：运行测试
    ├── deploy/linux/         # Linux 运维脚本
    ├── templates/            # 配置模板
    └── docs/                 # 本文档所在处
```

---

## 反向代理配置

如果需要通过域名访问 DicePP 或启用 HTTPS，可以配置 Nginx 或 Caddy 反向代理。

### Nginx 配置示例

```nginx
server {
    listen 80;
    server_name dicepp.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    # WebSocket 支持
    location /onebot/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

### Caddy 配置示例

```caddyfile
dicepp.example.com {
    reverse_proxy /onebot/* 127.0.0.1:8080
    reverse_proxy /dpp/* 127.0.0.1:8080
}
```

---

## 故障排除

### Bot 无法连接 LLOneBot

1. 检查 LLOneBot 中反向 WebSocket 地址是否正确
2. Docker 部署时确认端口 8080 已开放
3. 检查防火墙是否放行 8080 端口

### LLOneBot 连接失败

1. 确保 DicePP 已启动
2. 检查 WebSocket 地址是否填写正确（应为 `ws://127.0.0.1:8080/onebot/v11/ws`）
3. 确保没有其他程序占用 8080 端口

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
- [LLOneBot 文档](https://github.com/LLOneBot/LLOneBot)
- [uv 文档](https://docs.astral.sh/uv/)
- [DicePP 项目](https://github.com/pear-studio/nonebot-dicepp)
