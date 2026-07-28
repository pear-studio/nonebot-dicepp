# Server Setup

Server 提供持久共享存储和 SFTP（或本机文件）访问，与操作系统无关。

```text
<root>/
├── inbox/<workspace-id>/
├── public/
└── .staging/
```

Workspace ID 必须匹配
`^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$`，且不得为 `public`。使用 server 原生
权限或 ACL 保护共享根及认证配置。需要本机 client 直连目录时，只向共享子树
授予写入和 rename 权限，不放宽隔离根或认证配置。SFTP 必须支持列目录、上传、
下载和同根 rename。

所有 workspace 属于同一位用户；inbox 是寻址约定，不是隔离边界。helper 只直接
接收自己的 inbox 或 `public`，仅在用户明确要求时才用共享目录操作查看其他 inbox。
连接中断可能在 `.staging/` 留下唯一 `.part`；可清理确认已过期且不在传输中的
临时文件，不要因此删除 `inbox/` 或 `public/` 内容。

## Client 管理

注册时校验 workspace ID 和公钥，按公钥指纹去重，并记录 workspace 与指纹的
对应关系。通过可信渠道向 client 提供 SFTP host、port、user、client 可见的
共享根和 SSH host key fingerprint。

SFTP-only 不等于路径隔离。若要求密钥只能访问共享根，使用受限服务账号、
chroot 或等价机制，并禁用该账号的密码、shell、PTY 和 forwarding；否则明确
其实际文件权限边界。

使用 OpenSSH chroot 时，隔离根及父目录的属主/权限约束与可写共享子树不同；
不要让 client 写隔离根。`authorized_keys` 还必须满足 sshd 对属主、可读性和
权限的检查。具体账号、路径和命令按 server 平台与现有 SSH 策略决定。
