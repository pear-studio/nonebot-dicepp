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

### 安装测试依赖

```bash
pip install pytest pytest-asyncio pytest-cov
# 或使用 uv
uv sync --extra dev
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并显示覆盖率
pytest --cov

# 运行特定目录的测试
pytest src/plugins/DicePP
```

### 测试文件命名规范

- 文件名: `unit_test.py`, `test_*.py`, `*_test.py`
- 类名: `MyTestCase`, `Test*`
- 函数名: `test*`

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
