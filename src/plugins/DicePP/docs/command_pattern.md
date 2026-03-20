# 命令模式

> **文档导航**: [首页](./README.md) | [架构总览](./architecture.md) | 命令模式 | [掷骰示例](./example_roll.md) | [指令速查](./command_reference.md)

DicePP 使用经典的命令模式来处理用户交互。

## 核心抽象

### UserCommandBase

所有用户命令的基类，位于 `core/command/user_cmd.py`：

```python
class UserCommandBase(metaclass=abc.ABCMeta):
    readable_name: str = "未命名指令"  # 命令显示名称
    priority: int = 0                   # 优先级 (越小越高)
    flag: int = 0                      # 命令标识
    group_only: bool = False            # 是否仅群聊

    def __init__(self, bot: Bot):
        self.bot = bot

    @abc.abstractmethod
    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        """判断是否处理该消息"""
        should_proc: bool = False
        should_pass: bool = False  # 是否继续传递给其他命令
        return should_proc, should_pass, None

    @abc.abstractmethod
    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        """处理消息，返回命令列表"""
        return []

    @abc.abstractmethod
    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        """获取帮助文本"""
        return ""

    @abc.abstractmethod
    def get_description(self) -> str:
        """获取简短描述"""
        return ""
```

### BotCommandBase

机器人执行的操作基类，位于 `core/command/bot_cmd.py`：

```python
class BotCommandBase(ABC):
    """Bot 执行的操作命令"""
    pass

class BotSendMsgCommand(BotCommandBase):
    """发送消息"""
    def __init__(self, account: str, message: str, ports: List[MessagePort]): ...

class BotActionCommand(BotCommandBase):
    """执行动作 (如加好友、邀请入群)"""
    ...
```

## 定义命令

使用装饰器 `@custom_user_command` 定义命令：

```python
from core.command import custom_user_command, DPP_COMMAND_FLAG_ROLL

@custom_user_command(
    readable_name="掷骰指令",
    priority=0,
    group_only=False,
    flag=DPP_COMMAND_FLAG_ROLL
)
class RollDiceCommand(UserCommandBase):
    def __init__(self, bot: Bot):
        super().__init__(bot)
        # 注册本地化文本
        bot.loc_helper.register_loc_text(
            LOC_ROLL_RESULT,
            "{nickname} 的掷骰结果为 {roll_result_final}",
            "掷骰结果描述"
        )
        # 注册配置项
        bot.cfg_helper.register_config(
            CFG_ROLL_ENABLE,
            "1",
            "掷骰指令开关"
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        # 检查消息是否以 .r 开头
        should_proc = msg_str.startswith(".r")
        return should_proc, False, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析和处理逻辑
        feedback = "掷骰结果: 1D20=15"

        # 根据消息来源选择发送端口
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return "掷骰：.r[表达式]"

    def get_description(self) -> str:
        return ".r 掷骰"
```

## 命令注册

在 `Bot.start_up()` 中扫描并注册所有命令：

```python
def register_command(self):
    import core.command as command_module
    import module

    # 扫描所有 UserCommandBase 子类
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, command_module.UserCommandBase):
            if obj is not command_module.UserCommandBase:
                cmd = obj(self)
                self.command_dict[obj.readable_name] = cmd
```

## 消息处理流程

```
1. 收到消息
   ↓
2. Bot.process_message(msg_str, raw_message)
   ↓
3. 预处理消息 (提取用户ID、群ID等)
   ↓
4. 遍历 command_dict 中的所有命令
   ↓
5. 命令.can_process_msg() 判断是否处理
   ↓ (返回 True)
6. 命令.process_msg() 处理消息
   ↓
7. 返回 List[BotCommandBase]
   ↓
8. 执行所有 BotCommandBase
   ↓
9. 发送回复消息
```

## 优先级与 Flag

### 优先级 (priority)

- 值越小优先级越高
- 系统命令通常使用负数
- 用户命令从 0 开始

### Flag

用于标识命令类型，便于批量操作：

```python
DPP_COMMAND_FLAG_ROLL = 1 << 0      # 掷骰
DPP_COMMAND_FLAG_CHAR = 1 << 1      # 角色
DPP_COMMAND_FLAG_SYSTEM = 1 << 2   # 系统
```

## 常用辅助方法

### 1. 获取配置

```python
value = self.bot.cfg_helper.get_config(CFG_ROLL_ENABLE)[0]
```

### 2. 读写数据（SQLite / Repository）

```python
# 示例：用户统计（UserStat / UserStatInfo 见 core/data/models、core/statistics）
row = await self.bot.db.user_stat.get(meta.user_id)
if row:
    stat = UserStatInfo()
    stat.deserialize(row.data)
# 修改后写回
await self.bot.db.user_stat.upsert(UserStat(user_id=meta.user_id, data=stat.serialize()))
```

各表访问入口为 `self.bot.db` 上的属性（如 `karma`、`group_config`、`initiative`）。连接在 `delay_init_command` 中建立，处理消息前已可用。

### 3. 格式化文本

```python
feedback = self.bot.loc_helper.format_loc_text(
    LOC_ROLL_RESULT,
    nickname="Alice",
    roll_result_final="15"
)
```

### 4. 定时任务

```python
def tick_daily(self) -> List[BotCommandBase]:
    """每天执行一次"""
    # 清理统计数据等
    return []

def tick(self) -> List[BotCommandBase]:
    """每秒执行一次"""
    return []
```

---

## 相关文档

- **了解整体架构**: 查看 [架构总览](./architecture.md) 了解系统各组件的关系
- **完整实现案例**: 查看 [掷骰示例](./example_roll.md) 了解实际命令的完整实现
- **所有指令列表**: 查看 [指令速查手册](./command_reference.md) 了解所有可用指令
- **返回**: [文档首页](./README.md)
