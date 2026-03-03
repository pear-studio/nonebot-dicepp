# 架构总览

## 项目结构

```
DicePP/
├── core/               # 核心框架
│   ├── bot/            # 机器人主类
│   ├── command/        # 命令系统
│   ├── communication/  # 消息系统
│   ├── config/        # 配置管理
│   ├── data/          # 数据持久化
│   ├── localization/  # 国际化
│   └── statistics/    # 统计系统
├── module/             # 功能模块
│   ├── roll/          # 骰子系统
│   ├── character/     # 角色系统
│   ├── common/        # 通用功能
│   └── ...
├── utils/             # 工具函数
├── adapter/           # 适配器
└── docs/              # 文档
```

## 核心组件

### 1. Bot 类 (core/bot/dicebot.py)

Bot 是整个机器人的核心，负责：

```python
class Bot:
    def __init__(self, account: str):
        self.data_manager = DataManager(self.data_path)  # 数据管理
        self.loc_helper = LocalizationManager(...)         # 国际化
        self.cfg_helper = ConfigManager(...)               # 配置管理
        self.command_dict: Dict[str, UserCommandBase] = {} # 命令字典
        self.hub_manager = HubManager(self)                # 骰子中心
```

**主要职责：**
- 初始化所有子系统
- 注册命令
- 消息分发与处理
- 定时任务调度

### 2. 命令系统 (core/command/)

项目采用 **命令模式**，所有功能都封装为命令类：

```
UserCommandBase (抽象基类)
    ├── can_process_msg()  # 判断是否处理该消息
    ├── process_msg()       # 处理消息并返回命令列表
    ├── get_help()          # 获取帮助文本
    └── get_description()   # 获取简短描述
```

### 3. 数据管理 (core/data/)

```
DataManager
    ├── get_data(key)      # 读取数据
    ├── set_data(key, val) # 写入数据
    └── get_keys()         # 获取所有键
```

数据存储结构：
- `DC_USER_DATA` - 用户数据
- `DC_GROUP_DATA` - 群数据
- `DC_NICKNAME` - 昵称
- `DC_MACRO` - 宏定义
- `DC_VARIABLE` - 变量

### 4. 消息系统 (core/communication/)

```
MessageMetaData
    ├── msg: str           # 原始消息
    ├── user_id: str       # 用户ID
    ├── group_id: str      # 群ID (可选)
    └── ...

MessagePort (消息端口)
    ├── PrivateMessagePort # 私聊
    └── GroupMessagePort   # 群聊
```

## 消息流程

```
收到消息
    ↓
Bot.process_message()
    ↓
遍历所有命令 → can_process_msg()
    ↓
命中 → process_msg() → 返回 List[BotCommandBase]
    ↓
执行 BotCommandBase (发送消息/处理请求/更新数据)
```

## 模块依赖关系

```
Bot (主类)
    ↓ 依赖
├── DataManager (数据)
├── LocalizationManager (文本)
├── ConfigManager (配置)
├── HubManager (骰子中心)
└── CommandDict (命令)
    ↓ 依赖
├── UserCommandBase.process_msg()
│   ├── DataManager (读写数据)
│   ├── LocalizationManager (格式化文本)
│   └── ConfigManager (读取配置)
└── BotCommandBase
    └── 发送消息/处理请求
```

## 关键技术点

1. **异步处理**: 使用 `asyncio` 处理并发任务
2. **数据持久化**: JSON 格式存储，支持热加载
3. **国际化**: 支持多语言文本替换
4. **插件化**: 命令通过注册机制动态加载
