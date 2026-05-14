# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## persona

### [B-260511-e2f3c7] 主动分享 prompt 中 recent_history 引发跨时段"补答"
- 创建: 2026-05-11
- 问题表现:
  - 详细分析见 `.temp/1802_conversation_analysis.md`
  - 18:02 用户问"前两个观察是什么"，对话因工具调用超限未正常结束
  - 20:33 主动分享消息内容变成了回答 18:02 的问题："前两个……第一个是，体温比周围低。第二个是……长时间不活动会变僵硬..."
  - 根因: `proactive_scheduler.py:432-456` 构建 `ShareMessageContext` 时传入了 `recent_history`（最近 5 条对话），`generate_share_message()` 的 prompt 包含此板块，LLM 看到未完成的问答就继续回答
- 工作计划:
  - 方案A: 在 `generate_share_message` 的 system prompt 中明确指示 recent_history 仅供参考，禁止回答历史问题
  - 方案B: 将 `recent_history` 改为"关系背景"摘要而非原始对话，从源头消除补答动机
  - 影响面: `event_agent.py` `generate_share_message` prompt、`proactive_scheduler.py` `_format_recent_history`

### [B-260512-f2d8a1] tool_choice="required" 场景缺少显式终止机制
- 创建: 2026-05-12
- 问题表现:
  - `tool_choice="required"` 下 LLM 完成工具调用后无法表达"我已做完"——它必须继续调工具直到 `max_total_rounds` 耗尽
  - 单工具 CollectExecutor 场景首轮收集成功后 100% 浪费
  - 当前通过 `max_tool_rounds=1` + 早退 break 缓解，多工具场景依赖硬上限
- 工作计划:
  - 引入 `finish_task` 工具：LLM 完成后主动调用声明结束，循环检测到后立即终止
  - 作为通用终止方案，统一单工具和多工具场景
  - 影响面: `client.py:_generate_with_tools` 循环终止条件、后台 Agent prompt
  - 前置条件: 当前 `max_tool_rounds=1` 已覆盖，本条为架构扩展预留

## test-infra

### [B-260514-60a29a] 测试 bot config 文件泄漏，config/bots/ 积累 8000+ 残留文件
- 创建: 2026-05-14
- 问题表现:
    - config/bots/ 下有 8012 个文件，均为测试生成的 bot 账号配置
    - 按前缀分布：char_test(2278)、e2e(2090)、init_test(1751)、rollcmd_test(1236)、test_bot(645)、shell(11)
    - conftest.py 设置了 DICEPP_APP_DIR 指向 tmpdir，但未设置 DICEPP_PROJECT_ROOT，Paths.CONFIG_DIR 仍指向真实项目目录
    - _new_test_account() 每次生成唯一 bot ID（含 uuid），_ensure_account_config() 发现文件不存在即从 _template.json 自动拷贝
    - async_teardown_test_bot 和两个 pytest fixture 只清理 data_path（data 目录），不清理 config/bots/{id}.json
    - 每次 pytest 都泄漏文件，无任何回收机制
- 工作计划:
    - conftest.py 增设 DICEPP_PROJECT_ROOT 指向 tmpdir，在 tmpdir 下创建最小 config/bots/_template.json，atexit 全量清理
    - async_teardown_test_bot / shared_bot / fresh_bot 增加防御性删除 config/bots/{bot_id}.json
    - 清理现有 8012 个残留文件

