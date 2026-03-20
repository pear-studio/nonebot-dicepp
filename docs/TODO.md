# DicePP TODO（工程基建）

> 记录项目在“纯 Python 实现框架层面”可持续提升的基建改进点。
> 该文件由对话助手在本轮讨论中生成/更新。

## P0（框架层面优先）

1. 命令路由/匹配加速
   - 现状：`core/bot/dicebot.py` 的 `process_message()` 对每条消息按行 split 后，遍历所有 command 调用 `can_process_msg()`。
   - 待办：在 `register_command()` 阶段建立触发索引/分桶（例如按 `.r/.help/.hp` 等确定性特征先筛），减少每条消息的匹配成本；同时缓存 `can_process_msg` 的协程判定结果。
   - 验收：command 数量增长时，单条消息平均处理耗时不随命令数线性膨胀。

2. 聊天/自定义匹配正则缓存与索引
   - 现状：`core/localization/manager.py` 的 `process_chat()` 对每个自定义 key 运行 `re.match(key, msg)`，且 key 多为正则。
   - 待办：在加载 `localization.xlsx` / `chat.xlsx` 时对 key 进行预编译或缓存；增加短路过滤（按前缀/特征字符分桶）以减少不必要的正则匹配。
   - 验收：聊天关键字/宏触发的延迟降低；避免重复编译正则造成的波动。

3. 命令执行异常契约化（减少 broad except）
   - 现状：`dicebot.py` 在多个生命周期位置捕获 `AttributeError/TypeError/ValueError/RuntimeError`，统一走 `handle_exception()`，返回“未处理的错误”并发给 master。
   - 待办：让 `CommandError` 在框架层被显式捕获，并按 `to_user/to_master` 生成可理解反馈；其余异常仍走内部错误路径。
   - 验收：可预测的错误返回；master 不再被大量“应当是业务错误”的异常轰炸。

## P1（稳定性/可维护性）

4. 异步 tick/todo 任务生命周期治理
   - 现状：`tick_loop()` + `todo_tasks` 自实现调度（初始化任务/等待任务/超时回调/cancel）。
   - 待办：
     - 统一在 `shutdown_async()` 中对 `todo_tasks` 做 cancel/清理，避免任务泄露；
     - 避免边遍历边修改 dict 的时序隐患（使用 keys 快照）；
     - 明确任务状态（未初始化/运行中/完成/超时）。
   - 验收：长时间运行未出现任务堆积或偶发卡死。

5. Value Object 与可测试数据结构（端口/命令）
   - 现状：`dicebot.py` 会使用 `MessagePort` 作为 dict key 做合并；要求 hash/equality 语义可靠。
   - 待办：为 BotCommand/Port 类引入 `dataclass`（必要时 `frozen=True`），明确 `MessagePort` 的比较/哈希语义；把“合并规则”拆成可单元测试的纯函数。
   - 验收：合并端口逻辑可被单元测试覆盖；避免隐藏的等价/哈希问题。

## P2（后续增强）

6. 框架层更一致的 error code 结构
   - 现状：代码里存在 `CODE101/CODE110/...` 等错误代码，但结构和字段不完全统一。
   - 待办：形成统一结构（`{code, command, user_id, group_id, message_id}`），便于日志聚合和测试断言。

7. 路由/匹配契约（为后续扩展预留）
   - 现状：目前命令主要依赖 `can_process_msg()`，触发信息在子类内部隐含。
   - 待办：为 `UserCommandBase` 定义可选的触发元数据（触发前缀/正则特征/flag/cluster 等），让框架构建索引并减少反射判断。

## P0（工程化闭环优先）

1. CI：在 PR/push 自动跑测试与覆盖率
   - 现状：当前只有 `sync2gitee.yml`，未见默认的 `pytest`/coverage 门禁。
   - 待办：新增 GitHub Actions 工作流，默认跑 `uv run pytest`，可选增加 `uv run pytest --cov=...`。
   - 验收：每次 PR 都能看到测试结果与覆盖率变化。

2. 质量门禁：lint/format/type-check 形成最小集合
   - 现状：仓库根未看到 `ruff/black/isort/mypy/pyright/pre-commit` 等配置（至少未在常规路径中找到）。
   - 待办：引入并固化最小工具链：`ruff` + `ruff format`（或 `black`）+ import 整理（`ruff` 或 `isort`）；类型检查可选 `pyright`/`mypy`。
   - 验收：开发无需“猜格式/猜规则”，CI 明确给出失败原因。

3. FastAPI：对外接口契约化 + 鉴权 + 基础端点
   - 现状：`src/plugins/DicePP/module/fastapi/api.py` 仅有 `GET /` 测试路由。
   - 待办：
     - 补充 `/healthz`、错误响应标准格式
     - 接入鉴权（例如 `Authorization: Bearer`，复用现有 token 语义或新增配置项）
     - 引入版本化（如 `/v1/...`）
   - 验收：外部系统集成稳定，错误可机器解析。

4. 命令文档可验证（避免文档与代码漂移）
   - 现状：`command_reference.md` 很完整，但目前缺少“从代码自动核对文档”的机制。
   - 待办：新增测试/脚本从 `USER_COMMAND_CLS_DICT` 及各命令的 `get_help/get_description` 生成索引，与文档条目做对齐检查（至少保证覆盖率）。
   - 验收：文档漏写/错写能被测试提前发现。

5. 数据 schema 演进框架（迁移不是脚本堆）
   - 现状：当前依赖 `BotDatabase._ensure_all_tables()` 建表；存在 `scripts/migrate/`，但看不到 `schema_version` + 可复用迁移执行器。
   - 待办：引入 `schema_version` 表 + 迁移执行器（幂等、可回滚或至少可重复执行），逐步接入现有迁移逻辑。
   - 验收：未来字段变更不会导致数据不可用或需要手工介入。

## P1（运行/交付一致性）

6. 配置/文档一致性梳理（README vs 实际配置入口）
   - 现状：README 提到 `.env.example/.env`，但仓库里未看到实际文件；生产更依赖 `Data/.../Config` 的 `xlsx` 配置与运行时目录策略。
   - 待办：明确“必须的环境变量 vs xlsx 配置”，并让 `README/DEPLOY/scripts` 与实际启动路径完全一致。
   - 验收：新环境部署无需读代码也能跑通。

7. 可观测性：结构化日志与 request context
   - 现状：有 `dice_log` 和异常堆栈处理，但关键路径缺少统一字段（bot_id/group_id/user_id/message_id/command_name/error_code）。
   - 待办：在消息分发、命令执行、外部 API 入口统一注入上下文字段，形成可追踪链路。
   - 验收：出现问题能在日志中快速定位根因与影响范围。

8. 测试闭环：把集成测试纳入（可手动/夜间）流程
   - 现状：存在 `scripts/test/test_bot.py` 集成测试，但未见 CI 默认触发。
   - 待办：CI 默认跑单测+覆盖率；集成测试作为手动或 nightly job（按资源/时长选择）。
   - 验收：发布前有稳定的回归信号。

