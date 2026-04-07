# DicePP 部署指南

DicePP 是一个基于 NoneBot2 的 TRPG 骰娘机器人。NoneBot2 是一个现代、跨平台、可扩展的 Python 聊天机器人框架，基于 Python 类型注解和异步优先特性开发。

部署 DicePP 需要配合 QQNT（新版 QQ）与 LLOneBot 使用：LLOneBot 作为协议转换层，将 QQ 的消息转换为标准的 OneBot11 协议，与 DicePP 进行通信。

> **核心原则**：无论开发、测试还是部署，项目均通过 `uv`（本地）或 `Docker`（服务器）管理 Python 环境，**不依赖系统中已安装的任何 Python 环境**。

---

## 环境要求

| 场景 | 工具 | 说明 |
|------|------|------|
| Python | Python 3.9+ | NoneBot2 需要 Python 3.9 及以上版本 |
| Windows 本地开发/运行 | [uv](https://github.com/astral.sh/uv) | 替代 pip+venv，一键隔离环境 |
| Linux 服务器部署 | Docker + Docker Compose | 完全容器化，无需手动配 Python |
| QQ 客户端 | QQNT + LLOneBot | 新版 QQ 机器人客户端方案，使用 OneBot11 协议 |
| LLOneBot 部署 | QQNT (本地) 或 Docker (服务器) | 可在本地 Windows 运行或服务器 Docker 部署 |

---

## 部署方式

| 方式 | 适用场景 | 工具 |
|------|----------|------|
| Windows 本地运行 | 开发、测试 | uv + LLOneBot 本地 |
| Linux/WSL Docker 部署 | 生产服务器 | Docker + LLOneBot Docker |

---

## 方式一：Windows 本地运行

适用于本地开发和调试。

### 1. 安装 uv

在 PowerShell 中执行：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

重启终端后验证：`uv --version`

### 2. 克隆项目并安装依赖

```bat
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
scripts\dev\install.bat
```

### 3. 配置 LLOneBot

1. 安装 QQNT 和 LLOneBot 插件
2. 登录机器人 QQ 账号
3. 启用反向 WebSocket，地址：`ws://127.0.0.1:8080/onebot/v11/ws`

### 4. 启动 DicePP

```bat
scripts\dev\run.bat
```

---

## 方式二：Linux/WSL Docker 部署（推荐）

适用于生产服务器，完全容器化部署。

### 前置要求

- Docker Engine
- Docker Compose (V1 或 V2)

安装 Docker：

```bash
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker $USER && newgrp docker
```

### 部署步骤

#### 1. 克隆项目

```bash
mkdir -p ~/deploy && cd ~/deploy
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp
```

#### 2. 安装 LLOneBot

> **注意**: 如果当前用户没有 Docker 权限，需要将用户加入 docker 组或全程使用 `sudo`

```bash
# 方法1：将用户加入 docker 组（推荐，之后无需 sudo）
sudo usermod -aG docker $USER
newgrp docker  # 刷新组权限（或重新登录生效）

# 方法2：全程使用 sudo（如果方法1不可用）
sudo bash scripts/deploy/linux/llonebot/setup.sh
```

正常安装：
```bash
make setup-llbot
# 或: bash scripts/deploy/linux/llonebot/setup.sh
```

脚本会自动：
- 检查 Docker 环境
- 创建 dice-net 网络
- 下载并运行 LLOneBot 官方安装脚本
- 配置网络

安装过程（交互式配置向导）：

```
===== LLOneBot 安装向导 =====

请选择配置方式：
1) 现在配置（命令行配置所有选项）
2) 稍后配置（仅配置 WebUI，其他选项在 WebUI 中配置）
请选择 (1/2): 1

请输入 QQ 号（必填）: 你的QQ号

WebUI 配置：
WebUI 密码（必填，仅支持英文和数字）: 设置密码
WebUI 端口 (默认 3080): 回车使用默认

请选择要启用的协议（可多选）：
1) OneBot 11
2) Milky
3) Satori
输入选项（用空格分隔）: 1

OneBot 11 连接配置：
1) WebSocket 服务端
2) WebSocket 客户端
3) HTTP 服务端
4) WebHook
选择连接类型: 2
WebSocket URL: ws://dicepp:8080/onebot/v11/ws
Token (可选): 留空回车

选择连接类型: 0  (完成配置)

是否启用无头模式 (y/n): y

是否使用 Docker 镜像源 (y/n): y
```

关键配置说明：
- **协议选择**: 必须启用 `OneBot 11`
- **连接类型**: 选择 `WebSocket 客户端`（反向连接）
- **WebSocket URL**: 填写 `ws://dicepp:8080/onebot/v11/ws`（使用容器名，非 IP）
- **无头模式**: 推荐 `y`（省内存），如遇掉线问题可改用有头模式

安装完成后：
1. 查看日志获取二维码: `make llbot-logs` 或访问 `http://服务器IP:3080`
2. 扫码登录 QQ（复制日志中的二维码网址到浏览器，用手机 QQ 扫描）
3. 登录成功后 DicePP 才能正常连接

#### 3. 部署 DicePP

```bash
make deploy
# 或: bash scripts/deploy/linux/setup.sh
# 如需 sudo: sudo bash scripts/deploy/linux/setup.sh
```

如果提示网络不存在，先确认 `dice-net` 已创建：
```bash
docker network ls  # 查看网络列表
docker network create dice-net  # 如不存在则手动创建
```

### 日常操作

```bash
# DicePP 控制
make start           # 启动
make stop            # 停止
make restart         # 重启
make logs            # 查看日志（实时）
make update          # 更新代码并重启
make status          # 查看状态

# LLOneBot 控制
make llbot-start     # 启动
make llbot-stop      # 停止
make llbot-restart   # 重启
make llbot-logs      # 查看日志

# 全部服务
make start-all       # 启动全部
make stop-all        # 停止全部

# 帮助
make help            # 显示所有命令
```

---

## 架构说明

```
~/deploy/
├── llonebot/                    # LLOneBot (独立目录)
│   ├── docker-compose.yaml
│   └── data/
│
└── nonebot-dicepp/              # DicePP 项目
    ├── docker-compose.yml
    ├── Dockerfile
    └── src/plugins/DicePP/Data/ # 数据目录 (bind mount)

┌─────────────────────────────────────────────────┐
│                  dice-net 网络                   │
│   llonebot ──(ws://dicepp:8080)──▶ dicepp      │
└─────────────────────────────────────────────────┘
```

### 数据持久化

`src/plugins/DicePP/Data/` 目录通过 bind mount 挂载到宿主机：

| 目录 | 说明 |
|------|------|
| `Config/` | xlsx 配置文件，可人工编辑 |
| `Bot/` | 运行时数据，Bot 自动生成 |
| `DeckData/` | 牌组数据 |
| `QueryData/` | 查询数据 |
| `RandomGenData/` | 随机生成器数据 |

---

## 配置说明

### 环境变量 (.env)

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| HOST | 监听地址 | 0.0.0.0 |
| PORT | 监听端口 | 8080 |
| ACCESS_TOKEN | 访问令牌（可选） | - |
| WEBCHAT_ENABLED | 启用 Web Chat 反向 WS（Standalone） | false |
| WEBCHAT_HUB_URL | Web Chat 网关地址（建议 `wss://.../ws/bot/`） | - |
| WEBCHAT_API_KEY | Web Chat 鉴权密钥 | - |

> 启用 Web Chat 的生产环境请使用 `wss://` 并保持 TLS 证书校验开启；不要在日志中输出 API Key。  
> 运维监控建议以 WebSocket 协议层 ping/pong 为主指标；若网页端暂存应用层 JSON ping，请将其视为过渡信号而非权威心跳。

#### 应用根目录覆盖（一般无需配置）

| 变量名 | 说明 |
|--------|------|
| `DICEPP_APP_DIR` | 显式指定 **应用根目录**（其下为 `Data/` 等）。本地跑 **pytest** 时，测试框架会将其指向**临时目录**，避免写入仓库内 `src/plugins/DicePP/Data`。生产环境请勿随意设置，除非你刻意要把数据目录迁到别处。 |

### LLOneBot 配置

在 LLOneBot WebUI 中配置：

1. 启用 **OneBot11 协议**
2. 启用 **反向 WebSocket**
3. 添加地址：`ws://dicepp:8080/onebot/v11/ws`

> **注意**：使用容器名 `dicepp` 而不是 IP 地址

---

## 故障排除

### DicePP 无法启动

```bash
# 检查状态
make status

# 查看日志
make logs
```

### LLOneBot 无法连接 DicePP

1. 确认两个容器都在运行：`docker ps`
2. 确认网络配置：`docker network inspect dice-net`
3. 确认 WebSocket 地址：`ws://dicepp:8080/onebot/v11/ws`

### 验证 DicePP 是否正常工作

在不连接 LLOneBot 的情况下，可以使用内置测试脚本验证 DicePP：

```bash
# 在 DicePP 容器内运行集成测试
docker exec -it dicepp python scripts/test/test_bot.py --host localhost --port 8080
```

测试通过会显示：
```
============================================================
 结果: 5 通过, 0 失败
============================================================

🎉 所有测试通过!
```

也可以使用交互模式手动测试：

```bash
docker exec -it dicepp python scripts/test/test_bot.py -i --host localhost --port 8080
```

### dice-net 网络不存在

```bash
docker network create dice-net
```

### Docker Compose 命令不可用

脚本会自动检测 `docker compose` (V2) 或 `docker-compose` (V1)。

如果两者都不可用，安装 Docker Compose：

```bash
# V2 (推荐，随 Docker Engine 安装)
# 确保 Docker 版本 >= 20.10

# V1 (旧版)
sudo pip install docker-compose
```

### Docker 权限问题

如果提示权限不足：

```bash
# 方法1：将当前用户加入 docker 组（推荐，永久解决）
sudo usermod -aG docker $USER
newgrp docker  # 刷新组权限（或重新登录生效）

# 方法2：使用 sudo 运行（临时）
sudo make deploy
# 或
sudo bash scripts/deploy/linux/setup.sh
```

---

## 相关链接

- [NoneBot2 文档](https://v2.nonebot.dev/)
- [LLOneBot 文档](https://github.com/LLOneBot/LLOneBot)
- [LuckyLilliaBot (Docker)](https://github.com/LLOneBot/LuckyLilliaBot)
- [uv 文档](https://docs.astral.sh/uv/)