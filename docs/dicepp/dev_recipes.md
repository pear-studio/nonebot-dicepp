# 开发配方

本页给出高频开发任务的最小闭环路径。

## 新增一个命令

1. 在对应模块目录新增命令类（例如 `module/common/xxx_command.py`）
2. 继承 `UserCommandBase`
3. 使用 `@custom_user_command(...)` 注册
4. 在模块 `__init__.py` 导入该命令文件，确保装饰器执行
5. 实现：
   - `can_process_msg(...) -> (should_proc, should_pass, hint)`
   - `async process_msg(...) -> List[BotCommandBase]`
6. 补充 `get_help()` 与 `get_description()`

## 访问数据库

在命令中通过 `self.bot.db` 使用仓库 API：

```python
row = await self.bot.db.user_stat.get(user_id)
await self.bot.db.user_stat.upsert(model)
```

涉及 schema 改动时：

- 新增迁移脚本到 `core/data/migrations/`
- 保证可重复执行（幂等）

## 扩展本地化与配置

- 本地化：`bot.loc_helper.register_loc_text(...)`
- 配置项：`bot.cfg_helper.register_config(...)`

建议在命令 `__init__` 或 `delay_init` 中集中注册，避免分散。

## 调试命令分发

定位顺序：

1. 命令是否被导入并注册（`USER_COMMAND_CLS_DICT`）
2. `priority` 顺序是否被更高优先级命令拦截
3. `can_process_msg` 是否返回 `should_proc=True`
4. `group_only` / `permission_require` 是否命中限制
5. `should_pass` 是否导致链路继续或中断

## 测试建议

- 单元测试优先覆盖：解析、边界值、权限判断
- 集成测试覆盖：消息输入到输出命令链
- AST 相关变更优先补充兼容性测试与基线
