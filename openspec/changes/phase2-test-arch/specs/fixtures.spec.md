# Spec: pytest Fixture 规格

## 概述

定义共享的 pytest fixture，为测试提供标准化的 Bot 实例和测试代理。

## Fixture 定义位置

**文件**: `src/plugins/DicePP/conftest.py`

## Fixture 规格

### SPEC-P2-010: shared_bot

**作用域**: `class`

**返回类型**: `Tuple[Bot, TestProxy]`

**行为**:
1. 使用 `tmp_path_factory.mktemp()` 创建临时数据目录
2. 创建 Bot 实例，设置 `data_path` 为临时目录
3. 配置 `CFG_MASTER` 为 `"test_master"`
4. 创建 TestProxy 实例并绑定到 Bot
5. 调用 `bot.delay_init_debug()` 初始化
6. 设置 `proxy.mute = True`
7. yield `(bot, proxy)`
8. 调用 `bot.shutdown_debug()` 清理

**适用场景**:
- 与现有 `setUpClass/tearDownClass` 风格兼容
- 同一测试类中的所有测试方法共享 Bot 实例
- 测试方法间可能存在状态依赖

**接口规格**:

```python
@pytest.fixture(scope="class")
def shared_bot(tmp_path_factory) -> Generator[Tuple[Bot, TestProxy], None, None]:
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
```

**验收标准**:
- 同一测试类中多个测试方法使用同一 Bot 实例
- 不同测试类使用不同 Bot 实例
- 测试结束后临时目录被自动清理

### SPEC-P2-011: fresh_bot

**作用域**: `function`

**返回类型**: `Tuple[Bot, TestProxy]`

**行为**:
1. 使用 `tmp_path` 创建临时数据目录
2. 创建全新的 Bot 实例
3. 配置和初始化流程同 `shared_bot`
4. 每个测试方法获得独立的 Bot 实例

**适用场景**:
- 需要完全隔离的测试
- 测试方法间无状态依赖
- 新编写的测试（推荐使用）

**接口规格**:

```python
@pytest.fixture
def fresh_bot(tmp_path) -> Generator[Tuple[Bot, TestProxy], None, None]:
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

**验收标准**:
- 每个测试方法使用独立的 Bot 实例
- 测试方法间完全隔离，无状态污染

## 辅助函数规格

### SPEC-P2-020: make_group_meta

**签名**:
```python
def make_group_meta(
    group_id: str, 
    user_id: str, 
    nickname: str = "测试用户"
) -> MessageMetaData
```

**行为**:
- 创建群消息的元数据对象

**返回值字段**:
| 字段 | 值 |
|------|-----|
| `raw_msg` | `""` |
| `plain_msg` | `""` |
| `sender.user_id` | 传入的 `user_id` |
| `sender.nickname` | 传入的 `nickname` |
| `group_id` | 传入的 `group_id` |
| `to_me` | `False` |

### SPEC-P2-021: make_private_meta

**签名**:
```python
def make_private_meta(
    user_id: str, 
    nickname: str = "测试用户"
) -> MessageMetaData
```

**行为**:
- 创建私聊消息的元数据对象

**返回值字段**:
| 字段 | 值 |
|------|-----|
| `raw_msg` | `""` |
| `plain_msg` | `""` |
| `sender.user_id` | 传入的 `user_id` |
| `sender.nickname` | 传入的 `nickname` |
| `group_id` | `""` |
| `to_me` | `True` |

### SPEC-P2-022: send_and_check

**签名**:
```python
async def send_and_check(
    bot: Bot, 
    msg: str, 
    meta: MessageMetaData, 
    checker: Callable[[str], bool]
) -> List[BotCommandBase]
```

**行为**:
1. 使用 `msg` 更新 `meta` 的 `raw_msg` 和 `plain_msg`
2. 调用 `bot.process_message(msg, meta)`
3. 将返回的命令列表转为字符串
4. 使用 `checker` 验证结果
5. 如果验证失败，抛出 `AssertionError`
6. 返回命令列表

**验收标准**:
- checker 返回 True 时正常返回
- checker 返回 False 时抛出有意义的错误信息

## 使用示例

```python
@pytest.mark.integration
class TestMyFeature:
    async def test_example(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        cmds = await send_and_check(
            bot, ".r d20", meta,
            lambda s: "D20" in s
        )
        
        assert len(proxy.received) > 0
```
