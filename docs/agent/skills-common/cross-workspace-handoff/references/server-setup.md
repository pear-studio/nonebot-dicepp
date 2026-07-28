# Server Setup

Server 提供持久共享存储和 SFTP（或本机文件）访问，与操作系统无关。

按 [SKILL.md](../SKILL.md) 的协议创建共享根和每个 workspace 的 inbox，并使用
server 原生权限或 ACL 保护共享根及认证配置。SFTP 必须支持列目录、上传、下载
和同根 rename。

## Client 管理

注册时校验 workspace ID 和公钥，按公钥指纹去重，并记录 workspace 与指纹的
对应关系。通过可信渠道向 client 提供 SFTP host、port、user、client 可见的
共享根和 SSH host key fingerprint。

SFTP-only 不等于路径隔离。OpenSSH 的 `internal-sftp -d <path>` 只设置初始
目录；若要求密钥只能访问共享根，使用受限服务账号、chroot 或等价机制。没有
隔离时应明确其实际文件权限边界。

撤销时根据登记的 workspace 和公钥指纹删除准确的认证记录。共享目录和历史
消息默认保留。
