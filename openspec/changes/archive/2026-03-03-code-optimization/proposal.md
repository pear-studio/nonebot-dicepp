## Why

DicePP 项目代码存在以下问题影响可维护性和稳定性：
1. `log_command.py` 包含 72 个方法、1700+ 行代码，单文件过大
2. 代码中 91 处使用宽泛的 `except Exception:`，吞掉所有异常导致问题难以追踪
3. `character/coc/` 和 `character/dnd5e/` 存在大量重复代码

现在项目规模已超过 100 个文件、993 个方法，亟需优化以支撑后续开发。

## What Changes

### 重构优化
- 拆分 `module/common/log_command.py` 为多个独立命令类
- 将宽泛异常处理改为具体异常类型
- 提取角色系统公共基类，减少 CoC/DnD5e 重复代码

### 代码质量提升
- 为核心函数添加类型注解
- 提取 Magic Numbers 为常量
- 完善测试覆盖

## Capabilities

### New Capabilities
- `code-refactoring`: 代码重构规范与实施指南

### Modified Capabilities
- (无)

## Impact

### 受影响模块
- `module/common/log_command.py` - 拆分
- `core/bot/dicebot.py` - 异常处理优化
- `module/character/coc/` 和 `module/character/dnd5e/` - 提取公共基类

### 风险评估
- 低风险：仅重构不影响功能
- 需要充分测试验证
