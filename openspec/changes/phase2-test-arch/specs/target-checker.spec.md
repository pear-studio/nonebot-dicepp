# Spec: target_checker 接口规格

## 概述

定义 `__vg_msg` 等验证方法的 `target_checker` 参数，用于验证命令的发送目标和类型。

## 背景

现有的 `checker` 参数只能验证命令的文本内容，无法验证：
- 消息发送给谁（`MessagePort`）
- 命令的类型（发消息 vs 退群 vs 延迟）
- 隐藏骰是否同时发给用户和 GM

## 接口规格

### SPEC-P2-040: target_checker 参数

**添加位置**: `core/command/unit_test.py` 的验证方法

**受影响方法**:
- `__vg_msg`
- `__vp_msg`
- `__v_notice`

**参数签名**:
```python
async def __vg_msg(
    self, 
    msg: str, 
    checker: Optional[Callable[[str], bool]] = None,
    target_checker: Optional[Callable[[List[BotCommandBase]], bool]] = None,
    ...
) -> None
```

### SPEC-P2-041: target_checker 类型

```python
Callable[[List[BotCommandBase]], bool]
```

**输入**: Bot 处理消息后返回的完整命令列表
**输出**: 验证是否通过

### SPEC-P2-042: 验证流程

```python
async def __vg_msg(self, msg: str, ..., target_checker=None):
    bot_commands = await self.test_bot.process_message(msg, meta)
    
    # 现有的文本验证
    result = "\n".join([str(command) for command in bot_commands])
    if checker is not None:
        self.assertTrue(checker(result), ...)
    
    # 新增的目标验证
    if target_checker is not None:
        self.assertTrue(
            target_checker(bot_commands),
            f"target_checker failed for msg: {msg}\nCommands: {bot_commands}"
        )
```

### SPEC-P2-043: 验收标准

1. `target_checker` 为 `None` 时不执行任何验证
2. `target_checker` 返回 `True` 时验证通过
3. `target_checker` 返回 `False` 时抛出 `AssertionError`，包含：
   - 原始消息内容
   - 命令列表的字符串表示

## 使用示例

### 验证隐藏骰同时发给用户和群

```python
from core.command import BotSendMsgCommand

async def test_hidden_roll(self):
    def check_hidden_roll_targets(cmds: List[BotCommandBase]) -> bool:
        send_cmds = [c for c in cmds if isinstance(c, BotSendMsgCommand)]
        
        # 检查是否有发给群的消息
        has_group_msg = any(
            any(p.group_id for p in c.targets) 
            for c in send_cmds
        )
        
        # 检查是否有发给用户的私聊消息
        has_private_msg = any(
            any(not p.group_id for p in c.targets)
            for c in send_cmds
        )
        
        return has_group_msg and has_private_msg
    
    await self.__vg_msg(
        ".rh d20",
        checker=lambda s: "隐藏" in s or "暗骰" in s,
        target_checker=check_hidden_roll_targets
    )
```

### 验证命令类型

```python
from core.command import BotSendMsgCommand, BotDelayCommand

async def test_command_type(self):
    def check_no_delay(cmds: List[BotCommandBase]) -> bool:
        return not any(isinstance(c, BotDelayCommand) for c in cmds)
    
    await self.__vg_msg(".r d20", target_checker=check_no_delay)
```

### 验证消息数量

```python
async def test_single_response(self):
    def check_single_message(cmds: List[BotCommandBase]) -> bool:
        send_cmds = [c for c in cmds if isinstance(c, BotSendMsgCommand)]
        return len(send_cmds) == 1
    
    await self.__vg_msg(".r d20", target_checker=check_single_message)
```

## 常用 target_checker 工厂函数

建议在 `conftest.py` 中提供以下辅助函数：

```python
def has_group_message() -> Callable[[List[BotCommandBase]], bool]:
    """检查是否发送了群消息"""
    def checker(cmds):
        return any(
            isinstance(c, BotSendMsgCommand) and any(p.group_id for p in c.targets)
            for c in cmds
        )
    return checker

def has_private_message() -> Callable[[List[BotCommandBase]], bool]:
    """检查是否发送了私聊消息"""
    def checker(cmds):
        return any(
            isinstance(c, BotSendMsgCommand) and any(not p.group_id for p in c.targets)
            for c in cmds
        )
    return checker

def message_count(expected: int) -> Callable[[List[BotCommandBase]], bool]:
    """检查发送的消息数量"""
    def checker(cmds):
        send_cmds = [c for c in cmds if isinstance(c, BotSendMsgCommand)]
        return len(send_cmds) == expected
    return checker
```

## 向后兼容性

- `target_checker` 参数默认为 `None`
- 现有测试无需修改即可继续运行
- 新测试可选择性使用此功能
