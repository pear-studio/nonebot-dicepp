# DicePP 部署指南

DicePP 是一个基于 NoneBot2 的 DND 骰娘机器人，需要配合 go-cqhttp 使用。

## 环境要求

- Python 3.8+
- Docker (Linux 部署)
- go-cqhttp (QQ 机器人客户端)

---

## 部署方式

### 方式一：Windows 直接运行

适用于本地测试或不需要 Docker 的环境。

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

或运行提供的脚本：
```batch
.\tools\deploy\windows\install_deps.bat
```

#### 2. 配置 go-cqhttp

1. 从 [go-cqhttp releases](https://github.com/Mrs4s/go-cqhttp/releases) 下载 Windows 版本
2. 将压缩包内容解压到项目根目录的 `go-cqhttp` 文件夹
3. 复制 `tools\templates\config.gocqhttp.yml` 为 `go-cqhttp\config.yml`
4. 编辑 `config.yml`，修改 `uin` 为你的 QQ 号

#### 3. 启动 go-cqhttp

运行 go-cqhttp，首次会弹出二维码，使用需要绑定的 QQ 扫描登录。

#### 4. 启动 DicePP

```batch
python bot.py
```

或使用提供的脚本：
```batch
.\tools\deploy\windows\start.bat
```

---

### 方式二：Docker 部署 (Linux)

适用于服务器环境。

#### 1. 安装 Docker

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | bash

# 安装 Docker Compose
apt-get install docker-compose
```

#### 2. 配置环境变量

复制 `.env.linux` 为 `.env` 并根据需要修改配置：

```env
HOST=0.0.0.0
PORT=8080
```

#### 3. 配置 go-cqhttp

1. 下载 go-cqhttp Linux 版本到 `go-cqhttp` 文件夹
2. 复制 `tools\templates\config.gocqhttp.yml` 为 `go-cqhttp/config.yml`
3. 编辑 `config.yml`，修改 `uin` 为你的 QQ 号

#### 4. 创建 Docker 网络

```bash
docker network create dice-net
```

#### 5. 启动服务

```bash
# 启动 DicePP
docker-compose up --build -d

# 或使用提供的脚本
bash tools/deploy/linux/start.sh
```

#### 6. 启动 go-cqhttp

```bash
cd go-cqhttp
docker-compose up -d
```

---

## 配置说明

### 配置文件位置

- **Linux**: `src/plugins/DicePP/data/`
- **Windows**: 启动后会自动创建

### 主要配置项

| 配置项 | 说明 |
|--------|------|
| BOT_ADMIN | 机器人管理员 QQ 号 |
| SUPER_USERS | 超级用户 QQ 号列表 |
| NICKNAME | 机器人昵称 |

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| HOST | 监听地址 | 0.0.0.0 |
| PORT | 监听端口 | 8080 |
| SECRET | API 密钥 | - |
| ACCESS_TOKEN | 访问令牌 | - |

---

## 常用操作

### 查看日志

**Linux (Docker)**:
```bash
docker logs dicepp_nonebot_bot
# 或使用脚本
bash tools/deploy/linux/logs.sh
```

**Windows**:
直接在终端查看输出

### 重启服务

**Linux**:
```bash
docker-compose restart
# 或使用脚本
bash tools/deploy/linux/restart.sh
```

**Windows**:
停止并重新运行 `start.bat`

### 更新代码

```bash
git pull
docker-compose restart
```

---

## 目录结构

```
nonebot-dicepp/
├── bot.py                 # 主入口文件
├── Dockerfile             # Docker 镜像配置
├── docker-compose.yml     # Docker Compose 配置
├── requirements.txt       # Python 依赖
├── .env                   # 环境变量配置
├── src/
│   └── plugins/
│       └── DicePP/
│           ├── core/      # 核心模块
│           ├── module/    # 功能模块
│           └── data/      # 数据目录
├── tools/
│   ├── deploy/
│   │   ├── linux/        # Linux 部署脚本
│   │   └── windows/      # Windows 部署脚本
│   └── templates/        # 配置模板
└── go-cqhttp/            # go-cqhttp 目录 (需手动创建)
```

---

## 故障排除

### 连接失败

1. 检查 go-cqhttp 配置中的 `universal` 地址是否正确
2. 确认 Docker 容器网络配置正确
3. 检查防火墙是否允许相应端口

### 扫码登录失败

1. 确保 QQ 可以登录网页版 QQ
2. 尝试使用密码登录而非扫码

### 权限问题

1. Linux 下确保数据目录有正确权限
2. Windows 下以管理员身份运行

---

## 相关链接

- [NoneBot2 文档](https://v2.nonebot.dev/)
- [go-cqhttp](https://github.com/Mrs4s/go-cqhttp)
- [DicePP 项目](https://gitee.com/pear_studio/nonebot-dicepp)
