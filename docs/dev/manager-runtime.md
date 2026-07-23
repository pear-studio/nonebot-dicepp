# Manager 与 RuntimeUnit

本页说明第二批完成后的运行管理边界。归档与升级事务分别在后续批次建立，但都必须复用这里的 Manager、维护锁和持久化 operation 基础。

第三批归档事务的当前实现契约见 [Manager 归档与恢复](archive-restore.md)。

## 进程职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| Manager | RuntimeUnit 生命周期、维护锁、operation 持久化、内部 API | 用户界面、机器人业务逻辑 |
| Dashboard | 用户鉴权、展示、向 Manager 发起操作和重连查询 | Docker/子进程控制、归档文件写入 |
| Bot | NoneBot 与多个 QQ 账号的业务运行时 | 自身部署生命周期 |

没有 Manager 的部署属于不受支持的旧拓扑。Dashboard 会显示 Manager 不可用，不会退回到直接操作 Docker 或子进程。

## RuntimeUnit 语义

`RuntimeUnit` 是可以独立启停、查看状态和读取日志的进程或容器，不等于逻辑 `bot_id`。

- Linux 默认一个 Bot 容器对应 `dicepp-runtime`。
- Windows 默认一个 `DicePP-Runtime.exe` 子进程对应 `dicepp-runtime`。
- 同一 RuntimeUnit 可以承载多个 QQ Bot 账号。
- 启动、停止、重启及后续维护事务都作用于整个 RuntimeUnit。

Dashboard 可以按账号展示连接和配置状态，但不能把共享进程描述成“支持逐账号启停”。以后改为一账号一进程时，只需让 Runtime Adapter 暴露多个 RuntimeUnit。

## 内部 API 与鉴权

Manager 默认监听 `127.0.0.1:4091`；Linux Compose 显式改为 `0.0.0.0:4091`，但只通过 Compose 内部网络 `expose`，不映射宿主机端口。

Manager 首次启动时在 `<instance>/manager/state/api-token` 创建随机 token。Dashboard 以只读方式读取同一文件，并通过 Bearer token 调用 Manager。token 不应写进 Compose 环境变量、配置文件或日志。

当前兼容元数据：

| 项目 | 当前值 | 用途 |
|---|---:|---|
| Manager API | 2 | Dashboard 与 Manager 请求/响应兼容性 |
| Deployment schema | 2 | Compose、运行标签和未来 Release 契约兼容性 |
| Operation schema | 2 | 持久化 operation 记录读取兼容性 |

Dashboard 连接后必须先读取 Manager 健康信息并检查 API 兼容性。部署 schema 不匹配时应报告不受支持，而不是尝试猜测旧拓扑。

## operation 与维护锁

Manager 将 operation 写入 `<instance>/manager/state/manager.db`。Dashboard 提交操作后以 operation id 查询状态；浏览器刷新、Dashboard 重启或短暂断线不会丢失已提交的操作结果。

实例维护锁是实例级排他锁。一个需要停写的 operation 占有锁时，其他冲突操作必须被拒绝或等待，不允许并发操作同一批持久化资产。Manager 重启后会读取已有 operation 记录，并把启动前仍处于运行中的记录标记为已中断；第三批再为事务型归档和恢复增加可补偿 journal。

## Runtime Adapter

Linux Docker Adapter 只暴露状态、启动、停止、重启和日志等固定操作。目标容器必须同时匹配：

```text
io.dicepp.managed=true
io.dicepp.runtime-unit=dicepp-runtime
io.dicepp.deployment-schema=2
```

容器名、Compose service 名或用户传入的任意字符串都不能单独成为控制授权。只有 Manager 挂载 `/var/run/docker.sock`；Dashboard 不挂载该 socket。

Windows Process Adapter 只管理由托盘 Manager 启动的 Bot 子进程；Dashboard 生命周期也由托盘 Manager 持有，但不属于 Bot RuntimeUnit。退出托盘 Manager 时会有序关闭两者，不扫描或终止其他同名进程。

## 实例目录

```text
<instance>/
├─ config/
├─ data/
├─ content/
├─ dashboard/data/
└─ manager/
   ├─ state/       # token、operation store、维护状态
   ├─ packages/    # 后续版本下载缓存
   └─ backups/     # 后续事务安全归档
```

`manager/` 属于 Manager，不进入用户数据归档。`packages/` 和 `backups/` 在第四、第三批分别投入使用；第二批先固定目录所有权和挂载边界。

## 平台部署

Linux 标准 Compose 包含 `bot`、`dashboard`、`manager` 三个服务。Dashboard 通过 `http://manager:4091` 访问内部 API，Manager 通过 Docker Socket 控制带 DicePP 标签的 Bot RuntimeUnit。

Windows 的 `DicePP.exe` 就是托盘 Manager。它负责启动和监控 Bot、Dashboard，并可为当前用户设置登录自启动；实例数据始终保留在 DicePP 根目录的版本化程序目录之外。
