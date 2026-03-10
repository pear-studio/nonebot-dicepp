# nonebot-dicepp

基于 Python 的 DND 骰娘机器人，可作为机器人项目 NoneBot2 的插件使用。

## 功能特性

- 骰子掷骰：支持多种骰子规则和表达式
- 日志系统：记录群内骰子使用记录
- 角色卡管理：管理 DND 角色卡数据
- 群管理：基本的群管理功能
- Karma 系统：记录和管理玩家 Karma 值
- 外部 API：提供 HTTP API 接口供外部调用

## 环境要求

- Python >= 3.10
- NoneBot2 >= 2.4.0
- OneBot V11 适配器

## 安装

### 使用 uv（推荐）

```bash
# 克隆项目
git clone https://github.com/your-repo/nonebot-dicepp.git
cd nonebot-dicepp

# 安装依赖
uv venv .venv
uv pip install .
```

### 使用 pip

```bash
# 克隆项目
git clone https://github.com/your-repo/nonebot-dicepp.git
cd nonebot-dicepp

# 安装依赖
pip install .
```

## 配置

1. 复制环境配置文件：

```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，配置以下选项：

```env
# 服务器配置
HOST=127.0.0.1
PORT=8080

# 安全配置
SECRET=your-secret-key
ACCESS_TOKEN=your-access-token

# 超级用户
# SUPERUSERS=[]
```

## 启动

### 方式一：直接运行

```bash
python bot.py
```

### 方式二：使用 nb-cli

```bash
nb run
```

## Docker 部署

### 构建镜像

```bash
docker build -t dicepp:latest .
```

### 使用 docker-compose

```bash
docker-compose up -d
```

## OneBot 实现

推荐使用以下 OneBot V11 实现：

- **Lagrange**：新一代 Go 语言实现的 OneBot 客户端
- **NapCat**：功能丰富的 OneBot 客户端

> 注意：go-cqhttp 已停止维护，不推荐使用。

## 开发者

### 安装开发依赖

```bash
# 推荐：使用 uv 安装开发依赖
uv sync --dev

# 这会安装 pytest、pyinstaller 等开发工具
```

### 开发脚本

项目提供了完整的开发、测试、构建脚本：

```
scripts/
├── build/                 # 构建相关
│   ├── build.bat          # 构建打包脚本
│   └── dicepp.spec        # PyInstaller 打包配置
├── dev/                   # 开发环境脚本
│   ├── install.bat        # 安装开发依赖
│   └── run.bat            # 启动开发服务器
├── test/                  # 测试脚本
│   ├── run_unit_test.bat      # 单元测试
│   ├── run_integration_test.bat  # 集成测试
│   ├── run_build_test.bat     # 构建验证
│   └── test_bot.py            # 集成测试核心
├── deploy/                # 部署脚本
│   ├── linux/             # Linux 部署 (start/stop/restart/logs.sh)
│   └── windows/           # Windows 部署
└── migrate/               # 数据迁移工具
```

**常用命令：**

| 用途 | 命令 |
|------|------|
| 开发运行 | `scripts\dev\run.bat` |
| 单元测试 | `scripts\test\run_unit_test.bat` |
| 集成测试 | `scripts\test\run_integration_test.bat` |
| 打包构建 | `scripts\build\build.bat` |
| 构建验证 | `scripts\test\run_build_test.bat` |

### 构建独立 EXE

项目支持打包为独立的 Windows EXE，用户无需安装 Python 即可运行：

```bash
# 运行构建脚本
scripts\build\build.bat

# 验证构建
scripts\test\run_build_test.bat
```

构建完成后，产物位于 `dist\DicePP\` 目录：

```
dist/DicePP/
├── DicePP.exe      # 主程序
├── .env            # 配置文件（用户可编辑）
├── Data/           # 数据目录（用户数据存储位置）
└── _internal/      # 内部依赖（无需修改）
```

### 运行测试

```bash
# 单元测试
scripts\test\run_unit_test.bat
# 或直接使用 pytest
pytest

# 集成测试（需要先启动 Bot）
scripts\test\run_integration_test.bat

# 交互模式测试
scripts\test\run_integration_test.bat -i
```

## 交流群

请加入交流群 861919492 获取整合包和部署指南

## 更新日志

### V3.0.0
- NoneBot2 现代化升级 (v2.4.0+)
- 更新依赖版本 (aiohttp ^3.9, fastapi >=0.100.0, uvicorn >=0.24.0)
- Python 版本要求提升至 >=3.10
- 配置文件现代化 (.env.example)
- Docker 部署优化 (python:3.12-slim, 多阶段构建)
- 更新 README.md 和 DEPLOY.md 部署文档
- 修复部分 bug
- 优化 log 代码，新增外部接口
