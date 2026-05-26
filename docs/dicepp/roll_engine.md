# 掷骰引擎（AST）

本文档描述当前掷骰引擎结构与安全限制。Legacy 正则引擎已删除，生产路径统一使用 AST 引擎。

## 模块边界

AST 引擎代码位于：

- `module/roll/ast_engine/adapter.py`
- `module/roll/ast_engine/parser.py`
- `module/roll/ast_engine/evaluator.py`
- `module/roll/ast_engine/limits.py`
- `module/roll/ast_engine/errors.py`

## 公共入口

上层业务应优先使用：

- `exec_roll_exp_unified()`：执行表达式并返回完整 `RollResult`
- `preprocess_roll_exp()`：表达式预处理
- `is_roll_exp()`：表达式合法性判断
- `sift_roll_exp_and_reason()`：从命令片段中分离表达式与原因

底层 AST 调试或测试可使用：

- `exec_roll_exp_ast()`：返回 AST 原生 `RollExpressionResult`
- `build_sampling_plan()` / `sample_from_plan()`：`.rexp` 期望计算的重复采样路径

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
`exec_roll_exp_unified()` 会将 AST 引擎错误包装为 `RollDiceError`，以保持上层命令处理的用户可见错误契约。

## 相关文档

- 命令目录：`command_catalog.md`
- 开发配方：`dev_recipes.md`
