# 命令示例：以掷骰为例

> **文档导航**: [首页](./README.md) | [架构总览](./architecture.md) | [命令模式](./command_pattern.md) | 掷骰示例 | [指令速查](./command_reference.md)

本文以 `.r` 掷骰命令为例，详细说明命令的实现流程。

## 文件位置

- **命令入口**: `module/roll/roll_dice_command.py`
- **表达式解析**: `module/roll/expression.py`
- **结果处理**: `module/roll/result.py`

## 命令结构

```python
@custom_user_command(
    readable_name="掷骰指令",
    priority=0,
    group_only=False,
    flag=DPP_COMMAND_FLAG_ROLL
)
class RollDiceCommand(UserCommandBase):
    def __init__(self, bot: Bot): ...
    def can_process_msg(self, msg_str: str, meta: MessageMetaData): ...
    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any): ...
    def get_help(self, keyword: str, meta: MessageMetaData): ...
    def get_description(self): str: ...
    def tick_daily(self) -> List[BotCommandBase]: ...
```

## 消息处理流程

### 1. can_process_msg - 判断是否处理

```python
def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
    # 简单判断：消息是否以 ".r" 开头
    should_proc: bool = msg_str.startswith(".r")
    should_pass: bool = False  # 不传递给其他命令
    return should_proc, should_pass, None
```

### 2. process_msg - 核心处理

```python
def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
    # === 步骤 1: 检查功能开关 ===
    if not self.bot.cfg_helper.get_config(CFG_ROLL_ENABLE):
        feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE)
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    # === 步骤 2: 解析参数 ===
    # 去除 ".r" 前缀
    msg_str = msg_str[2:].strip()

    # 解析各种标志
    is_hidden = False      # 暗骰
    is_show_info = True    # 显示详情
    special_mode = ""     # 特殊模式 (a/n)
    compute_exp = False    # 计算期望
    times = 1              # 掷骰次数

    # 处理重复掷骰 (.r 4#d20)
    if "#" in msg_str:
        time_str, msg_str = msg_str.split("#", 1)
        times = int(time_str)

    # 处理暗骰 (.r h...)
    if msg_str.startswith("h"):
        is_hidden = True
        msg_str = msg_str[1:]

    # === 步骤 3: 解析掷骰表达式 ===
    exp_str, reason_str = sift_roll_exp_and_reason(msg_str)
    exp_str = preprocess_roll_exp(exp_str)  # 预处理: 转大写、中文转英文
    roll_exp = parse_roll_exp(exp_str)      # 解析为表达式对象

    # === 步骤 4: 执行掷骰 ===
    results = [roll_exp.get_result() for _ in range(times)]

    # === 步骤 5: 格式化结果 ===
    roll_result_final = results[0].get_complete_result()

    # === 步骤 6: 获取回复端口 ===
    port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)

    # === 步骤 7: 生成回复 ===
    nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
    feedback = self.bot.loc_helper.format_loc_text(
        LOC_ROLL_RESULT,
        nickname=nickname,
        roll_result_final=roll_result_final
    )

    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
```

## 表达式解析流程

掷骰表达式的解析在 `module/roll/expression.py` 中完成：

### 输入示例

```
.d20+1d4+5 攻击哥布林
```

### 解析步骤

```
1. sift_roll_exp_and_reason()
   → exp_str: "D20+1D4+5"
   → reason_str: "攻击哥布林"

2. preprocess_roll_exp()
   → 转换为大写
   → 中文转英文 (优 -> 优势, 劣势 -> 劣势)
   → 抗性/易伤处理

3. parse_roll_exp() - 核心解析
   ├── split_roll_str() - 按连接符分割
   │   → ["D20", "+", "1D4", "+", "5"]
   │
   ├── parse_single_roll_exp() - 解析每个元素
   │   → RollExpressionXDY("D20")
   │   → REModAdd
   │   → RollExpressionXDY("1D4")
   │   → REModAdd
   │   → RollExpressionInt("5")
   │
   └── create_leveling_list() - 构建求值顺序
       → 构建嵌套列表用于计算
```

### 计算结果

```
RollExpression.get_result() 返回 RollResult:
    ├── val_list: [15, 3, 5]     # 骰值列表
    ├── info: "[15]+[3]+5"       # 显示信息
    ├── exp: "D20+1D4+5"         # 表达式
    └── type: 20                  # 骰子类型
```

## 完整交互示例

### 用户输入

```
.r 4#d20+5 优势攻击
```

### 系统处理

```
1. can_process_msg(".r 4#d20+5 优势攻击")
   → 返回 (True, False, None)

2. process_msg() 内部处理:
   a) 解析 flags: times=4, reason="优势攻击"
   b) preprocess: "4#D20+5 优势" → "4#D20+5 优势"
   c) sift: exp="4#D20+5", reason="优势攻击"
   d) parse: 解析 "D20+5" (优势已转为 K1)
   e) execute: 掷骰 4 次

3. 返回命令列表:
   [
       BotSendMsgCommand(
           account="123456",
           message="Alice 的掷骰结果为 ...",
           ports=[GroupMessagePort("987654")]
       )
   ]

4. Bot 执行命令 → 发送消息到群
```

## 关键类说明

### RollExpression

表达式基类及其实现：

```
RollExpression (抽象基类)
    │
    ├── RollExpressionFormula    # 完整表达式
    │       └── 计算 + - * / 连接
    │
    ├── RollExpressionXDY        # XdY 掷骰
    │       └── 1D20, 3D6 等
    │
    ├── RollExpressionInt       # 整数常量
    │       └── 5, -10 等
    │
    ├── RollExpressionFloat     # 浮点数
    │       └── 3.14 等
    │
    └── RollExpressionXB        # 全回合攻击
            └── 5B 等
```

### RollResult

掷骰结果：

```python
class RollResult:
    val_list: List[int]    # [15, 3, 5]
    info: str              # "[15]+[3]+5"
    exp: str               # "1D20+1D4+5"
    type: int              # 骰子类型 (20)
    success: int           # 大成功次数
    fail: int              # 大失败次数
    d20_num: int           # D20 数量
```

### 修饰器 (RollExpModifier)

处理各种修饰指令：

```
RollModKeepHighest (k)   # 取最高
RollModKeepLowest (kl)   # 取最低
RollModReroll (r)       # 重掷
RollModExplode (x)       # 爆炸
RollModCritRange (cs)    # 暴击区间
RollModSuccess (sa/sf)   # 成功判定
RollModMultiply (*)      # 乘算
RollModDivide (/)         # 除算
```

## 数据持久化

掷骰命令会记录统计数据：

```python
def record_roll_data(bot, meta, res_list):
    # 更新用户统计
    user_stat = bot.data_manager.get_data(DC_USER_DATA, [user_id, DCK_USER_STAT])
    user_stat.roll.times.inc(roll_times)

    # 记录 D20 分布
    for res in results:
        if res.d20_num == 1:
            user_stat.roll.d20.record(int(res.val_list[0]))
```

## 小结

掷骰命令展示了 DicePP 的典型处理流程：

1. **接收消息** → 提取消息内容和元信息
2. **解析参数** → 处理各种标志和表达式
3. **业务逻辑** → 掷骰、计算 Karma 等
4. **格式化** → 本地化文本替换
5. **返回命令** → BotSendMsgCommand
6. **执行** → 发送回复消息

---

## 相关文档

- **了解整体架构**: 查看 [架构总览](./architecture.md) 了解系统各组件的关系
- **命令系统详解**: 查看 [命令模式](./command_pattern.md) 了解命令的设计与实现
- **掷骰指令用法**: 查看 [指令速查手册 - 掷骰指令](./command_reference.md#掷骰指令) 了解掷骰指令的完整语法
- **返回**: [文档首页](./README.md)
