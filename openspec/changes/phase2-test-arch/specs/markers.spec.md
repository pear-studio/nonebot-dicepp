# Spec: pytest Marker 规格

## 概述

定义项目使用的 pytest marker 标签，用于测试分类和选择性运行。

## 配置位置

**文件**: `pyproject.toml` 的 `[tool.pytest.ini_options]` 节

## Marker 定义

### SPEC-P2-030: markers 配置

```toml
[tool.pytest.ini_options]
markers = [
    "unit: 纯单元测试，不需要 Bot 实例",
    "integration: 完整 Bot 集成测试",
    "slow: 执行时间 > 1秒的测试（如蒙特卡洛统计）",
    "karma: 业力骰子系统测试",
    "log: 日志系统测试",
    "coc: COC 角色卡测试",
    "dnd: DnD 角色卡测试",
]
```

## Marker 语义规格

### SPEC-P2-031: unit

**含义**: 纯单元测试，不依赖 Bot 实例

**适用场景**:
- 测试独立的数据结构（如 `KarmaConfig`, `KarmaState`）
- 测试纯函数（如表达式解析）
- 测试序列化/反序列化

**特征**:
- 不使用 `shared_bot` 或 `fresh_bot` fixture
- 执行速度快（通常 < 100ms）
- 无 I/O 操作

**示例**:
```python
@pytest.mark.unit
class TestKarmaConfig:
    def test_roundtrip(self):
        cfg = KarmaConfig()
        assert KarmaConfig.from_dict(cfg.to_dict()).mode == cfg.mode
```

### SPEC-P2-032: integration

**含义**: 需要完整 Bot 实例的集成测试

**适用场景**:
- 测试命令处理流程
- 测试模块间交互
- 测试配置持久化

**特征**:
- 使用 `shared_bot` 或 `fresh_bot` fixture
- 可能涉及文件 I/O
- 执行时间相对较长

**示例**:
```python
@pytest.mark.integration
class TestRollCommand:
    async def test_basic_roll(self, fresh_bot):
        bot, proxy = fresh_bot
        # ...
```

### SPEC-P2-033: slow

**含义**: 执行时间较长的测试

**适用场景**:
- 蒙特卡洛统计测试（多次采样）
- 压力测试
- 需要大量数据的测试

**阈值**: 单个测试用例执行时间 > 1 秒

**组合使用**:
```python
@pytest.mark.unit
@pytest.mark.slow
class TestKarmaEngines:
    # 500 次采样的统计测试
    pass
```

### SPEC-P2-034: 功能域 marker

| Marker | 对应模块 |
|--------|----------|
| `karma` | `module/roll/karma_*.py` |
| `log` | `module/common/log_*.py` |
| `coc` | `module/character/coc/` |
| `dnd` | `module/character/dnd5e/` |

**用途**: 按功能域筛选测试

## 运行命令示例

```bash
# 只运行单元测试
pytest -m unit

# 只运行集成测试
pytest -m integration

# 运行单元测试，排除慢速测试
pytest -m "unit and not slow"

# 只运行业力骰子相关测试
pytest -m karma

# 运行日志和角色卡相关测试
pytest -m "log or coc or dnd"

# 运行所有非慢速测试
pytest -m "not slow"
```

## 标注规范

### 测试类标注

- 测试类**必须**标注 `unit` 或 `integration`
- 如果类中所有方法都属于同一功能域，在类级别标注功能 marker
- `slow` 标记应精确到方法级别

**正确示例**:
```python
@pytest.mark.unit
@pytest.mark.karma
class TestKarmaConfig:
    def test_default(self): ...
    def test_custom(self): ...

@pytest.mark.integration
@pytest.mark.karma
class TestKarmaCommand:
    async def test_enable(self, fresh_bot): ...
    
    @pytest.mark.slow
    async def test_statistical_behavior(self, fresh_bot): ...
```

**错误示例**:
```python
# ❌ 缺少 unit/integration 标记
class TestKarmaConfig:
    pass

# ❌ slow 不应该用于整个类
@pytest.mark.slow
class TestKarmaEngines:
    pass
```

## 验收标准

1. `pytest -m unit` 不应执行任何需要 Bot 实例的测试
2. `pytest -m integration` 不应执行纯数据结构测试
3. `pytest -m "not slow"` 应在 30 秒内完成（取决于测试数量）
4. 所有测试类都有 `unit` 或 `integration` 标记
