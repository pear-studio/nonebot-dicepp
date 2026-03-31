# DicePP 开发文档

本目录聚焦 DicePP 内核实现，与 `src/plugins/DicePP` 代码保持对齐。

> 返回根文档导航：[`../README.md`](../README.md)

## 文档地图

| 文档 | 说明 |
|------|------|
| [system_overview.md](./system_overview.md) | 系统边界、运行形态与主执行链路 |
| [command_runtime.md](./command_runtime.md) | 命令注册、分发协议、优先级与权限机制 |
| [command_catalog.md](./command_catalog.md) | 按模块整理的命令与触发词索引 |
| [data_layer.md](./data_layer.md) | 数据库与仓储访问模式、迁移约束 |
| [roll_engine.md](./roll_engine.md) | AST/Legacy 掷骰引擎边界与限制 |
| [standalone_runtime.md](./standalone_runtime.md) | Standalone 生命周期与接口运行语义 |
| [dev_recipes.md](./dev_recipes.md) | 常见开发任务的最小闭环操作 |

## 阅读建议

1. 新加入开发：`system_overview` -> `command_runtime` -> `command_catalog`
2. 修改数据相关功能：`data_layer` -> `dev_recipes`
3. 修改掷骰相关功能：`roll_engine` -> `command_catalog` -> `dev_recipes`
4. 调试独立运行问题：`standalone_runtime` + `../standalone.md`

## 维护规则

- 避免在文档中写死“固定命令总数”。
- 触发词或行为变更后，优先更新 `command_catalog.md`。
- 涉及架构行为变化时，先更新对应机制文档再合并代码。