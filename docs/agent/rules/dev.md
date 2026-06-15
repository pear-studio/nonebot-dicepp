# DicePP Development Rules

当前目录是 DicePP 开发环境。可以在用户任务范围内修改代码、文档、测试和 agent 配置。

## Validation

- 完成前运行与风险相称的验证，并报告结果。
- 开发验证优先使用 `run-tests` 和 `dicepp-shell` 技能；命令细节以对应技能和项目配置为准。
- 涉及外部 API、LLM 或付费服务调用时，先确认配置、成本和调用次数边界。

## Backlog

开发延后项使用 `docs/dev/backlog.md`，优先通过 backlog skills 管理。

## Production Handoff

收到生产环境问题交接时，使用 `dev-handoff-accept` 判断是记录 backlog、诊断还是直接实现。

如本地 `docs/agent/.agent-env.json` 配置了生产环境 peer 路径，可只读查看生产日志和证据；不要从开发环境写入生产目录。
