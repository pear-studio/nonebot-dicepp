# Spec: KarmaState 接口规格

## 概述

定义业力骰子状态类 `KarmaState` 的滑动窗口行为。

## 源文件

**位置**: `src/plugins/DicePP/module/roll/karma_manager.py`

## 类定义

```python
class KarmaState:
    def __init__(self):
        self.window: int = DEFAULT_WINDOW  # 20
        self.history: Deque[float] = deque()
```

## 属性规格

### SPEC-P3-KS001: window

| 属性 | 值 |
|------|-----|
| 类型 | `int` |
| 默认值 | `20` (`DEFAULT_WINDOW`) |
| 有效范围 | `1` - `200` (`MAX_WINDOW`) |
| 含义 | 滑动窗口最大容量 |

### SPEC-P3-KS002: history

| 属性 | 值 |
|------|-----|
| 类型 | `Deque[float]` |
| 默认值 | 空 deque |
| 含义 | 归一化后的历史骰值（0-100） |

## 方法规格

### SPEC-P3-KS010: resize

**签名**: `def resize(self, window: int) -> None`

**行为**:
1. 将 `window` 限制在 `[1, MAX_WINDOW]` 范围内
2. 如果新窗口大小与当前相同，直接返回
3. 更新 `self.window`
4. 如果历史长度超过新窗口大小，从左侧丢弃多余元素

**边界条件**:

| 输入 | 结果 |
|------|------|
| `window <= 0` | 设为 1 |
| `window > 200` | 设为 200 |
| `window == self.window` | 无操作 |

**测试用例**:
```python
def test_resize_shrink():
    state = KarmaState()
    for v in [1, 2, 3, 4, 5]:
        state.append(v)
    state.resize(3)
    assert state.window == 3
    assert len(state.history) == 3
    assert list(state.history) == [3, 4, 5]  # 保留最新的

def test_resize_clamp():
    state = KarmaState()
    state.resize(0)
    assert state.window == 1
    state.resize(999)
    assert state.window == 200
```

### SPEC-P3-KS011: append

**签名**: `def append(self, value: float) -> None`

**行为**:
1. 将 `value` 追加到 `history` 右侧
2. 如果长度超过 `window`，从左侧弹出多余元素

**测试用例**:
```python
def test_append_overflow():
    state = KarmaState()
    state.resize(3)
    for v in [10, 20, 30, 40]:
        state.append(v)
    assert len(state.history) == 3
    assert list(state.history) == [20, 30, 40]
```

### SPEC-P3-KS012: average

**签名**: `def average(self) -> float`

**行为**:
- 返回 `history` 中所有值的算术平均
- 如果 `history` 为空，返回 `50.0`

**测试用例**:
```python
def test_average_normal():
    state = KarmaState()
    for v in [10, 20, 30, 40, 50]:
        state.append(v)
    assert state.average() == 30.0

def test_average_empty():
    state = KarmaState()
    assert state.average() == 50.0
```

### SPEC-P3-KS013: last

**签名**: `def last(self) -> Optional[float]`

**行为**:
- 返回 `history` 中最后一个值
- 如果 `history` 为空，返回 `None`

**测试用例**:
```python
def test_last_normal():
    state = KarmaState()
    state.append(42.0)
    state.append(99.0)
    assert state.last() == 99.0

def test_last_empty():
    state = KarmaState()
    assert state.last() is None
```

### SPEC-P3-KS014: tail

**签名**: `def tail(self, count: int) -> List[float]`

**行为**:
- 返回 `history` 中最后 `count` 个值的列表
- 如果 `count <= 0` 或 `history` 为空，返回空列表
- 如果 `count > len(history)`，返回全部元素

**测试用例**:
```python
def test_tail_normal():
    state = KarmaState()
    for v in [10, 20, 30, 40, 50]:
        state.append(v)
    assert state.tail(3) == [30, 40, 50]
    assert state.tail(10) == [10, 20, 30, 40, 50]

def test_tail_zero():
    state = KarmaState()
    state.append(10)
    assert state.tail(0) == []
    assert state.tail(-1) == []

def test_tail_empty():
    state = KarmaState()
    assert state.tail(5) == []
```

## 完整测试套件

```python
@pytest.mark.unit
@pytest.mark.karma
class TestKarmaState:
    def test_initial_state(self):
        state = KarmaState()
        assert state.window == 20
        assert len(state.history) == 0
        assert state.average() == 50.0
        assert state.last() is None
        assert state.tail(5) == []

    def test_append_and_average(self):
        state = KarmaState()
        state.resize(5)
        for v in [20.0, 40.0, 60.0, 80.0, 100.0]:
            state.append(v)
        assert abs(state.average() - 60.0) < 0.001

    def test_window_overflow(self):
        state = KarmaState()
        state.resize(3)
        for v in [100.0, 100.0, 100.0, 10.0]:
            state.append(v)
        # 窗口保留 [100, 100, 10]，平均 70
        assert abs(state.average() - 70.0) < 0.001

    def test_resize_preserves_recent(self):
        state = KarmaState()
        for v in range(1, 11):  # 1-10
            state.append(float(v))
        state.resize(5)
        assert state.tail(5) == [6.0, 7.0, 8.0, 9.0, 10.0]
```
