# Spec: Log 命令行为规格

## 概述

定义日志系统 `.log` 命令的交互行为。

## 源文件

**位置**: `src/plugins/DicePP/module/common/log_command.py`

## 命令列表

| 命令 | 用途 |
|------|------|
| `.log new <名称>` | 创建新日志 |
| `.log on [名称]` | 开始记录 |
| `.log off` | 暂停记录 |
| `.log end` | 结束并保存日志 |
| `.log halt` | 暂停但不结束 |
| `.log list` | 列出所有日志 |
| `.log delete <名称>` | 删除日志 |
| `.log stat [名称]` | 显示统计信息 |
| `.log set <选项> <值>` | 设置过滤选项 |

## 命令规格

### SPEC-P3-LC001: .log new

**语法**: `.log new <名称>`

**行为**:
1. 创建新日志，名称不能重复（同群内）
2. 自动启用记录状态
3. 返回成功消息，包含日志名称

**成功响应包含**:
- 日志名称
- 创建成功提示

**错误场景**:
- 名称已存在 → 提示重名

**测试用例**:
```python
async def test_new_log(fresh_bot):
    bot, proxy = fresh_bot
    meta = make_group_meta("g1", "u1")
    
    cmds = await send_and_check(bot, ".log new 冒险日志", meta,
        lambda s: "冒险日志" in s)
    
    # 验证日志已创建
    assert any("创建" in str(c) or "新建" in str(c) for c in cmds)
```

### SPEC-P3-LC002: .log on

**语法**: `.log on [名称]`

**行为**:
1. 如果指定名称，激活该日志的记录
2. 如果不指定名称，激活最近一个日志
3. 设置 recording = True

**成功响应包含**:
- "开启"/"on" 或类似提示
- 日志名称

**错误场景**:
- 日志不存在 → 提示未找到
- 无任何日志 → 提示先创建

### SPEC-P3-LC003: .log off

**语法**: `.log off`

**行为**:
1. 暂停当前正在记录的日志
2. 设置 recording = False
3. 不删除日志和记录

**成功响应包含**:
- "暂停"/"off" 或类似提示

### SPEC-P3-LC004: .log end

**语法**: `.log end`

**行为**:
1. 结束当前日志
2. 设置 recording = False
3. 可能生成导出文件

**成功响应包含**:
- "结束"/"end" 或类似提示

### SPEC-P3-LC005: .log halt

**语法**: `.log halt`

**行为**:
- 与 `.log off` 类似，但语义上表示"暂停"

### SPEC-P3-LC010: .log list

**语法**: `.log list`

**行为**:
1. 列出当前群的所有日志
2. 显示每个日志的名称和状态

**成功响应包含**:
- 日志名称列表

**空列表响应**:
- 提示无日志

**测试用例**:
```python
async def test_list_logs(fresh_bot):
    bot, proxy = fresh_bot
    meta = make_group_meta("g1", "u1")
    
    await send_and_check(bot, ".log new Alpha", meta, lambda s: True)
    await send_and_check(bot, ".log new Beta", meta, lambda s: True)
    
    cmds = await send_and_check(bot, ".log list", meta,
        lambda s: "Alpha" in s and "Beta" in s)
```

### SPEC-P3-LC011: .log delete

**语法**: `.log delete <名称>`

**行为**:
1. 删除指定名称的日志
2. 同时删除所有关联记录

**成功响应包含**:
- 删除成功提示

**错误场景**:
- 日志不存在 → 提示未找到

### SPEC-P3-LC012: .log stat

**语法**: `.log stat [名称]`

**行为**:
1. 显示日志的统计信息
2. 包含：记录条数、参与用户数、时间范围等

**成功响应包含**:
- "统计"/"stat" 或类似提示
- 数值信息

### SPEC-P3-LC020: .log set

**语法**: `.log set <选项> <值>`

**可设置选项**:

| 选项 | 值 | 说明 |
|------|-----|------|
| `filter_bot` | on/off | 过滤机器人消息 |
| `filter_command` | on/off | 过滤命令消息 |
| `filter_outside` | on/off | 过滤非游戏内容 |
| `filter_media` | on/off | 过滤媒体消息 |

**成功响应包含**:
- 设置成功提示

## 完整流程测试

### SPEC-P3-LC100: new → on → off → on → end 流程

```python
@pytest.mark.integration
@pytest.mark.log
class TestLogCommand:
    async def test_full_flow(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        # 1. 创建日志
        await send_and_check(bot, ".log new 测试", meta, 
            lambda s: "测试" in s)
        
        # 2. 开启记录
        await send_and_check(bot, ".log on 测试", meta,
            lambda s: "on" in s.lower() or "开启" in s or "开始" in s)
        
        # 3. 暂停
        await send_and_check(bot, ".log off", meta,
            lambda s: "off" in s.lower() or "暂停" in s)
        
        # 4. 恢复
        await send_and_check(bot, ".log on 测试", meta,
            lambda s: True)
        
        # 5. 结束
        await send_and_check(bot, ".log end", meta,
            lambda s: "end" in s.lower() or "结束" in s)
```

## 消息记录行为

### SPEC-P3-LC200: 记录追加

当日志处于 recording 状态时：

1. 群内每条消息（非命令）被记录
2. Bot 回复也被记录（source = "bot"）
3. 用户消息 source = "user"

**不记录的情况**:
- recording = False
- 消息被过滤器排除

### SPEC-P3-LC201: 撤回处理

当用户撤回消息时：
- 根据 message_id 删除对应记录

## 边界条件

### 同群多日志

- 同一群可以有多个日志
- 同时只能有一个处于 recording 状态
- 开启新日志时自动关闭其他日志的记录

### 日志名称

- 大小写不敏感匹配
- 允许中文、空格、特殊字符
- 最大长度：无硬限制，但建议 < 100 字符
