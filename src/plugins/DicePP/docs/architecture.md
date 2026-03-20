# 架构总览

> **文档导航**: [首页](./README.md) | 架构总览 | [命令模式](./command_pattern.md) | [掷骰示例](./example_roll.md) | [指令速查](./command_reference.md)

## 项目结构

```
DicePP/
├── core/               # 核心框架
│   ├── bot/            # 机器人主类
│   ├── command/        # 命令系统
│   ├── communication/  # 消息系统
│   ├── config/        # 配置管理
│   ├── data/          # 数据持久化 (BotDatabase, Repository)
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
    def __init__(self, account: str, ...):
        self.db = BotDatabase(self.account)   # 异步 SQLite（connect 在 delay_init_command）
        self.loc_helper = LocalizationManager(...)
        self.cfg_helper = ConfigManager(...)
        self.command_dict: Dict[str, UserCommandBase] = {}
        self.hub_manager = HubManager(self)
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
    ├── process_msg()       # 处理消息并返回命令列表（异步）
    ├── get_help()          # 获取帮助文本
    └── get_description()   # 获取简短描述
```

### 3. 数据持久化 (core/data/)

运行时数据通过 **`BotDatabase`**（`aiosqlite`）访问：

- **`bot_data.db`**：各业务表的键值 + JSON `data` 列（Pydantic 模型），由 **`Repository<T>`** 封装 CRUD。
- **`log.db`**：跑团日志会话与逐条记录，由 **`LogRepository`** 维护（与主库分离）。

`Bot` 在异步初始化流程中调用 `await self.db.connect()` 后，命令内使用 `await self.bot.db.<repo>.get(...)` 等形式读写。

**命名常量**（`core/data/basic.py` 等）：如 `DC_USER_DATA`、`DCK_USER_STAT` 等仍用于兼容文档与部分逻辑中的「数据域」标识，不等同于旧版按 JSON 文件分块存储的实现。

**内存模型**：统计、部分角色子结构仍使用 **`JsonObject`**（`core/data/json_object.py`）做序列化，再写入 SQLite 的 `data` 字段或由业务自行拼装。

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
├── BotDatabase (SQLite / Repository)
├── LocalizationManager (文本)
├── ConfigManager (配置)
├── HubManager (骰子中心)
└── CommandDict (命令)
    ↓ 依赖
├── UserCommandBase.process_msg()
│   ├── await bot.db.* （异步持久化）
│   ├── LocalizationManager (格式化文本)
│   └── ConfigManager (读取配置)
└── BotCommandBase
    └── 发送消息/处理请求
```

## 关键技术点

1. **异步处理**: 命令处理与数据库访问以 `async/await` 为主。
2. **数据持久化**: SQLite（WAL），业务行内 JSON 与日志规范化表并存；详见仓库根目录 `docs/DATA_LAYER.md`。
3. **国际化**: 支持多语言文本替换
4. **插件化**: 命令通过注册机制动态加载

---

## 相关文档

- **深入命令系统**: 查看 [命令模式](./command_pattern.md) 了解命令的设计与实现
- **实际案例分析**: 查看 [掷骰示例](./example_roll.md) 了解完整的命令实现流程
- **快速上手**: 查看 [指令速查手册](./command_reference.md) 了解所有可用指令
- **返回**: [文档首页](./README.md)
