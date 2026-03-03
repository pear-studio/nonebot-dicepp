# Spec: KarmaConfig 接口规格

## 概述

定义业力骰子配置类 `KarmaConfig` 的数据结构和序列化行为。

## 源文件

**位置**: `src/plugins/DicePP/module/roll/karma_manager.py`

## 类定义

```python
@dataclass
class KarmaConfig:
    is_enabled: bool = False
    mode: str = "custom"
    engine: str = "precise"
    custom_percentage: int = DEFAULT_PERCENTAGE  # 60
    custom_roll_count: int = DEFAULT_WINDOW      # 20
    intro_sent: bool = False
```

## 字段规格

### SPEC-P3-K001: is_enabled

| 属性 | 值 |
|------|-----|
| 类型 | `bool` |
| 默认值 | `False` |
| 含义 | 群是否启用业力骰子 |

### SPEC-P3-K002: mode

| 属性 | 值 |
|------|-----|
| 类型 | `str` |
| 默认值 | `"custom"` |
| 有效值 | `"custom"`, `"balanced"`, `"dramatic"`, `"hero"`, `"grim"`, `"stable"` |

**模式说明**:

| 模式 | 别名 | 目标期望 | 窗口大小 |
|------|------|----------|----------|
| `custom` | 自定义 | 用户设定 | 用户设定 |
| `balanced` | 均衡/均衡稳定 | 55% | 15 |
| `dramatic` | 戏剧化 | - | - |
| `hero` | 主角光环 | 65% | 15 |
| `grim` | 冷酷现实 | 40% | 25 |
| `stable` | 高斯稳定 | - | - |

### SPEC-P3-K003: engine

| 属性 | 值 |
|------|-----|
| 类型 | `str` |
| 默认值 | `"precise"` |
| 有效值 | `"advantage"`, `"precise"` |

**引擎说明**:

| 引擎 | 别名 | 算法 |
|------|------|------|
| `advantage` | 优势判定/adv/优势 | 投 N 次取最大/最小 |
| `precise` | 精确加权/precision/精确 | 加权随机分布 |

### SPEC-P3-K004: custom_percentage

| 属性 | 值 |
|------|-----|
| 类型 | `int` |
| 默认值 | `60` |
| 有效范围 | `1` - `100` |
| 含义 | 自定义模式的目标期望百分比 |

### SPEC-P3-K005: custom_roll_count

| 属性 | 值 |
|------|-----|
| 类型 | `int` |
| 默认值 | `20` |
| 有效范围 | `1` - `200` (`MAX_WINDOW`) |
| 含义 | 滑动窗口大小（历史记录数量） |

### SPEC-P3-K006: intro_sent

| 属性 | 值 |
|------|-----|
| 类型 | `bool` |
| 默认值 | `False` |
| 含义 | 是否已发送过首次启用介绍 |

## 序列化规格

### SPEC-P3-K010: to_dict

**签名**: `def to_dict(self) -> Dict[str, object]`

**行为**:
- 返回包含所有字段的字典
- 字典键名与字段名完全一致

**返回值示例**:
```python
{
    "is_enabled": True,
    "mode": "hero",
    "engine": "precise",
    "custom_percentage": 70,
    "custom_roll_count": 30,
    "intro_sent": True,
}
```

### SPEC-P3-K011: from_dict

**签名**: `@classmethod def from_dict(cls, data: Optional[Dict[str, object]]) -> "KarmaConfig"`

**行为**:

| 输入 | 输出 |
|------|------|
| `None` | 默认配置 |
| `{}` | 默认配置 |
| 部分字段 | 缺失字段使用默认值 |
| 完整字段 | 完整配置 |

**边界条件**:
- `data` 为 `None` 时不抛异常
- 未知字段被忽略
- 类型不匹配时强制转换（`bool()`, `str()`, `int()`）

## 测试用例规格

### TC-K001: 默认配置往返测试

```python
def test_roundtrip_default():
    cfg = KarmaConfig()
    restored = KarmaConfig.from_dict(cfg.to_dict())
    assert cfg.is_enabled == restored.is_enabled
    assert cfg.mode == restored.mode
    assert cfg.engine == restored.engine
    assert cfg.custom_percentage == restored.custom_percentage
    assert cfg.custom_roll_count == restored.custom_roll_count
    assert cfg.intro_sent == restored.intro_sent
```

### TC-K002: 自定义配置往返测试

```python
def test_roundtrip_custom():
    cfg = KarmaConfig(
        is_enabled=True,
        mode="hero",
        engine="advantage",
        custom_percentage=75,
        custom_roll_count=50,
        intro_sent=True,
    )
    restored = KarmaConfig.from_dict(cfg.to_dict())
    assert restored.mode == "hero"
    assert restored.engine == "advantage"
    assert restored.custom_percentage == 75
```

### TC-K003: from_dict(None) 测试

```python
def test_from_dict_none():
    cfg = KarmaConfig.from_dict(None)
    assert cfg is not None
    assert cfg.is_enabled == False
    assert cfg.mode == "custom"
```

### TC-K004: 部分字段测试

```python
def test_from_dict_partial():
    cfg = KarmaConfig.from_dict({"mode": "grim"})
    assert cfg.mode == "grim"
    assert cfg.engine == "advantage"  # 默认值
    assert cfg.custom_percentage == 60  # 默认值
```

### TC-K005: 未知字段测试

```python
def test_from_dict_unknown_fields():
    cfg = KarmaConfig.from_dict({
        "mode": "balanced",
        "unknown_field": "should_be_ignored",
        "another_unknown": 12345,
    })
    assert cfg.mode == "balanced"
    # 不抛异常
```
