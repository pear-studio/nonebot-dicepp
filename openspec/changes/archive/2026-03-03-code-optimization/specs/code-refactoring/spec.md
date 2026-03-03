## ADDED Requirements

### Requirement: 代码重构规范

代码重构 SHALL 遵循以下规范：

#### SPEC-P1-001: 日志命令拆分
`module/common/log_command.py` SHALL 拆分为以下独立模块：

| 模块 | 功能 |
|------|------|
| `log_query_command.py` | 日志查询 `.log` |
| `log_record_command.py` | 日志记录 |
| `log_stat_command.py` | 日志统计 |
| `log_export_command.py` | 日志导出 |

#### SPEC-P1-002: 异常处理细化
所有 `except Exception:` SHALL 改为具体异常类型：

- `AttributeError`: 属性访问错误
- `TypeError`: 类型错误
- `KeyError`: 字典键错误
- `ValueError`: 值错误
- `DataManagerError`: 数据管理错误（自定义）

#### SPEC-P1-003: 角色系统基类提取
`character/coc/` 和 `character/dnd5e/` SHALL 提取以下公共基类：

- `character/base/ability.py`: BaseAbility
- `character/base/health.py`: BaseHealth
- `character/base/hp_command.py`: BaseHPCommand
- `character/base/money.py`: BaseMoney
- `character/base/spell.py`: BaseSpell

#### SPEC-P1-004: 类型注解要求
核心函数 SHALL 添加返回类型注解：

- `process_msg()` -> `List[BotCommandBase]`
- `can_process_msg()` -> `Tuple[bool, bool, Any]`
- `get_help()` -> `str`
- `get_description()` -> `str`

#### SPEC-P1-005: 测试覆盖要求
重构后 SHALL 保持现有测试通过，并满足：

- `pytest` 运行无错误
- 关键路径有单元测试覆盖

### Scenario: 拆分日志命令后功能一致

- **WHEN** 用户执行 `.log` 查询命令
- **THEN** 返回结果与原 `log_command.py` 一致

### Scenario: 异常处理细化后错误可追踪

- **WHEN** 发生异常时
- **THEN** 日志显示具体异常类型和堆栈

### Scenario: 角色基类提取后行为不变

- **WHEN** 用户使用 CoC 或 DnD5e 角色卡
- **THEN** 功能与重构前一致
