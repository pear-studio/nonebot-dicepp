## 1. 异常处理优化

- [x] 1.1 分析并列出 `dicebot.py` 中的 `except Exception:` 用法
- [x] 1.2 将 `dicebot.py` 异常处理改为具体类型
- [x] 1.3 分析并列出 `roll/` 模块中的异常处理
- [x] 1.4 将 `roll/` 模块异常处理改为具体类型
- [x] 1.5 将 `log_command.py` 异常处理改为具体类型 (约20处)

## 2. 日志命令拆分

- [x] 2.0 分析 log_command.py 结构 (已有部分拆分: LogCommand, LogRecorderCommand, LogStatCommand)
- [ ] 2.1 创建 `module/common/log_query_command.py` (日志查询) - 已有部分实现
- [ ] 2.2 创建 `module/common/log_record_command.py` (日志记录) - 已有 LogRecorderCommand
- [ ] 2.3 创建 `module/common/log_stat_command.py` (日志统计) - 已有 LogStatCommand
- [ ] 2.4 创建 `module/common/log_export_command.py` (日志导出) - 已有部分实现
- [x] 2.5 保持 `log_command.py` 作为兼容层 (现有设计已满足)
- [x] 2.6 运行 pytest 验证功能一致

## 3. 角色系统公共基类

- [ ] 3.1 创建 `character/base/__init__.py`
- [ ] 3.2 创建 `character/base/ability.py` (BaseAbility 抽象类)
- [ ] 3.3 创建 `character/base/health.py` (BaseHealth 抽象类)
- [ ] 3.4 创建 `character/base/hp_command.py` (BaseHPCommand 抽象类)
- [ ] 3.5 创建 `character/base/money.py` (BaseMoney 抽象类)
- [ ] 3.6 创建 `character/base/spell.py` (BaseSpell 抽象类)
- [ ] 3.7 重构 `character/coc/` 继承基类
- [ ] 3.8 重构 `character/dnd5e/` 继承基类

## 4. 类型注解完善

- [ ] 4.1 为 `roll/` 模块核心函数添加类型注解
- [ ] 4.2 为 `character/` 模块核心函数添加类型注解
- [ ] 4.3 为 `common/` 模块核心函数添加类型注解

## 5. 测试验证

- [x] 5.1 运行 `pytest` 验证现有测试通过
- [ ] 5.2 补充缺失的单元测试
- [ ] 5.3 检查测试覆盖率
