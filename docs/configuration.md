# 配置入门

DicePP 使用 JSON 配置。新手通常只需要改三个地方：

- `config/bots/{QQ号}.json`
- `config/global.json`
- `config/user.json`

## 目录说明

| 路径 | 用途 | 是否提交 |
|------|------|----------|
| `config/global.json` | 全局默认配置 | 可以 |
| `config/user.json` | API Key、密钥等敏感信息 | 不提交 |
| `config/bots/_template.json` | 账号配置模板 | 可以 |
| `config/bots/{QQ号}.json` | 具体机器人账号配置 | 不提交 |
| `content/` | 用户自己的角色卡、牌组、查询库等内容 | 不提交 |
| `data/` | 运行时数据 | 不提交 |
| `dashboard/data/` | Dashboard 账号和会话数据 | 不提交 |
| `manager/state/` | Manager token、operation 和维护状态 | 不提交 |
| `manager/control/` | Bot↔Manager 专用控制凭据；Dashboard 不挂载 | 不提交 |
| `manager/packages/` | Manager 已下载并校验的更新包缓存 | 不提交 |
| `manager/backups/` | 用户归档与事务安全归档 | 不提交 |
| `templates/characters/default/` | 随版本发布的只读角色模板，供未来 Dashboard 新建角色使用 | 可以 |

`content/` 完全属于当前实例。DicePP 启动和升级不会从 `templates/` 自动复制、合并或覆盖内容；只有用户显式新建或导入后，文件才会进入 `content/`。

## 配置优先级

从高到低：

1. 环境变量，如 `DICE_MASTER`
2. 账号配置：`config/bots/{QQ号}.json`
3. 用户覆盖配置：`config/user.json`
4. 全局配置：`config/global.json`

配置会深度合并。比如 `user.json` 只写 API Key，不需要复制整段 `persona_ai`。

## 创建账号配置

复制模板：

```bash
cp config/bots/_template.json config/bots/你的QQ号.json
```

Windows PowerShell：

```powershell
Copy-Item config\bots\_template.json config\bots\你的QQ号.json
```

最小示例：

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "骰娘"
}
```

常用字段：

| 字段 | 说明 |
|------|------|
| `master` | 最高权限用户，通常填你自己的 QQ |
| `admin` | 管理员 QQ 列表 |
| `friend_token` | 添加好友口令 |
| `persona` | 默认文字人设，不等同于 Persona AI |
| `nickname` | 机器人昵称 |

## 敏感信息放哪里

API Key 放在 `config/user.json`：

```json
{
  "persona_ai": {
    "providers": {
      "minimax": {
        "api_key": "your-api-key-here"
      }
    }
  }
}
```

不要把 `user.json` 发给别人，也不要提交到 Git。

## Persona 定时主动分享

定时主动分享的字段位于 `config/global.json` 的 `persona_ai` 段，默认全部关闭。它只向明确列入白名单的私聊或群聊发送，不会按好感度或群活跃度自动扩大接收范围。

```json
{
  "persona_ai": {
    "proactive_share_schedule_enabled": true,
    "proactive_share_schedule_morning_enabled": true,
    "proactive_share_schedule_evening_enabled": false,
    "proactive_share_schedule_times": ["14:00", "18:30"],
    "proactive_share_schedule_jitter_minutes": 15,
    "proactive_always_send_users": ["123456"],
    "proactive_always_send_groups": ["123456789"]
  }
}
```

| 字段 | 含义 |
|------|------|
| `proactive_share_schedule_enabled` | 日程总开关；必须为 `true` 才会发送 |
| `proactive_share_schedule_morning_enabled` | 在角色卡定义的活动日开始后发送早安 |
| `proactive_share_schedule_evening_enabled` | 在角色卡定义的活动日结束前发送晚安 |
| `proactive_share_schedule_times` | 自定义日程，使用 `HH:MM`，例如 `"18:30"` |
| `proactive_share_schedule_jitter_minutes` | 每个时间点的正负随机偏移分钟数，范围 `0`–`120` |
| `proactive_always_send_users` | 接收定时主动消息的私聊 QQ 号列表 |
| `proactive_always_send_groups` | 接收定时主动消息的群号列表 |

总开关打开但没有任何时间点，或接收者列表都为空时，不会发送消息。早安/晚安需要角色卡具有活动日开始/结束时间；缺失时会跳过。管理员可用 `.ai admin pause` 和 `.ai admin resume` 暂停或恢复主动消息。完整使用说明见 [Persona AI](./persona.md)。

## 常用环境变量

| 变量 | 作用 | 默认值 |
|------|------|------|
| `DICE_MASTER` | 覆盖 master，多个值用逗号分隔 | |
| `DICE_ADMIN` | 覆盖 admin，多个值用逗号分隔 | |
| `DICE_NICKNAME` | 覆盖机器人昵称 | |
| `DICE_PERSONA` | 覆盖默认人设 | |
| `DICEPP_PROJECT_ROOT` | 覆盖项目根目录，一般不用 | |
| `DICEPP_DATA_DIR` | 兼容旧部署：只覆盖运行时 `data/` 目录；Bot 与 Dashboard 使用同一解析规则 | |
| `DICEPP_MANAGER_URL` | Manager 地址（Bot 控制 WebSocket 与 Dashboard API 代理） | 本机 `http://127.0.0.1:4091`；源码 Bot 必须显式设置 |
| `DICEPP_ONEBOT_HOST` | OneBot 监听地址；远程 OneBot 客户端需显式设为 `0.0.0.0` | `127.0.0.1`（Compose 部署显式设为 `0.0.0.0`） |

### Manager 环境变量

标准部署已经设置好以下变量，普通用户不需要手动修改。它们属于进程连接和部署元数据，不替代 `config/*.json` 中的 DicePP 业务配置。

| 变量 | 使用方 | 作用 | 默认值/标准部署值 |
|---|---|---|---|
| `DICEPP_MANAGER_URL` | Bot、Dashboard | Manager 地址；Bot 将其转换为 `/v1/control/ws`，Dashboard 调用 HTTP API | 本机 `http://127.0.0.1:4091`；Compose `http://manager:4091` |
| `DICEPP_MANAGER_TOKEN_FILE` | Manager、Dashboard | 私有 HTTP API token 文件；Bot 不读取、不使用 | `<instance>/manager/state/api-token` |
| `manager/control/control-token` | Bot、Manager | Bot WebSocket 控制凭据；与 HTTP API token 独立，Dashboard 不读取 | `<instance>/manager/control/control-token` |
| `DICEPP_MANAGER_CLIENT_TIMEOUT` | Dashboard | 调用 Manager 的超时秒数 | `10` |
| `DICEPP_MANAGER_HOST` | Manager | API 监听地址 | 本机 `127.0.0.1`；Compose `0.0.0.0` |
| `DICEPP_MANAGER_PORT` | Manager | API 监听端口 | `4091` |
| `DICEPP_MANAGER_RUNTIME` | Manager | Runtime Adapter 类型 | 标准 Linux 为 `docker`，Windows 为 `process` |
| `DICEPP_MANAGER_RUNTIME_UNIT_ID` | Manager | 默认运行单元标识 | `dicepp-runtime` |
| `DICEPP_MANAGER_DOCKER_COMMAND` | Linux Manager | Docker 控制端点；标准部署使用 Unix Socket，测试/兼容环境可指定单一 CLI 路径 | `unix:///var/run/docker.sock` |
| `DICEPP_MANAGER_DOCKER_TIMEOUT` | Linux Manager | Docker 固定操作超时秒数 | `30` |
| `DICEPP_MANAGER_CONTROL_HEARTBEAT_TIMEOUT` | Manager | Bot status/pong 心跳超时秒数 | `120` |
| `DICEPP_MANAGER_CONTROL_RELOAD_TIMEOUT` | Manager | 等待旧版 control reload 兼容响应的秒数（Dashboard 不再调用） | `5` |

Manager API、operation store 和部署拓扑分别带有独立的兼容版本。当前标准部署使用 Manager API `3`、operation schema `2` 和 deployment schema `2`。这些值由程序与发布包共同声明，用户不应通过环境变量强行覆盖。Linux Bot 容器使用以下标签声明可管理范围：

```yaml
labels:
  io.dicepp.managed: "true"
  io.dicepp.runtime-unit: "dicepp-runtime"
  io.dicepp.deployment-schema: "2"
```

Manager 只控制三个标签同时匹配的容器。修改 RuntimeUnit id 或部署 schema 时，必须同步 Manager 环境和容器标签；不匹配会被明确报告为不受支持。

## 修改后如何生效

**Web 管理面板**（推荐）：在面板中修改配置后点击保存。Manager 会先校验并原子写入磁盘；页面会列出同一 RuntimeUnit 内会一起短暂离线的 QQ 账号，并提供重启按钮。配置只会在 RuntimeUnit 重启后完整生效。

**手动编辑**：修改 JSON 文件后重启：

```bash
docker compose restart bot
```

`.reload` 仅保留为兼容提示，不会修改运行中的配置。直接编辑 JSON 时应先确认格式正确，再重启对应 RuntimeUnit；标准 Compose 也可以只重启 Bot 服务。

## 配置不生效时检查

1. 文件名是否是机器人 QQ 号：`config/bots/{QQ号}.json`
2. JSON 是否合法，逗号和引号是否正确
3. Docker 容器里是否挂载了你修改的配置
4. 是否被环境变量覆盖

Docker 中查看配置：

```bash
docker exec dicepp cat /app/config/global.json
docker exec dicepp cat /app/config/user.json
```

## 从旧版本迁移

新版本主要使用：

- `config/`：配置
- `content/`：角色卡、牌组、查询数据等内容
- `data/`：运行时数据
- `dashboard/data/`：Dashboard 本地账号和会话数据
- `manager/`：Manager 状态、下载缓存和事务安全归档

Bot、Dashboard 和 Manager 通过同一份实例布局解析这些目录。除兼容旧部署的 `DICEPP_DATA_DIR` 外，建议让三个目录保持在同一个实例根目录，便于归档和跨平台迁移。普通归档只保存配置和 Catalog 管理的 `data/`；完整归档才包含可能很大的 `content/`。

当前不提供旧 `Data` 目录的自动迁移。若你手上仍有旧版本 `Data` 资产，请先整体备份，再根据当前 `config/`、`content/`、`data/` 文档手工整理到新目录结构；不要假设旧 Excel 文件会被自动兼容或自动导入。
