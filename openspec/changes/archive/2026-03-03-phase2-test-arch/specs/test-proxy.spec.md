# Spec: TestProxy 接口规格

## 概述

定义测试用的 `ClientProxy` 实现，用于捕获和验证 Bot 发出的命令。

## 类定义

**文件位置**: `src/plugins/DicePP/conftest.py`

**继承关系**: `TestProxy(ClientProxy)`

## 接口规格

### SPEC-P2-001: 属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `received` | `List[BotCommandBase]` | `[]` | 存储所有收到的命令 |
| `mute` | `bool` | `False` | 是否静默输出 |

### SPEC-P2-002: process_bot_command

```python
async def process_bot_command(self, command: BotCommandBase) -> None
```

**行为**:
1. 将 `command` 追加到 `self.received` 列表
2. 如果 `self.mute` 为 `False`，打印命令到控制台

**验收标准**:
- 调用后 `len(self.received)` 增加 1
- 最后一个元素是传入的 command

### SPEC-P2-003: process_bot_command_list

```python
async def process_bot_command_list(self, command_list: List[BotCommandBase]) -> None
```

**行为**:
- 对列表中每个命令调用 `process_bot_command`

**验收标准**:
- 所有命令按顺序追加到 `received`

### SPEC-P2-004: clear

```python
def clear(self) -> None
```

**行为**:
- 清空 `self.received` 列表

**验收标准**:
- 调用后 `len(self.received) == 0`

### SPEC-P2-005: get_group_list

```python
async def get_group_list(self) -> List[GroupInfo]
```

**行为**:
- 返回空列表 `[]`

**原因**: 测试环境不需要真实群列表

### SPEC-P2-006: get_group_info

```python
async def get_group_info(self, group_id: str) -> GroupInfo
```

**行为**:
- 返回 `GroupInfo(group_id, f"测试群{group_id}", 10)`

**参数说明**:
- `group_id`: 传入的群ID
- `group_name`: 固定格式 "测试群{group_id}"
- `member_count`: 固定值 10

### SPEC-P2-007: get_group_member_list

```python
async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]
```

**行为**:
- 返回空列表 `[]`

### SPEC-P2-008: get_group_member_info

```python
async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo
```

**行为**:
- 返回 `GroupMemberInfo(user_id, f"用户{user_id}", "member")`

## 完整实现示例

```python
from typing import List
from adapter import ClientProxy
from core.command import BotCommandBase
from core.communication import GroupInfo, GroupMemberInfo

class TestProxy(ClientProxy):
    def __init__(self):
        self.received: List[BotCommandBase] = []
        self.mute: bool = False

    async def process_bot_command(self, command: BotCommandBase) -> None:
        self.received.append(command)
        if not self.mute:
            print(f"[TestProxy] {command}")

    async def process_bot_command_list(self, command_list: List[BotCommandBase]) -> None:
        for cmd in command_list:
            await self.process_bot_command(cmd)

    def clear(self) -> None:
        self.received.clear()

    async def get_group_list(self) -> List[GroupInfo]:
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        return GroupInfo(group_id, f"测试群{group_id}", 10)

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        return []

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        return GroupMemberInfo(user_id, f"用户{user_id}", "member")
```

## 使用示例

```python
proxy = TestProxy()
proxy.mute = True
await proxy.process_bot_command(some_command)
assert len(proxy.received) == 1
assert proxy.received[0] is some_command

proxy.clear()
assert len(proxy.received) == 0
```
