# DicePP Development Rules

当前目录是 DicePP 开发环境。可以在用户任务范围内修改代码、文档、测试和 agent 配置。

## Validation

- 完成前运行与风险相称的验证，并报告结果。
- 开发验证优先使用 `auto-test-run`；涉及机器人指令交互时配合 `dicepp-shell`。
- 涉及外部 API、LLM 或付费服务调用时，先确认配置、成本和调用次数边界。
- 测试层级、fixture 边界和 `quick` 代表集以 `docs/dev/testing.md` 为准；不要自行新增测试选择 marker。
- 每次 push 前必须在当前 HEAD 上成功运行完整离线回归 `uv run pytest`。仅当本次会话已经在同一 HEAD 上成功运行，且之后没有代码、配置或测试改动时可以复用该结果；否则先补跑。此约束由 Agent 执行，不安装 Git hook。

## Docker 镜像验收

- 镜像冷构建成本高（低配机型实测单镜像 45-60 分钟），且换 build-arg 会使依赖层缓存全部失效。
- 验收前确认镜像构建自目标 commit，避免用旧镜像得出无效结论。
- 验收需要重建镜像且必须暂停生产环境时，先向用户说明预计耗时与网络/性能风险并获得确认；不得未评估就承诺"几分钟完成"。

## Backlog

开发延后项使用 `docs/dev/backlog.md`，优先通过 backlog skills 管理。

## Production Handoff

收到生产环境问题交接时，使用 `dev-handoff-accept` 判断是记录 backlog、诊断还是直接实现。

如本地 `docs/agent/.agent-env.json` 配置了生产环境 peer 路径，可只读查看生产日志和证据；不要从开发环境写入生产目录。
