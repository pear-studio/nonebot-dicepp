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
| `content/characters/` | Persona 角色卡和皮肤 | 默认只提交示例 |
| `data/` | 运行时数据 | 不提交 |

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

## 常用环境变量

| 变量 | 作用 |
|------|------|
| `DICE_MASTER` | 覆盖 master，多个值用逗号分隔 |
| `DICE_ADMIN` | 覆盖 admin，多个值用逗号分隔 |
| `DICE_NICKNAME` | 覆盖机器人昵称 |
| `DICE_PERSONA` | 覆盖默认人设 |
| `DICEPP_PROJECT_ROOT` | 覆盖项目根目录，一般不用 |
| `DPP_ADMIN_HOST` | Dashboard 地址（bot 用于建立 WebSocket 控制通道） | `127.0.0.1` |
| `DPP_ADMIN_PORT` | Dashboard 端口 | `4090` |

## 修改后如何生效

**Web 管理面板**（推荐）：在面板中修改配置后点击保存，自动写入磁盘并通知 Bot 热重载。

**手动编辑**：修改 JSON 文件后重启：

```bash
docker compose restart
```

或通过 QQ 发送 `.reload` 热重载。如果 JSON 写错，热重载会保留旧配置。

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

3.0 尚未提供旧 `Data` 目录的自动迁移。若你手上仍有旧版本 `Data` 资产，请先整体备份，再根据当前 `config/`、`content/`、`data/` 文档手工整理到新目录结构；不要假设旧 Excel 文件会被自动兼容或自动导入。
