---
name: cross-workspace-handoff
description: 通过用户配置的 SFTP 或本地共享目录在不同设备、工作区或 agent 上下文之间交接任务和显式附件。用于跨平台开发协作、生产到开发交接、同机不同仓库副本、共享身份的 Git 链接工作树、Codex/Claude/Kimi 之间需要持久传递信息时；也可配合其他 handoff skill 完成实际投递。
---

# Cross-Workspace Handoff

初始化或连接异常时读取 [client-setup.md](references/client-setup.md)；搭建或排查
共享服务时读取 [server-setup.md](references/server-setup.md)。

## 发送

```text
python <skill-dir>/scripts/handoff_bundle.py id
```

使用输出的 ID，在工作区
`.temp/cross-workspace-handoff/outbox/<message-id>/handoff.md` 写：

```text
Message-ID: <message-id>
From: <workspace-id>
To: <target>
Created: <带时区的 ISO-8601 timestamp>
Subject: <subject>

<body>
```

回复可增加 `Reply-To: <message-id>`；附件放入同目录的 `attachments/`。随后运行：

```text
python <skill-dir>/scripts/handoff_bundle.py pack <message-dir> .temp/cross-workspace-handoff/outbox/<message-id>.zip
python <skill-dir>/scripts/handoff_transfer.py send <target> .temp/cross-workspace-handoff/outbox/<message-id>.zip
```

## 接收

```text
python <skill-dir>/scripts/handoff_transfer.py receive <message-id> [--public]
```

读取命令打印路径中的 `handoff.md` 和显式附件。

## 边界

- 每次明确目标；仅在用户明确要求时使用 `public`。
- 不要对不同内容复用同一 Message-ID；helper 只保证相同 bundle 的幂等重试。
- 附件须显式选择，不得包含密钥、token、数据库、`.git` 或无关工作区内容。
- 回复或交付结果使用新 handoff，必要时带 `Reply-To`，不另设 ACK。
- 不自动删除；本地缓存存在不代表任务已处理。
- 向用户汇报 `[目标或收件箱] <message-id>`；接收时同时给出来源和本地路径。
