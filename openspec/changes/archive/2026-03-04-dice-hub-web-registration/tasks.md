## 1. 准备阶段

- [x] 1.1 添加 httpx 依赖到 pyproject.toml (项目已有 aiohttp，跳过)
- [x] 1.2 在 core/config/common.py 新增 dicehub_api_url 配置项
- [x] 1.3 检查现有 dice_hub 模块文件列表，确认需要删除的文件

## 2. 删除旧模块

- [x] 2.1 删除 module/dice_hub/encrypt.py
- [x] 2.2 删除 module/dice_hub/data.py
- [x] 2.3 删除 module/dice_hub/__pycache__/ 目录
- [x] 2.4 备份并删除旧数据文件（如果有）

## 3. 新增 HTTP 客户端

- [x] 3.1 创建 module/dice_hub/api_client.py（HTTP 请求封装）
- [x] 3.2 实现 register() 方法：POST /api/bots/register
- [x] 3.3 实现 heartbeat() 方法：POST /api/bots/heartbeat
- [x] 3.4 实现 get_robots() 方法：GET /api/bots/
- [x] 3.5 添加错误处理和重试逻辑

## 4. 重写 HubManager

- [x] 4.1 重写 module/dice_hub/manager.py
- [x] 4.2 移除旧的 QQ 消息相关逻辑
- [x] 4.3 新增 API Key 存储和读取方法
- [x] 4.4 新增心跳调度逻辑（支持测试/正式模式）

## 5. 重写 HubCommand

- [x] 5.1 重写 module/dice_hub/hub_command.py
- [x] 5.2 实现 `.hub register` 指令
- [x] 5.3 实现 `.hub key` 指令
- [x] 5.4 实现 `.hub list` 指令
- [x] 5.5 实现 `.hub online` 指令
- [x] 5.6 实现 `.hub url` 指令（设置/查看 API URL）

## 6. 定时任务

- [x] 6.1 实现心跳定时任务（支持测试 10s / 正式 3min）
- [x] 6.2 实现机器人列表定时刷新任务（10min）

## 7. 测试

- [x] 7.1 编写单元测试：api_client.py
- [x] 7.2 编写集成测试：hub_command.py 指令
- [x] 7.3 手动测试：注册、心跳、列表功能
- [x] 7.4 与网站端联调测试

## 8. 清理

- [x] 8.1 更新 docs/feature_list.md（更新多机器人互联说明）
- [x] 8.2 删除旧的 __init__.py 导出（如有必要）
- [x] 8.3 运行 pytest 确保无回归问题
