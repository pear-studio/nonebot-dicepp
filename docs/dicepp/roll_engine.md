# 掷骰引擎（AST/Legacy）

本文档描述当前掷骰引擎结构、安全限制与回退机制。

## 模块边界

AST 引擎代码位于：

- `module/roll/ast_engine/adapter.py`
- `module/roll/ast_engine/limits.py`
- `module/roll/ast_engine/errors.py`
- `module/roll/ast_engine/legacy_adapter.py`

## 默认引擎与切换

`adapter.py` 提供：

- `enable_ast_engine()`
- `disable_ast_engine()`
- `is_ast_engine_enabled()`

注意：

- `disable_ast_engine()` 仅切换默认引擎类型到 legacy。
- legacy 实际调用还受 `legacy_adapter.py` 中 `_LEGACY_ENABLED` 显式开关保护（默认 `False`）。
- 若未手动启用 `_LEGACY_ENABLED = True`，legacy 路径会抛错并拒绝执行。

## 安全限制（当前默认）

来自 `module/roll/ast_engine/limits.py`：

- 表达式长度：`max_expression_length = 1000`
- 解析深度：`max_parse_depth = 50`
- 骰子数量：`max_dice_count = 100`
- 骰子面数：`max_dice_sides = 1000`
- 爆炸迭代：`max_explosion_iterations = 100`
- 总掷骰次数：`max_total_rolls = 10000`

其中骰子数量/面数与 `roll_config.py` 的默认上限保持一致。

## 错误处理

错误类型由 `errors.py` 统一定义，按语法、运行时、限制超限分类。
建议文档与上层调用只依赖“错误类别 + code”，避免绑定具体文案。

## 运维建议

- 生产环境保持 AST 默认，不建议开启 legacy 显式开关。
- 临时排障如需 legacy：
  1. 手动将 `_LEGACY_ENABLED = True`
  2. 切换默认引擎到 legacy
  3. 排障结束后恢复为 `False`

## 相关文档

- 命令目录：`command_catalog.md`
- 开发配方：`dev_recipes.md`
