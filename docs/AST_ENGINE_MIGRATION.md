# AST Roll Engine 迁移指南

本文档为维护者提供 AST 掷骰引擎的迁移说明。

## 概述

DicePP 3.0 引入了基于 AST (Abstract Syntax Tree) 的新掷骰表达式引擎，
替代原有的正则表达式解析方案。新引擎提供：

- 明确的运算符优先级和结合性
- 结构化的 trace 输出
- 统一的错误处理
- 安全限制保护

## 运算符优先级

新引擎遵循以下优先级（从高到低）：

| 优先级 | 运算符 | 示例 |
|--------|--------|------|
| 1 (最高) | 后缀修饰器 | `K`, `R`, `X`, `CS` |
| 2 | 一元运算符 | `+x`, `-x` |
| 3 | 乘除 | `*`, `/` |
| 4 (最低) | 加减 | `+`, `-` |

所有二元运算符均为**左结合**：`1-1-1` = `(1-1)-1` = `-1`

## 回退开关

### 代码级回退

在代码中切换引擎：

```python
from module.roll.ast_engine import (
    enable_ast_engine,
    disable_ast_engine,
    is_ast_engine_enabled,
)

# 禁用 AST 引擎，回退到 legacy
disable_ast_engine()

# 重新启用 AST 引擎
enable_ast_engine()

# 检查当前状态
if is_ast_engine_enabled():
    print("使用 AST 引擎")
```

### 显式指定引擎

执行时显式选择引擎：

```python
from module.roll.ast_engine import exec_roll_exp_unified, EngineType

# 使用 AST 引擎
result = exec_roll_exp_unified("2D20K1", engine=EngineType.AST)

# 使用 legacy 引擎
result = exec_roll_exp_unified("2D20K1", engine=EngineType.LEGACY)
```

## 测试要求

### 运行兼容性测试

确保新引擎与 legacy 引擎产生相同结果：

```bash
# 运行兼容语料库测试
uv run pytest tests/module/roll/test_compatibility_corpus.py -v

# 运行 AST 引擎单元测试
uv run pytest tests/module/roll/test_ast_*.py -v

# 运行完整测试套件
uv run pytest
```

### 添加新测试用例

在兼容语料库中添加新表达式时，需要：

1. 在 `test_compatibility_corpus.py` 中添加 `CorpusEntry`
2. 使用 `scripts/capture_baseline.py` 捕获基线值
3. 更新 `expected_value` 字段

```python
# 示例：添加新测试用例
CorpusEntry(
    "4D6K3",  # 表达式
    expected_value=12,  # 预期值
    description="4D6 drop lowest",
    high_risk=True,  # 标记为高风险（如果涉及过程文本）
)
```

## 错误代码

| 代码 | 类型 | 描述 |
|------|------|------|
| 100 | SYNTAX_ERROR | 语法错误 |
| 101 | UNEXPECTED_TOKEN | 意外字符 |
| 102 | UNMATCHED_PAREN | 括号不匹配 |
| 200 | RUNTIME_ERROR | 运行时错误 |
| 300 | LIMIT_EXCEEDED | 安全限制超出 |
| 301 | EXPRESSION_TOO_LONG | 表达式过长 |
| 303 | DICE_COUNT_EXCEEDED | 骰子数量过多 |

## 安全限制

默认限制配置：

| 限制项 | 默认值 |
|--------|--------|
| 表达式长度 | 1000 字符 |
| 骰子数量 | 1000 |
| 骰子面数 | 1,000,000 |
| 爆炸迭代 | 100 次 |
| 总掷骰次数 | 10,000 |

自定义限制：

```python
from module.roll.ast_engine.limits import SafetyLimits
from module.roll.ast_engine import exec_roll_exp_ast

limits = SafetyLimits(
    max_dice_count=500,
    max_explosion_iterations=50,
)

result = exec_roll_exp_ast("100D20", limits=limits)
```

## 后续规划

- **稳定期**：当前版本保留 legacy 引擎作为回退选项
- **删除计划**：稳定期后（约 2-3 个版本），legacy 引擎将被移除
- **不在本次范围**：legacy 模块删除不属于本次变更范围

## 故障排查

### 兼容性问题

如果发现 AST 引擎与 legacy 引擎结果不一致：

1. 使用 `compare_engines()` 函数对比两个引擎
2. 检查表达式是否在兼容语料库中
3. 如果是新边界情况，添加到语料库并调整

### 性能问题

AST 引擎首次解析会有 Lark 语法初始化开销。
后续解析使用缓存的 parser 实例。

## 联系方式

如有问题，请提交 Issue 或联系维护者。
