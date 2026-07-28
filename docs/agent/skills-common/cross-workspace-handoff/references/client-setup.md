# Client Setup

Client 是发送或接收 handoff 的 workspace，与操作系统无关。

## 身份与密钥

Workspace ID 必须匹配
`^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$`，且不得为保留目标 `public`。
独立 clone 使用独立 ID 和密钥；linked worktree 沿用 main worktree 的配置、
身份、密钥、inbox 和本地缓存。

每个独立 workspace 使用专用密钥。私钥默认位于项目之外：

```text
<user-ssh-dir>/cross-workspace-handoff/<workspaceId>/id_ed25519
```

私钥留在项目和 Skill 外且永不交付，只向 server 注册公钥。

可使用 `ssh-keygen` 生成带 workspace 注释的 Ed25519 密钥。自动化时用进程 API
传递参数，避免 shell 丢失空口令参数；随后用 `ssh-keygen -y` 对照公钥，确认
私钥可读且密钥对匹配。

## 配置

`docs/agent/.agent-env.json` 是项目已有、被 Git 忽略的本地 agent 配置；helper
依赖这个相对位置确定工作区和缓存根。保留现有字段并合并：

```json
{
  "crossWorkspaceHandoff": {
    "workspaceId": "<workspace-id>",
    "share": {
      "kind": "sftp",
      "host": "<sftp-host>",
      "port": 22,
      "user": "<sftp-user>",
      "identityFile": "<private-key-path>",
      "root": "<client-visible-share-root>",
      "hostKeyFingerprint": "SHA256:<trusted-host-key-fingerprint>"
    }
  }
}
```

缺少配置时询问用户，不猜 host、账号、密钥或路径。传输 helper 在 linked
worktree 中优先读取 main worktree 配置，并把缓存放在该配置所属工作区的
`.temp/cross-workspace-handoff/`。

Windows 路径可写成
`C:/Users/<user>/.ssh/cross-workspace-handoff/<workspace-id>/id_ed25519`。

运行在 server 本机且可直接访问共享目录时，`share` 可改为
`{"kind":"local","root":"<absolute-local-share-root>"}`。

通过可信渠道核对 host key 并写入 `known_hosts`。helper 始终严格校验
`known_hosts`；可选的 `hostKeyFingerprint` 会进一步只接受其中与指纹匹配的
可信 key。

最后验证专用密钥能够完成 SFTP 列目录、上传、下载和同根 rename；若 server
声明禁止 shell，也验证该限制。
