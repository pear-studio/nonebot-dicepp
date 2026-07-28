---
name: cross-workspace-handoff
description: 通过用户配置的 SFTP 或本地共享目录在不同设备、工作区或 agent 上下文之间交接任务和显式附件。用于跨平台开发协作、生产到开发交接、同机不同仓库副本、共享身份的 Git 链接工作树、Codex/Claude/Kimi 之间需要持久传递信息时；也可配合其他 handoff skill 完成实际投递。
---

# Cross-Workspace Handoff

把共享目录当作同一位用户拥有的多个 workspace 之间的邮箱。使用标准 SFTP/文件
操作和本 Skill 的 bundle 脚本，不建立消息服务或状态机。

初始化 workspace 时读取 [client-setup.md](references/client-setup.md)；建立共享
服务或管理 client 时读取 [server-setup.md](references/server-setup.md)。

## 身份与配置

Workspace 是可单独寻址的身份。独立 Git clone 可有独立身份；linked worktree
沿用 main worktree 的 ID、密钥和 inbox，不是独立目标。

连接配置位于被 Git 忽略的 `docs/agent/.agent-env.json`，使用
`crossWorkspaceHandoff.workspaceId` 和 `share`（`sftp` 或 `local`）。Linked
worktree 使用 main worktree 的 handoff 配置。缺少配置时询问用户，不猜主机、
账号、密钥或路径。

## 协议

```text
<root>/
├── inbox/<workspaceId>/
├── public/
└── .staging/
```

所有 workspace 属于同一位用户，这是合作式约定而非 inbox 隔离。默认只查看
自己的 inbox；仅在用户明确要求时读写 `public/` 或查看其他 inbox。内容默认不
删除。

Workspace/message ID 必须匹配
`^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$`，使用配置、路径或正文中的 ID 前先校验。

## Bundle

```text
python scripts/handoff_bundle.py id
python scripts/handoff_bundle.py pack <message-directory> <bundle.zip>
python scripts/handoff_bundle.py unpack <bundle.zip> <message-directory>
```

Bundle 根部只允许 `handoff.md` 和可选的 `attachments/`。`handoff.md` 至少包含
Message-ID、From、To、Created、Subject；回复可增加 Reply-To，公开消息的 To
为 `public`。附件必须显式选择，不得包含密钥、token、数据库、`.git` 或无关
工作区内容。

脚本只生成 ID、打包和路径安全解包，不读取连接配置或执行传输。

## 交换

- 每次明确指定目标；公开投递必须由用户明确要求。
- 先写 `.staging/<messageId>.zip.part`，完成后在同一共享根内 rename 到
  `inbox/<target>/<messageId>.zip` 或 `public/<messageId>.zip`。
- 结果不确定时检查最终路径；存在即复用同一 bundle，不存在才重送相同
  Message-ID。
- 接收时核对 ZIP basename、Message-ID、From、To 和所在 inbox/public，再读取
  显式附件。已有本地 ZIP 或解包目录只表示已缓存，不表示任务已处理。
- 确认、回复和交付结果都使用一条新的 handoff，并在需要时写 Reply-To；发送与
  接收使用同一协议，不另设确认状态。
