---
name: dev-handoff-accept
description: 在开发环境中接收生产问题 handoff 时使用。用户提供 handoff 内容或路径后, 分析证据并决定直接诊断/修复、新增 backlog、要求补充生产信息或拆分任务；可在配置了 peer.prodRoot 时只读查看生产日志和证据, 但不得写入生产目录。
---

# Dev Handoff Accept

使用本技能在开发环境接收生产问题交接。

## 输入

用户应提供 handoff 内容或具体文件路径。若用户只说要处理生产交接但未提供内容或路径，提醒用户查看当前开发目录的 `.temp/prod-handoff/`，或提供具体 handoff 文件。

## 处理原则

- 先提取现象、影响范围、证据、已确认事实和未确认假设。
- 判断下一步是直接诊断/修复、新增 backlog、要求补充生产信息，还是拆分多个任务。
- 如本地 `docs/agent/.agent-env.json` 配置了 `peer.prodRoot`，可以只读查看生产日志和证据。
- 不要从开发环境写入生产目录，不要修改生产配置、数据库、数据文件或服务状态。
- 若需要记录延后项，使用 backlog skills 管理 `docs/dev/backlog.md`。
- 若证据不足以安全判断，先列出缺口并请求补充。
