# Client Setup

Client 是发送或接收 handoff 的 workspace，与操作系统无关。

## 身份与密钥

每个独立 workspace 使用唯一 ID 和专用密钥。私钥默认位于项目之外：

```text
<user-ssh-dir>/cross-workspace-handoff/<workspaceId>/id_ed25519
```

不要把私钥放进 Skill；Skill 可能被复制到多个 agent 目录。若使用 workspace-local
secret 目录，须位于 Skill 外并确认 Git 忽略规则有效。私钥不得交付，只向 server
注册公钥。

可使用 `ssh-keygen` 生成带 workspace 注释的 Ed25519 密钥；具体命令按当前平台
调整。

## 配置

`docs/agent/.agent-env.json` 是项目已有、被 Git 忽略的本地 agent 配置。保留
现有字段并合并：

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
      "root": "<client-visible-share-root>"
    }
  }
}
```

运行在 server 本机且可直接访问共享目录时，`share` 可改为
`{"kind":"local","root":"<absolute-local-share-root>"}`。

通过可信渠道核对 SSH host key fingerprint，并验证专用密钥能够完成 SFTP
列目录、上传、下载和同根 rename；若 server 声明禁止 shell，也验证该限制。
