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

### 关于 `cryptography` 依赖

项目依赖 `cryptography>=42.0`（用于 Persona AI 的 API Key 加密）。该库在大多数常见平台上有预编译 wheel，通常无需额外工具链即可直接安装：

- **Windows / Linux x86_64 / macOS**：`uv pip install` 或 Docker 构建时会自动下载预编译包。
- **ARM 设备（如树莓派）或 Alpine Linux**：若预编译 wheel 不可用，需要本地 Rust 编译工具链，或手动安装系统预编译包（如 `apk add py3-cryptography` 后跳过 pip 安装）。
- **Docker 部署**：官方镜像基于常规 Linux 发行版，构建时已处理该依赖，通常无需额外操作。

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

## 方式一（替代）：Linux 服务器本地开发

适用于在 Linux 服务器上进行开发。与 Docker 部署不同，此方式直接在宿主机运行 Python，便于快速迭代和调试。

### 1. 安装 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# 将 uv 加入 PATH（当前会话）
export PATH="$HOME/.local/bin:$PATH"
# 验证
uv --version
```

> 建议将 `export PATH="$HOME/.local/bin:$PATH"` 写入 `~/.bashrc`，避免每次手动添加。

### 2. 克隆项目并安装依赖

```bash
git clone https://github.com/pear-studio/nonebot-dicepp.git
cd nonebot-dicepp

# 安装运行时依赖
make install
# 或: uv sync

# 安装开发依赖（含 pytest、pytest-cov）
make install-dev
# 或: uv sync --group dev
```

`uv sync` 会自动创建 `.venv` 虚拟环境并安装依赖。`uv.lock` 锁定文件确保所有环境依赖版本一致。

### 3. 配置环境

复制环境变量模板并调整端口（避免与生产环境冲突）：

```bash
cp .env .env.dev
# 编辑 .env.dev，将 PORT 改为 8081
```

### 4. 运行测试验证

```bash
make test
# 或: uv run pytest

# 运行指定模块
uv run pytest tests/module/roll/ -v
```

### 5. 开发工作流（Git Worktree）

如果 `master` 分支正在运行生产机器人，建议使用 **Git Worktree** 隔离开发空间：

```bash
# 在主仓库创建 dev 分支和 worktree
git branch dev
git worktree add /home/ubuntu/dev/dicepp dev

# 进入开发目录
cd /home/ubuntu/dev/dicepp

# 复用主仓库的虚拟环境和密钥
ln -s /home/ubuntu/nonebot-dicepp/.venv .venv
ln -s /home/ubuntu/nonebot-dicepp/config/secrets.json config/secrets.json

# 配置开发环境变量
cp .env .env.dev
# 修改 PORT 为 8081
```

worktree 与主仓库共用同一个 `.git`，但代码文件完全隔离：
- 主仓库 `master`：运行生产机器人，保持干净
- worktree `dev`：开发、测试，修改不影响生产

### 6. 启动 Bot（开发模式）

```bash
# 使用开发配置
uv run python bot.py
```

> 注意：开发模式启动 Bot 前，确保 LLOneBot 的反向 WebSocket 地址指向开发端口（如 `ws://服务器IP:8081/onebot/v11/ws`），或临时断开 LLOneBot 避免消息误发到生产实例。

---

## 方式二：Linux/WSL Docker 部署（推荐）

适用于生产服务器，完全容器化部署。

### 前置要求

- Docker Engine
- Docker Compose V2 (推荐) 或 V1

安装 Docker：

```bash
curl -fsSL https://get.docker.com | bash
sudo usermod -aG docker $USER && newgrp docker
```

安装 Docker Compose V2：

```bash
# 创建插件目录
mkdir -p ~/.docker/cli-plugins

# 下载 V2 插件（国内可用 ghfast.top 等代理加速）
curl -SL https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose

# 或国内加速
curl -SL https://ghfast.top/https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose

# 添加执行权限
chmod +x ~/.docker/cli-plugins/docker-compose

# 验证安装
docker compose version
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

#### Docker 部署环境

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
```

#### 本地开发环境

```bash
# 环境管理
make install         # 安装运行时依赖（uv sync）
make install-dev     # 安装开发依赖（uv sync --group dev）
make clean           # 清理临时文件

# 测试
make test            # 运行全部测试
make test-cov        # 运行测试（带覆盖率报告）

# 运行
make run             # 本地启动 Bot
uv run pytest        # 直接运行 pytest
uv run python bot.py # 直接启动 Bot

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

### 配置 Persona AI（可选）

如需启用 AI 对话功能，需配置 LLM API：

**1. 编辑 `config/global.json`：**
```json
"persona_ai": {
  "enabled": true,
  "character_name": "default",
  "character_path": "./content/characters",
  "primary_base_url": "https://api.minimaxi.com/v1",
  "primary_model": "MiniMax-M2.7",
  "max_concurrent_requests": 2,
  "timeout": 30,
  "daily_limit": 20
}
```

**2. 编辑 `config/secrets.json`：**
```json
{
  "persona_ai": {
    "primary_api_key": "your-api-key-here"
  }
}
```

**⚠️ 重要**：`secrets.json` 与 `global.json` 是**深度合并**，只需在 `secrets.json` 中放置敏感字段（如 API key），其他配置保留在 `global.json` 中即可。

**支持的模型**（MiniMax）：
- `MiniMax-M2.7` - 标准版
- `MiniMax-M2.7-highspeed` - 高速版

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

如果两者都不可用，安装 Docker Compose V2：

```bash
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version
```

国内服务器如果下载慢，可使用代理：

```bash
curl -SL https://ghfast.top/https://github.com/docker/compose/releases/download/v2.34.0/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose
```

或使用旧版 V1：

```bash
pip install docker-compose
```

### 故障排除

#### 404 Not Found
**症状**：API 返回 404
**原因**：`base_url` 配置错误
**解决**：使用 `https://api.minimaxi.com/v1`，不要加 `/anthropic`

#### 400 Bad Request - invalid chat setting (2013)
**症状**：`invalid params, invalid chat setting (2013)`
**原因**：MiniMax 不支持多条 system 消息
**解决**：代码已修复，如仍报错请重新构建镜像：`docker compose build`

#### 回复包含 `<think>` 标签
**症状**：回复中显示思考过程
**原因**：MiniMax-M2.7 模型的思维链输出
**解决**：代码已自动过滤 `<think>...</think>` 标签

#### 配置未生效
**症状**：修改配置后行为未改变
**原因**：Docker 使用镜像中的旧代码
**解决**：
```bash
docker compose down
docker compose build
docker compose up -d
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