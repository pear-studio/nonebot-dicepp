---
name: prod-handoff-create
description: 在生产环境中, 当用户已明确要求生成、创建、归档或交接生产问题 handoff, 且问题事实已和用户确认后使用。用于把生产问题整理为开发环境可接手的交接文档, 可在配置了 peer.devRoot 时写入开发环境 .temp/prod-handoff/, 但不得写入当前生产项目。
---

# Prod Handoff Create

使用本技能把已确认的生产问题交接给开发环境。

## Preconditions

- 只在生产环境中使用。
- 仅当用户明确要求生成、创建、归档或交接 handoff 时使用。
- 写入 handoff 前，必须先和用户确认问题事实、影响范围和证据；信息不足时先提出待确认问题，不写文件。

## Handoff 内容

交接应包含：

- 标题
- 生产环境与时间
- 现象与影响范围
- 已观察到的证据，如日志摘要、错误信息、版本或配置状态
- 已确认事实
- 未确认假设
- 风险与紧急程度
- 建议开发环境下一步

不要包含密钥、token、隐私数据或完整敏感配置。

## 写入规则

- 默认不写当前生产项目下的任何文件。
- 读取 `docs/agent/.agent-env.json`；仅当其中配置了 `peer.devRoot` 时，允许写入 `<devRoot>/.temp/prod-handoff/`。
- 写入前解析目标路径，确认最终路径位于 `<devRoot>/.temp/prod-handoff/` 内。
- 文件名使用时间戳和简短 slug，避免覆盖既有文件。
- 不写入 `devRoot` 的其他位置，不修改开发环境代码或 backlog。
- 未配置 `peer.devRoot` 时，只在对话中输出 handoff 内容，并提示用户提供开发环境路径或手动交接。
