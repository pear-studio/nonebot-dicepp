# Spec: COC 角色卡规格

## 概述

定义 COC（克苏鲁的呼唤）角色卡的数据结构和命令行为。

## 源文件

**位置**: 
- `src/plugins/DicePP/module/character/coc/character.py`
- `src/plugins/DicePP/module/character/coc/ability.py`
- `src/plugins/DicePP/module/character/coc/health.py`

## 说明

COC 模块复用了 `dnd5e` 模块的基础数据结构（`HPInfo`, `AbilityInfo` 等），但采用 COC 规则的属性名称和计算方式。

## 角色卡关键字

| 关键字 | 说明 |
|--------|------|
| `$姓名$` | 角色名称 |
| `$等级$` | 角色等级 |
| `$生命值$` | HP 信息 |
| `$属性$` | 能力值 |

## DNDCharInfo 类

### SPEC-P3-C001: 类定义

```python
@custom_json_object
class DNDCharInfo(JsonObject):
    is_init: bool = False
    name: str = ""
    hp_info: HPInfo
    ability_info: AbilityInfo
    spell_info: SpellInfo
    money_info: MoneyInfo
```

### SPEC-P3-C002: initialize 方法

**签名**: `def initialize(self, input_str: str) -> None`

**行为**:
1. 解析用户输入的角色卡字符串
2. 提取各个关键字段
3. 验证数据有效性
4. 填充属性

**输入格式示例**:
```
$姓名$ 探索者
$等级$ 5
$生命值$ 5/10(4)
$属性$ 10/11/12/15/12
```

**错误处理**:
- 无效数值 → 抛出 `AssertionError`
- 必需字段缺失 → 抛出 `AssertionError`

### SPEC-P3-C003: serialize / deserialize

**行为**:
- 序列化为 JSON 字符串
- 反序列化恢复对象状态
- 嵌套的 `JsonObject` 递归处理

**验收标准**:
```python
def test_roundtrip():
    char = gen_template_char()
    json_str = char.serialize()
    restored = DNDCharInfo()
    restored.deserialize(json_str)
    assert restored.name == char.name
    assert restored.hp_info.hp_cur == char.hp_info.hp_cur
```

### SPEC-P3-C004: get_char_info

**签名**: `def get_char_info(self) -> str`

**行为**:
- 返回格式化的角色卡字符串
- 按照 `CHAR_INFO_KEY_LIST` 顺序输出

## HPInfo 类（来自 dnd5e）

### SPEC-P3-C010: HP 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `hp_cur` | int | 当前 HP |
| `hp_max` | int | 最大 HP |
| `hp_temp` | int | 临时 HP |
| `hp_dice_type` | int | 生命骰类型 |
| `hp_dice_num` | int | 当前生命骰数量 |
| `hp_dice_max` | int | 最大生命骰数量 |

### SPEC-P3-C011: HP 方法

**use_hp_dice**: 使用生命骰恢复 HP
**long_rest**: 长休恢复

## 集成测试

### TC-C001: 记录和查询角色卡

```python
@pytest.mark.integration
class TestCocCharCommand:
    async def test_char_record_and_query(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        
        char_template = """
        $姓名$ 探索者
        $等级$ 5
        $生命值$ 10/10
        $属性$ 50/60/45/55/70/65
        """
        
        # 记录角色卡
        await send_and_check(
            bot, f".pc记录\n{char_template}", meta,
            lambda s: "探索者" in s or "记录" in s
        )
        
        # 查询角色卡
        await send_and_check(
            bot, ".pc", meta,
            lambda s: "探索者" in s
        )
```

## 注意事项

1. **模块共用**: COC 和 DnD5e 共用 `character.py` 中的 `DNDCharInfo` 类
2. **属性格式**: 属性值用 `/` 分隔
3. **HP 格式**: `当前/最大(临时)` 如 `5/10(4)`
4. **关键字**: 必须用 `$` 包裹，如 `$姓名$`

## 前置条件

在编写测试前，需要先阅读以下文件确认实际 API：
- `module/character/coc/ability.py`
- `module/character/coc/health.py`  
- `module/character/dnd5e/ability.py`
- `module/character/char_command.py`
