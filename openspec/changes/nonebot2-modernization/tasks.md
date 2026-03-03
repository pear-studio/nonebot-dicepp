## 1. 依赖升级准备

- [x] 1.1 备份当前 `pyproject.toml` 和 `requirements.txt`
- [x] 1.2 更新 `pyproject.toml` 中 nonebot2 版本为 `^2.4.0`
- [x] 1.3 更新 `pyproject.toml` 中 nonebot-adapter-onebot 版本为 `^2.4.0`
- [x] 1.4 更新 `pyproject.toml` 中 nb-cli 版本为 `^1.4.0`
- [x] 1.5 移除 `pyproject.toml` 中 `nonebot-adapter-cqhttp` 引用
- [x] 1.6 更新 `pyproject.toml` 中 Python 版本要求为 `>=3.10`
- [x] 1.7 更新其他依赖版本（aiohttp ^3.9, fastapi >=0.100.0, uvicorn >=0.24.0）
- [x] 1.8 修复 `pyproject.toml` 中版本号格式为 PEP 440 兼容（`2.0.0`）

## 2. requirements.txt 同步更新

- [x] 2.1 更新 `requirements.txt` 中 nonebot2 版本为 `>=2.4.0`
- [x] 2.2 更新 `requirements.txt` 中 nonebot-adapter-onebot 版本为 `>=2.4.0`
- [x] 2.3 更新 `requirements.txt` 中 nb-cli 版本为 `>=1.4.0`
- [x] 2.4 更新其他依赖版本与 pyproject.toml 保持一致
- [x] 2.5 移除任何弃用依赖引用

## 3. 依赖安装验证

- [x] 3.1 创建新的虚拟环境进行测试安装
- [x] 3.2 运行 `pip install -r requirements.txt` 验证无冲突
- [x] 3.3 运行 `uv sync` 验证无冲突（如果使用 uv）
- [x] 3.4 运行 `pytest src/plugins/DicePP` 验证测试通过（40 passed）

## 4. 代码适配

- [x] 4.1 检查 `bot.py` 中 NoneBot 初始化代码是否兼容新版
- [x] 4.2 检查 `bot.py` 中日志配置是否有弃用警告
- [x] 4.3 检查 `adapter/nonebot_adapter.py` 中的 import 语句
- [x] 4.4 检查 `adapter/nonebot_adapter.py` 中的 API 调用兼容性
- [x] 4.5 运行 Bot 并检查控制台是否有 DeprecationWarning
- [x] 4.6 修复所有弃用警告（如有）

## 5. 配置文件现代化

- [x] 5.1 创建 `.env.example` 文件
- [x] 5.2 添加 HOST 配置项及中文注释
- [x] 5.3 添加 PORT 配置项及中文注释
- [x] 5.4 添加 SUPERUSERS 配置项及中文注释
- [x] 5.5 添加 COMMAND_START 和 COMMAND_SEP 配置项及注释
- [x] 5.6 检查 `.gitignore` 是否正确忽略 `.env` 但不忽略 `.env.example`
- [x] 5.7 验证 `pyproject.toml` 的 `[project]` 段落完整性

## 6. Dockerfile 更新

- [x] 6.1 更新 `Dockerfile` 基础镜像为 `python:3.12-slim`
- [x] 6.2 验证多阶段构建配置正确
- [x] 6.3 验证 HEALTHCHECK 指令存在
- [x] 6.4 更新 `Dockerfile_pi`（如需要树莓派支持）
- [ ] 6.5 本地构建 Docker 镜像测试：`docker build -t dicepp:test .`（需 Docker 环境）

## 7. docker-compose.yml 更新

- [x] 7.1 检查端口映射配置
- [x] 7.2 检查数据目录卷挂载配置
- [x] 7.3 检查重启策略配置
- [ ] 7.4 运行 `docker-compose up` 测试部署（需 Docker 环境）

## 8. README.md 更新

- [x] 8.1 更新项目描述和功能特性列表
- [x] 8.2 添加环境要求说明（Python >= 3.10）
- [x] 8.3 添加 pip 安装命令
- [x] 8.4 添加 uv 安装命令
- [x] 8.5 添加配置步骤（复制 .env.example）
- [x] 8.6 添加启动命令（python bot.py / nb run）
- [x] 8.7 添加 Docker 启动方式说明
- [x] 8.8 添加推荐的 OneBot 实现说明（Lagrange、NapCat）
- [x] 8.9 说明 go-cqhttp 已停止维护

## 9. DEPLOY.md 部署文档

- [x] 9.1 添加服务器配置要求说明
- [x] 9.2 添加完整的 Docker 部署步骤
- [x] 9.3 添加反向代理配置示例（Nginx/Caddy）
- [x] 9.4 添加常见问题及解决方案

## 10. 最终验证与发布

- [ ] 10.1 全量运行测试套件
- [ ] 10.2 在测试环境启动 Bot 验证基本功能
- [ ] 10.3 测试核心命令：.r、.log、.mode 等
- [ ] 10.4 更新版本号为 2.0.0
- [ ] 10.5 更新 CHANGELOG（如有）
- [ ] 10.6 提交所有变更并创建 Pull Request
