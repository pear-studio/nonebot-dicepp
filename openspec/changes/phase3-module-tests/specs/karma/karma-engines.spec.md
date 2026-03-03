# Spec: Karma 引擎行为规格

## 概述

定义业力骰子各引擎的统计行为特征，用于蒙特卡洛测试验证。

## 源文件

**位置**: `src/plugins/DicePP/module/roll/karma_manager.py`

## 引擎列表

| 引擎 | 内部方法 |
|------|----------|
| standard | `_roll_standard` |
| advantage | `_roll_advantage` |
| precise | `_roll_precise` |
| dramatic | `_roll_dramatic` |
| stable | `_roll_stable` |

## 标准引擎 (standard)

### SPEC-P3-KE001: _roll_standard

**签名**: `def _roll_standard(self, dice_type: int) -> int`

**行为**:
- 返回 `[1, dice_type]` 范围内的均匀随机整数

**统计特征**:
- 期望值: `(1 + dice_type) / 2`
- 归一化期望: `50%`
- 方差: 均匀分布标准方差

**测试验收标准**:
```python
# 500 次采样，归一化平均值应在 [0.4, 0.6] 范围内
def test_standard_is_uniform():
    samples = [roll_standard(20) for _ in range(500)]
    normalized = [s / 20 for s in samples]
    avg = sum(normalized) / len(normalized)
    assert 0.4 < avg < 0.6
```

## 优势引擎 (advantage)

### SPEC-P3-KE010: _roll_advantage

**签名**: `def _roll_advantage(self, dice_type: int, direction: str) -> int`

**参数**:
- `dice_type`: 骰子面数
- `direction`: `"up"` 或 `"down"`

**行为**:
- 投掷 `ADVANTAGE_ROLLS` (3) 次
- `direction == "up"`: 返回最大值
- `direction == "down"`: 返回最小值

**统计特征**:
| 方向 | 期望偏移 |
|------|----------|
| up | 高于均匀分布 |
| down | 低于均匀分布 |

**测试验收标准**:
```python
def test_advantage_up_skews_high():
    samples = [_roll_advantage(20, "up") for _ in range(500)]
    avg = sum(samples) / len(samples)
    # 3 次取最大，期望约 15.5 (d20)
    assert avg > 12.0

def test_advantage_down_skews_low():
    samples = [_roll_advantage(20, "down") for _ in range(500)]
    avg = sum(samples) / len(samples)
    # 3 次取最小，期望约 5.5 (d20)
    assert avg < 9.0
```

## 精确引擎 (precise)

### SPEC-P3-KE020: _roll_precise

**签名**: `def _roll_precise(self, dice_type: int, direction: str, diff: float) -> int`

**参数**:
- `dice_type`: 骰子面数
- `direction`: `"up"` 或 `"down"`
- `diff`: 与目标期望的差值（影响加权强度）

**行为**:
- 对每个面计算权重
- `direction == "up"`: 高面权重更大
- `direction == "down"`: 低面权重更大
- 权重比例由 `diff` 决定（差距越大，偏向越强）

**权重公式**:
```python
ratio = max(diff / 100.0, PRECISION_MIN_RATIO)  # 0.05
ratio = min(ratio, 0.95)

for face in faces:
    norm = (face - 1) / (dice_type - 1)
    if direction == "up":
        weight = 1.0 + ratio * norm
    else:
        weight = 1.0 + ratio * (1.0 - norm)
```

**统计特征**:
- diff 越大，偏向越明显
- 最小偏向比例: 5%
- 最大偏向比例: 95%

## 戏剧化引擎 (dramatic)

### SPEC-P3-KE030: _roll_dramatic

**签名**: `def _roll_dramatic(self, dice_type: int) -> int`

**行为**:
1. 定义 `edge = max(1, dice_type // 5)`
2. 45% 概率返回低区 `[1, edge]`
3. 45% 概率返回高区 `[dice_type - edge + 1, dice_type]`
4. 10% 概率返回均匀随机

**统计特征**:
- 双峰分布
- 极端值出现频率高于均匀分布

**测试验收标准**:
```python
def test_dramatic_is_bimodal():
    samples = [_roll_dramatic(20) for _ in range(500)]
    edge = 4  # 20 // 5
    low_count = sum(1 for s in samples if s <= edge)
    high_count = sum(1 for s in samples if s >= 17)  # 20 - 4 + 1
    # 低区和高区各约 45%
    assert low_count > 150  # 期望 225
    assert high_count > 150  # 期望 225
```

## 稳定引擎 (stable)

### SPEC-P3-KE040: _roll_stable

**签名**: `def _roll_stable(self, dice_type: int) -> int`

**行为**:
1. 投掷 3 次，计算平均值
2. 四舍五入取整
3. 限制在 `[1, dice_type]` 范围内

**统计特征**:
- 类高斯分布（中心极限定理）
- 方差小于均匀分布
- 期望值接近均匀分布

**测试验收标准**:
```python
def test_stable_lower_variance():
    stable_samples = [_roll_stable(20) for _ in range(500)]
    uniform_samples = [random.randint(1, 20) for _ in range(500)]
    
    stable_var = variance(stable_samples)
    uniform_var = variance(uniform_samples)
    
    assert stable_var < uniform_var
```

## 模式与引擎对应关系

| 模式 | 使用的引擎 | 方向决定逻辑 |
|------|------------|--------------|
| custom | 用户选择 | 基于历史平均 |
| balanced | precise | 目标 55% |
| hero | 用户选择 | 目标 65%，强制上修 |
| grim | 用户选择 | 目标 40%，强制下修 |
| dramatic | dramatic | 忽略引擎设置 |
| stable | stable | 忽略引擎设置 |

## 方向决定逻辑 (_determine_direction)

### SPEC-P3-KE050: _determine_direction

**签名**: 
```python
def _determine_direction(
    self, mode: str, state: KarmaState, target: float
) -> Tuple[Optional[str], bool, float]
```

**返回值**:
- `direction`: `"up"`, `"down"`, 或 `None`
- `forced`: 是否强制修正
- `current_avg`: 当前平均值

**逻辑规则**:

| 模式 | 条件 | direction | forced |
|------|------|-----------|--------|
| hero | 最近 3 次都 < 40 | "up" | True |
| grim | 上一次 > 95 | "down" | True |
| 其他 | avg < target - 0.5 | "up" | False |
| 其他 | avg > target + 0.5 | "down" | False |
| 其他 | 接近目标 | None | False |

## 蒙特卡洛测试套件

```python
@pytest.mark.unit
@pytest.mark.karma
@pytest.mark.slow
class TestKarmaEngines:
    SAMPLES = 500
    
    def test_hero_mode_skews_high(self, fresh_bot):
        """hero 模式长期平均应高于 0.5"""
        bot, _ = fresh_bot
        manager = get_karma_manager(bot)
        manager.enable("g1")
        manager.set_mode("g1", "hero")
        
        # 预热：喂低值历史
        for _ in range(20):
            manager.generate_value("g1", "u1", 20)
        
        samples = [manager.generate_value("g1", "u1", 20) / 20 
                   for _ in range(self.SAMPLES)]
        avg = sum(samples) / len(samples)
        assert avg > 0.50

    def test_grim_mode_skews_low(self, fresh_bot):
        """grim 模式长期平均应低于 0.55"""
        bot, _ = fresh_bot
        manager = get_karma_manager(bot)
        manager.enable("g1")
        manager.set_mode("g1", "grim")
        
        for _ in range(20):
            manager.generate_value("g1", "u1", 20)
        
        samples = [manager.generate_value("g1", "u1", 20) / 20 
                   for _ in range(self.SAMPLES)]
        avg = sum(samples) / len(samples)
        assert avg < 0.55
```
