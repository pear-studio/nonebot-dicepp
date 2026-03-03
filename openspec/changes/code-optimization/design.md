## Context

### 当前状态
- `log_command.py`: 1700+ 行，72 个方法，包含日志查询、记录、统计、导出功能
- 异常处理: 91 处 `except Exception:` 吞掉所有异常
- 角色系统: `coc/` 和 `dnd5e/` 各有 5 个重复文件

### 约束
- 保持功能兼容，不改变用户可见行为
- 使用 pytest + pytest-asyncio 测试
- 遵循现有代码风格

## Goals / Non-Goals

**Goals:**
- 拆分 `log_command.py` 为多个独立命令类
- 细化异常处理为具体异常类型
- 提取角色系统公共基类
- 添加类型注解

**Non-Goals:**
- 不改变命令行接口
- 不改变数据存储格式
- 不添加新功能

## Decisions

### 1. log_command.py 拆分方案

**方案**: 按功能拆分为 4 个独立命令类

| 新文件 | 功能 |
|--------|------|
| `log_query_command.py` | 日志查询 (.log) |
| `log_record_command.py` | 日志记录 |
| `log_stat_command.py` | 日志统计 |
| `log_export_command.py` | 日志导出 (Word/Excel) |

**迁移**: 保持原 `log_command.py` 作为兼容层，逐步迁移

### 2. 异常处理优化

**方案**: 将 `except Exception:` 改为具体异常类型

```python
# Before
except Exception:
    dice_log(...)

# After
except (AttributeError, TypeError, KeyError) as e:
    dice_log(f"Tick error: {e}")
```

### 3. 角色系统公共基类

**方案**: 创建 `character/base/` 目录

```
character/base/
    __init__.py
    ability.py      # BaseAbility (抽象类)
    health.py       # BaseHealth (抽象类)
    hp_command.py   # BaseHPCommand (抽象类)
    money.py        # BaseMoney (抽象类)
    spell.py        # BaseSpell (抽象类)
```

继承关系:
```
BaseAbility <-- COCAbility, DND5eAbility
BaseHealth <-- COCHealth, DND5eHealth
```

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| 拆分导致接口变化 | 保持原接口兼容层 |
| 测试覆盖不足 | 补充单元测试 |
| 回归问题 | 运行 pytest 验证 |
| 迁移周期长 | 分步骤实施 |

## Migration Plan

1. **Phase 1**: 拆分 `log_command.py`
   - 创建新命令类
   - 迁移测试
   - 验证功能

2. **Phase 2**: 异常处理优化
   - 逐模块处理
   - 优先核心模块 (dicebot.py, roll/)

3. **Phase 3**: 角色系统重构
   - 创建基类
   - 迁移 CoC
   - 迁移 DnD5e

## Open Questions

- 是否需要完全移除原 `log_command.py`？
- 异常处理细化到何种程度？
