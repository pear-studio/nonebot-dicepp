---
alwaysApply: false
description: 编写 DicePP 插件代码
---
# DicePP 开发规范

## 技术栈

- Python 3.10+ / NoneBot2 2.4+
- OneBot v11 适配器
- aiohttp / aiofiles (异步IO)
- openpyxl (Excel处理)
- SQLite3 (查询数据库)

## 项目结构

```
src/plugins/DicePP/
├── core/               # 核心框架
│   ├── bot.py         # Bot 主类
│   ├── command/       # 命令处理
│   ├── communication/ # 消息通信
│   ├── config/        # 配置管理
│   ├── data/          # 数据持久化
│   └── localization/  # 本地化
├── module/            # 功能模块
│   ├── common/        # 通用命令 (help, master等)
│   ├── roll/          # 掷骰命令
│   ├── query/         # 查询命令
│   ├── deck/          # 抽卡命令
│   ├── character/     # 角色卡
│   ├── initiative/    # 先攻管理
│   └── misc/          # 杂项命令
├── adapter/           # NoneBot 适配器
└── utils/             # 工具函数
```

## 命令结构

```
module/xxx/
├── xxx_command.py     # 命令实现
├── xxx_data.py        # 数据定义 (可选)
└── __init__.py
```

## 命名规范

- Command 类: PascalCase + Command (`RollDiceCommand`)
- DataChunk 类: PascalCase + DataChunk (`UserDataChunk`)
- 本地化 Key: UPPER_SNAKE_CASE (`LOC_ROLL_RESULT`)
- 配置 Key: UPPER_SNAKE_CASE (`CFG_ROLL_ENABLE`)

## 创建新命令

1. 继承 `UserCommandBase`
2. 使用 `@custom_user_command` 装饰器
3. 实现 `can_process_msg` 和 `process_msg` 方法

```python
from core.command import UserCommandBase, custom_user_command
from core.command.const import *

@custom_user_command(
    readable_name="示例命令",
    priority=DPP_COMMAND_PRIORITY_DEFAULT,
    flag=DPP_COMMAND_FLAG_FUN
)
class ExampleCommand(UserCommandBase):
    def __init__(self, bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text("loc_key", "默认文本", "注释")

    def can_process_msg(self, msg_str, meta):
        if msg_str.startswith(".example"):
            return True, False, msg_str[8:].strip()
        return False, False, None

    def process_msg(self, msg_str, meta, hint):
        # 处理逻辑
        return [BotSendMsgCommand(self.bot.account, "回复", [port])]

    def get_help(self, keyword, meta):
        return ".example 示例命令"

    def get_description(self):
        return ".example 示例命令"
```

## 数据访问

```python
# 读取数据
data = self.bot.data_manager.get_data(
    DC_USER_DATA,           # DataChunk 标识
    [user_id, "key"],       # 路径
    default_val="default"   # 默认值
)

# 写入数据
self.bot.data_manager.set_data(
    DC_USER_DATA,
    [user_id, "key"],
    "new_value"
)
```

## 本地化

```python
# 注册
bot.loc_helper.register_loc_text(
    "loc_key",
    "你好 {name}!",
    "用户问候语"
)

# 使用
text = self.format_loc("loc_key", name="用户")
```

## 工具导入

```python
# 推荐: 从 utils 包直接导入
from utils import read_json, update_json, read_xlsx, update_xlsx

# 避免: 从子模块导入
# from utils.localdata import read_json  # 不推荐
```

## 安全规范

- 禁止硬编码敏感信息
- 使用 `.env` 管理环境变量
- 数据文件使用 `.gitignore` 忽略

## 测试规范

- 测试文件放在 `tests/` 目录
- 命名: `test_*.py`
- 使用 pytest fixtures: `shared_bot`, `fresh_bot`
