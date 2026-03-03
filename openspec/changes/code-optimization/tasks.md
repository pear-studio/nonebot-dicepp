## 1. 异常处理优化

- [x] 1.1 分析并列出 `dicebot.py` 中的 `except Exception:` 用法
- [x] 1.2 将 `dicebot.py` 异常处理改为具体类型
- [x] 1.3 分析并列出 `roll/` 模块中的异常处理
- [x] 1.4 将 `roll/` 模块异常处理改为具体类型
- [x] 1.5 将 `log_command.py` 异常处理改为具体类型 (约20处)

## 2. 日志命令拆分

- [x] 2.0 分析 log_command.py 结构 (已有部分拆分: LogCommand, LogRecorderCommand, LogStatCommand)
- [x] 2.1-2.4 已有部分实现，无需额外拆分
- [x] 2.5 保持 `log_command.py` 作为兼容层 (现有设计已满足)
- [x] 2.6 运行 pytest 验证功能一致

## 3. 角色系统公共基类

- [x] 3.1 创建 `character/base/__init__.py`
- [x] 3.2 创建 `character/base/ability.py` (从 coc/dnd5e 提取)
- [x] 3.3 创建 `character/base/health.py` (从 coc/dnd5e 提取)
- [x] 3.4 创建 `character/base/money.py` (从 coc/dnd5e 提取)
- [x] 3.5 创建 `character/base/spell.py` (从 coc/dnd5e 提取)
- [x] 3.6 创建 `character/base/character.py` (从 coc/dnd5e 提取)
- [x] 3.7 重构 `character/coc/` 从 base/ 导入
- [x] 3.8 重构 `character/dnd5e/` 从 base/ 导入

## 4. 类型注解完善

- [x] 4.1 验证 roll/ 模块核心函数已有类型注解
- [x] 4.2 验证 character/ 模块核心函数已有类型注解
- [x] 4.3 验证 common/ 模块核心函数已有类型注解

**结论**: 核心函数 (can_process_msg, process_msg, get_help, get_description) 已有完整类型注解

## 5. 测试验证

- [x] 5.1 运行 `pytest` 验证现有测试通过
