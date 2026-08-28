# 配置入门

DicePP 使用 JSON 配置。常用文件是：

- `config/bots/{QQ号}.json`：单个 Bot 账号配置，包括 Persona AI 和 API Key；
- `config/user.json`：当前保留为空，暂不需要填写；
- `content/`：角色卡、牌组、随机表和查询库；
- `data/`：Bot 运行数据库、日志和存档 `data/backups/`；
- `dashboard/data/`：Dashboard 管理员和 session 数据；
- `data/backups/`：Dashboard 存档库存。不要提交包含密码或 API Key 的 JSON。

## 创建 Bot 配置

直接创建 `config/bots/{QQ号}.json`。缺失文件时 Bot 使用代码默认值，Dashboard
首次保存时才创建配置文件；不要复制模板文件。

最小示例：

```json
{
  "master": "你的QQ号",
  "friend_request_token": "添加好友口令",
  "accept_group_invites": true,
  "persona": "default"
}
```

常用字段：

| 字段 | 说明 |
|---|---|
| `master` | 唯一 Master QQ号，拥有全部管理权限 |
| `friend_request_token` | 添加好友口令；留空接受全部好友请求 |
| `accept_group_invites` | 是否接受加群和群邀请请求 |
| `persona` | 默认文字人设 |

多条 Bot 指令使用固定的两个反斜杠（`\\`）分隔，不属于配置项，也没有环境变量覆盖。

## API Key 与环境变量

API Key 放在未提交的 `config/bots/{QQ号}.json`，例如：

```json
{
  "persona_ai": {
    "providers": {
      "deepseek": {"api_key": "your-api-key-here"}
    }
  }
}
```

常用环境变量：

| 变量 | 作用 | 默认值 |
|---|---|---|
| `DICE_PERSONA` | 覆盖默认人设 | |
| `DICEPP_PROJECT_ROOT` | 覆盖实例根目录 | 当前目录 |
| `DICEPP_DATA_DIR` | 覆盖运行时 data 目录 | |
| `DICEPP_ONEBOT_HOST` | OneBot 监听地址 | `127.0.0.1` |

Compose 中将 `DICEPP_ONEBOT_HOST` 设为 `0.0.0.0`，供同一 `dice-net` 网络中的 NapCat/LLOneBot 连接 `ws://dicepp:8080/onebot/v11/ws`。Dashboard 监听 4090。

## 配置何时生效

Dashboard 保存配置后会返回 `restart_required`。按页面提示停止并重新启动 Bot；不会通过隐藏进程或网络控制通道热重载。

手动编辑 JSON 后：

```bash
docker compose restart dicepp
```

Windows 请退出并重新启动 `DicePP.exe`。如果配置不生效，先检查 JSON 格式、文件名、挂载目录和环境变量覆盖。

## 数据维护

创建存档、清空业务数据和导入空实例前，必须停止 Bot。Dashboard 管理数据库、session、日志、程序文件和存档不会被清空。
