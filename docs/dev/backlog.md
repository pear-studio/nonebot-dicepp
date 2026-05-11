# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **问题表现**：症状、错误日志、量化指标、复现路径
- **工作计划**：可能的修复方向、需先验证的假设、影响面、风险点

---

## ci

### [B-260508-93fe70] CI 没覆盖 integration 测试，回归保护层裸奔
- 创建: 2026-05-08
- 问题表现:
  - .github/workflows/ci.yml:41 仅执行 pytest -m unit，整个 tests/integration/** 不会被 CI 触发
  - 本次 timeout 收敛改动验证时发现 tests/integration/persona/test_command.py::TestAdminCommands::test_admin_events 在 origin/master 41d8973 之前就已 break，未被任何 CI/PR 拦截
  - 用户面命令链路（IsolatedAsyncioTestCase 走 .ai admin events 等）恰好是 integration 标签，CI 长期裸奔
- 工作计划:
  - 在 ci.yml 增加 integration 测试 step：可改为 pytest -m "unit or integration" 单 job，或拆分为独立 job 便于失败定位
  - 本机跑 tests/unit/persona + tests/integration/persona 共 494 用例约 58s，加进 CI 总时长可控
  - 如担心 integration 偶发依赖（外部进程/真实 IO），先 grep tests/integration 确认无 real_llm/external_service 类需要单独 marker 隔离

## persona

### [B-260507-f9ea98] 厂商适配层根据模型类型选择 prompt 注入角色（system/user/developer）
- 创建: 2026-05-07
- 问题表现:
  - `_on_segment_round_complete` 全局硬编码 `user` 角色注入纠正消息
  - 短期 fix 已将前缀改为 `[内部指令: ...]`，但角色仍是 user
  - 兼容约束: MiniMax 等厂商不支持 mid-conversation system，必须用 user 兜底
  - 风险: LLM 可能将 `[内部指令: ...]` 文本误识为用户输入，影响对话连贯性
- 工作计划:
  - 在 ContextBuilder 或厂商适配层加模型类型分支：支持 system/developer 角色的厂商使用对应角色，MiniMax 等保留 user 注入路径
  - 需先观察现有 LLM 是否真的频繁误识 `[内部指令]`，决定是否提前优先级
  - 影响面: ContextBuilder、厂商 adapter、segment dispatcher


