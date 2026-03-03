# Phase 2: 技术设计

## 1. 共享 Fixture：`src/plugins/DicePP/conftest.py`

将 `TestProxy` 和 `Bot` 工厂提取到 conftest，供所有测试文件复用：

```python
# src/plugins/DicePP/conftest.py

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from typing import List
from core.bot import Bot
from core.command import BotCommandBase
from core.config import ConfigItem, CFG_MASTER
from adapter import ClientProxy

class TestProxy(ClientProxy):
    """通用测试代理，记录所有收到的命令，支持静音模式。"""
    def __init__(self):
        self.received: List[BotCommandBase] = []  # 新增：记录所有命令
        self.mute = False

    async def process_bot_command(self, command: BotCommandBase):
        self.received.append(command)
        if not self.mute:
            print(f"[TestProxy] {command}")

    async def process_bot_command_list(self, command_list):
        for cmd in command_list:
            await self.process_bot_command(cmd)

    def clear(self):
        self.received.clear()

    # get_group_list / get_group_info 等保持原有 stub 实现
    ...


@pytest.fixture(scope="class")
def shared_bot(tmp_path_factory):
    """class 级别共享 Bot，与现有测试行为一致。"""
    data_path = str(tmp_path_factory.mktemp("bot_data"))
    bot = Bot("test_bot", data_path=data_path)
    bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
    bot.cfg_helper.save_config()
    proxy = TestProxy()
    bot.set_client_proxy(proxy)
    bot.delay_init_debug()
    proxy.mute = True
    yield bot, proxy
    bot.shutdown_debug()


@pytest.fixture
def fresh_bot(tmp_path):
    """function 级别独立 Bot，每个测试方法完全隔离。供新测试使用。"""
    bot = Bot("test_bot_fresh", data_path=str(tmp_path))
    bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
    bot.cfg_helper.save_config()
    proxy = TestProxy()
    bot.set_client_proxy(proxy)
    bot.delay_init_debug()
    proxy.mute = True
    yield bot, proxy
    bot.shutdown_debug()
```

## 2. 增强 checker：支持 target_checker

在 `core/command/unit_test.py` 的辅助方法中增加 `target_checker` 参数：

```python
async def __vg_msg(self, msg: str, ...,
                   target_checker: Callable[[List[BotCommandBase]], bool] = None):
    """
    target_checker: 接收原始 BotCommandBase 列表，用于验证命令目标、类型等。
    
    示例：
      # 验证隐藏骰同时发给用户和GM
      target_checker=lambda cmds: (
          any(isinstance(c, BotSendMsgCommand) and "user" in str(c.targets) for c in cmds) and
          any(isinstance(c, BotSendMsgCommand) and "group" in str(c.targets) for c in cmds)
      )
    """
    bot_commands = await self.test_bot.process_message(msg, meta)
    result = "\n".join([str(command) for command in bot_commands])
    self.assertTrue(checker(result), ...)
    if target_checker is not None:
        self.assertTrue(target_checker(bot_commands), 
                        f"target_checker failed for msg: {msg}\nCommands: {bot_commands}")
```

## 3. pytest mark 标签体系

在 `pyproject.toml` 中注册自定义 marker：

```toml
[tool.pytest.ini_options]
markers = [
    "unit: pure unit tests, no Bot instance needed",
    "integration: full Bot integration tests",
    "slow: tests that take >1s (e.g. repeated dice rolls)",
    "karma: karma dice system tests",
    "log: log system tests",
]
```

使用方式（在测试类/方法上）：
```python
@pytest.mark.unit
class TestKarmaState:
    ...

@pytest.mark.integration
@pytest.mark.slow  
class TestRollDiceIntegration:
    ...
```

运行命令：
```bash
pytest -m "unit and not slow"       # 只跑快速单元测试
pytest -m "integration"             # 只跑集成测试
pytest -m "karma or log"            # 只跑特定模块
```

## 4. TestProxy.received 的用途

`TestProxy` 新增 `received: List[BotCommandBase]` 字段后，可以实现更精确的断言：

```python
# 验证 .rh 同时发出了两条消息
proxy.clear()
await bot.process_message(".rh d20", meta_group)

group_msgs = [c for c in proxy.received 
              if isinstance(c, BotSendMsgCommand) 
              and any(p.group_id for p in c.targets)]
private_msgs = [c for c in proxy.received 
                if isinstance(c, BotSendMsgCommand) 
                and any(not p.group_id for p in c.targets)]

assert len(group_msgs) == 1  # 群里通知"有人进行了隐藏骰"
assert len(private_msgs) == 1  # 私信发给骰子者结果
```

## 5. 隔离策略（现有测试 vs 新测试）

```
现有集成测试（保持不变）
├── 使用 setUpClass/tearDownClass
├── 共享 test_bot 实例
└── 字母序执行顺序依赖保留

新增测试（新风格）
├── 使用 @pytest.fixture(scope="function") 的 fresh_bot
├── 每个测试方法完全独立
├── 使用 @pytest.mark 标签
└── 可以使用 target_checker 验证命令目标
```

## 风险点

| 风险 | 说明 | 缓解 |
|------|------|------|
| `tmp_path_factory` scope 冲突 | class scope fixture 与 function scope 的 tmp_path 混用 | 使用 `tmp_path_factory.mktemp()` 替代 `tmp_path` |
| 现有测试 `tearDownClass` 清理逻辑 | 已有代码手动清理 `data_path`，与 `tmp_path` 自动清理冲突 | 现有测试不改动，仅新测试使用 fixture 的 tmp_path |
