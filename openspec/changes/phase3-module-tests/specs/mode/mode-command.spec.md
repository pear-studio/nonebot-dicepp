# Spec: 模式切换命令规格

## 概述

定义 `.mode` 命令的行为，用于切换骰子系统的工作模式。

## 源文件

**位置**: `src/plugins/DicePP/module/common/mode_command.py`

## 命令语法

```
.mode [模式名称]
```

## 支持的模式

| 模式 | 别名 | 默认骰 | 说明 |
|------|------|--------|------|
| `coc` | coc, COC | D100 | 克苏鲁的呼唤 |
| `dnd` | dnd, DND, 5e | D20 | 龙与地下城 |
| `general` | 通用 | D20 | 通用模式 |

## 命令规格

### SPEC-P3-M001: 查询当前模式

**语法**: `.mode`

**行为**:
- 显示当前群的模式设置

**响应包含**:
- 当前模式名称

### SPEC-P3-M002: 切换模式

**语法**: `.mode <模式名称>`

**行为**:
1. 验证模式名称有效
2. 更新群配置
3. 返回切换成功消息

**成功响应包含**:
- 新模式名称

**错误场景**:
- 无效模式名 → 提示可用模式列表

## 模式影响

### SPEC-P3-M010: 默认骰子

| 模式 | `.r` 默认 |
|------|-----------|
| coc | 1D100 |
| dnd | 1D20 |
| general | 1D20 |

**验收标准**:
```python
async def test_mode_affects_default_dice(fresh_bot):
    bot, proxy = fresh_bot
    meta = make_group_meta("g1", "u1")
    
    # 切换到 COC
    await send_and_check(bot, ".mode coc", meta, lambda s: True)
    
    # .r 应该使用 D100
    await send_and_check(bot, ".r", meta, 
        lambda s: "D100" in s or "d100" in s)
    
    # 切换到 DnD
    await send_and_check(bot, ".mode dnd", meta, lambda s: True)
    
    # .r 应该使用 D20
    await send_and_check(bot, ".r", meta,
        lambda s: "D20" in s or "d20" in s)
```

### SPEC-P3-M011: 技能检定

| 模式 | 技能检定规则 |
|------|--------------|
| coc | 百分骰，对比技能值 |
| dnd | D20 + 调整值，对比 DC |

## 持久化

### SPEC-P3-M020: 模式持久化

**行为**:
- 模式设置保存到群配置
- Bot 重启后保持

**测试方法**:
```python
async def test_mode_persistence(fresh_bot):
    bot, proxy = fresh_bot
    meta = make_group_meta("g1", "u1")
    
    # 设置模式
    await send_and_check(bot, ".mode coc", meta, lambda s: True)
    
    # 模拟 Bot 重启（重新初始化）
    # 注意：这需要特殊的测试设置
    
    # 验证模式保持
    await send_and_check(bot, ".mode", meta,
        lambda s: "coc" in s.lower())
```

## 权限

### SPEC-P3-M030: 权限控制

**默认**:
- 任何群成员都可以切换模式

**可配置**:
- 管理员限制（如果实现）

## 完整测试套件

```python
@pytest.mark.integration
class TestModeCommand:
    async def test_query_mode(self, fresh_bot):
        """查询当前模式"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        await send_and_check(bot, ".mode", meta,
            lambda s: len(s) > 0)  # 有响应即可

    async def test_switch_to_coc(self, fresh_bot):
        """切换到 COC 模式"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        await send_and_check(bot, ".mode coc", meta,
            lambda s: "coc" in s.lower() or "克苏鲁" in s)

    async def test_switch_to_dnd(self, fresh_bot):
        """切换到 DnD 模式"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        await send_and_check(bot, ".mode dnd", meta,
            lambda s: "dnd" in s.lower() or "龙与地下城" in s)

    async def test_invalid_mode(self, fresh_bot):
        """无效模式名"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        await send_and_check(bot, ".mode invalid_mode_xyz", meta,
            lambda s: "无效" in s or "未知" in s or "可用" in s)

    async def test_mode_affects_roll(self, fresh_bot):
        """模式影响默认骰"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        await send_and_check(bot, ".mode coc", meta, lambda s: True)
        result = await send_and_check(bot, ".r", meta, lambda s: True)
        # 检查结果中是否包含 D100 相关内容
        result_str = "\n".join(str(c) for c in result)
        assert "100" in result_str or "D100" in result_str
```

## 前置条件

在编写测试前，需要先阅读以下文件确认实际 API：
- `module/common/mode_command.py`
- `Data/Config/mode_setting.xlsx`（模式配置表）
