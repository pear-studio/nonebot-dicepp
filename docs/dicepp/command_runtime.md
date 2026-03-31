# 命令运行机制

本文档描述 DicePP 命令的真实注册与执行协议。命令细节与触发词索引见 `command_catalog.md`。

## 注册机制（当前实现）

DicePP 使用装饰器注册，而不是运行时目录扫描：

1. 在命令类上使用 `@custom_user_command(...)`
2. 装饰器将命令类写入 `USER_COMMAND_CLS_DICT`
3. Bot 启动时读取该注册表并按 `priority` 排序实例化

关键代码：

- `core/command/user_cmd.py`
- `core/bot/dicebot.py`（`register_command()`）

## 命令接口契约

所有命令继承 `UserCommandBase`，核心接口：

- `can_process_msg(msg_str, meta) -> (should_proc, should_pass, hint)`
- `async process_msg(msg_str, meta, hint) -> List[BotCommandBase]`
- `get_help()` / `get_description()`

### 返回值语义

- `should_proc=False`：该命令不处理当前消息
- `should_proc=True`：进入权限/群聊限制检查并执行 `process_msg`
- `should_pass=False`：处理后终止继续分发
- `should_pass=True`：继续传给后续命令（旁路监听场景）

## 执行流水线

`Bot.process_message()` 的核心顺序：

1. `preprocess_msg` 预处理（小写化、标点转换、转义处理等）
2. 按 `CFG_COMMAND_SPLIT` 拆分多条子指令
3. 对每条子指令按优先级遍历 `self.command_dict.values()`
4. 调 `can_process_msg`（兼容 sync/async 两种实现）
5. 命中后执行 `process_msg` 并收集 `BotCommandBase`
6. 若 `should_pass=False` 立即停止该子指令后续分发

关键代码：

- `core/bot/dicebot.py`（`process_message()`）
- `core/communication/process.py`（`preprocess_msg`）

## 优先级与权限说明

### 优先级

- 规则：数值越小越先执行
- 常量定义在 `core/command/const.py`
- 例如：
  - `DPP_COMMAND_PRIORITY_DEFAULT = 1 << 10`
  - `DPP_COMMAND_PRIORITY_MASTER = 1 << 11`
  - `DPP_COMMAND_PRIORITY_TRIVIAL = 1 << 12`

### 权限

`meta.permission` 在 `process_message()` 中计算：

- 4: master
- 3: admin
- 2: 群主
- 1: 群管理
- 0: 普通成员

命令可通过两层限制：

- 声明层：`group_only` / `permission_require`
- 逻辑层：命令内部进一步校验（例如某些子命令要求更高权限）

## 易错点

- 不要将“命令总数”写死到文档，新增命令后会漂移。
- 并非所有命令都是纯 `.xxx` 前缀触发（存在自动匹配类命令）。
- `process_msg` 应按异步接口实现与调用。
