# Persona AI 使用

Persona AI 可以让 DicePP 扮演一个有角色设定、记忆和主动消息能力的 AI 角色。

开始前先确认 DicePP 已能正常收发 `.help`。

## 准备 API Key

先准备一个 LLM API Key。推荐先使用 MiniMax，也可以使用兼容 OpenAI 格式的接口。

API Key 只写到 `config/user.json`，不要写进 `config/global.json`。

在 `config/user.json` 中加入：

```json
{
  "persona_ai": {
    "providers": {
      "minimax": {
        "api_key": "把你的 API Key 放在这里"
      }
    }
  }
}
```

如果文件里已经有其他内容，只把 `persona_ai` 这一段合并进去，不要重复写两个最外层 `{}`。

## 启用 Persona

打开 `config/global.json`，找到 `persona_ai` 这一整段。

新手只需要先确认这几项：

```json
"enabled": true,
"character_path": "./content/characters",
"timezone": "Asia/Shanghai",
"daily_limit": 20
```

其中角色名不再写在 `global.json` 里，而是由每个 bot 的账号配置独立指定。
打开 `config/bots/{你的QQ号}.json`，在顶层加入：

```json
"persona": "default"
```

字段 `persona` 对应 `content/characters/{名字}/` 目录。设为 `null` 或不设置时 Persona 不启用。

含义：

| 字段 | 说明 |
|------|------|
| `enabled` | 设为 `true` 才会启用 Persona（global.json） |
| `persona` | 角色卡目录名，在 `config/bots/{账号}.json` 顶层设置 |
| `character_path` | 角色目录根路径，通常不用改 |
| `timezone` | 时区，国内建议 `Asia/Shanghai` |
| `daily_limit` | 普通用户每日主模型调用次数 |

MiniMax 的模型配置通常已经在 `global.json` 里。如果你需要手动补，确认 `providers.minimax.base_url` 是：

```text
https://api.minimaxi.com/v1
```

不要把 `api_key` 填到 `global.json` 里。

## 准备角色卡

角色卡属于实例用户数据，存放在：

```text
content/characters/{角色名}/
```

DicePP 不会在启动或升级时自动生成 `content/characters/default/`。程序包内的 `templates/characters/default/` 是只读模板资源，预留给 Dashboard 的“新建角色”功能，不会被 Bot 当作角色卡读取。

当前可手动建立角色目录，例如：

```text
content/characters/mychar/
  character.yaml
  skin.yaml
```

然后把 `config/bots/{账号}.json` 中的 `persona` 改成：

```json
"persona": "mychar"
```

如果配置了角色名但实例 `content/characters/{角色名}/character.yaml` 不存在，Persona AI 不会从程序模板静默回退；请先创建或导入角色卡。

角色卡写法见 [persona-character-card.md](./persona-character-card.md)。

## 重启并验证

保存配置后，按你的部署方式重启 DicePP：

- Windows 部署：重启 DicePP 程序。
- Linux Docker 部署：在部署目录执行 `docker compose restart`。

验证：

1. 启动日志中应出现 `[persona.init] character=... loaded`。
2. 私聊机器人发送 `.ai`。
3. 群聊中 `@机器人 你好`。

## 白名单和口令

默认未设置口令时，白名单不会拦截访问，适合测试。

生产环境建议由管理员设置口令：

```text
.ai admin code 你的口令
```

用户私聊加入：

```text
.ai join 你的口令
```

群聊白名单由管理员添加：

```text
.ai admin whitelist add group 群号
```

## 常用管理员命令

| 命令 | 作用 |
|------|------|
| `.ai admin` | 查看当前 Persona 状态 |
| `.ai admin reload` | 重新加载角色卡 |
| `.ai admin events` | 查看角色事件配置 |
| `.ai admin today` | 查看今天的日记和事件 |
| `.ai admin pause` | 暂停主动消息 |
| `.ai admin resume` | 恢复主动消息 |
| `.ai admin list` | 查看白名单 |

这些命令只对 `master` 或 `admin` 有效。

## 常见问题

### 启动日志没有 persona.init

检查：

- `persona_ai.enabled` 是否为 `true`
- `config/bots/{账号}.json` 顶层的 `persona` 是否对应 `content/characters/{name}`
- `character.yaml` 是否存在
- YAML 缩进是否正确

### 发送 .ai 无反应

检查：

- 是否被白名单拦截，可发送 `.ai status`
- 是否超过 `daily_limit`
- 日志中是否有 LLM 错误

### LLM 调用失败

检查：

- `api_key` 是否写在 `config/user.json`
- `base_url` 是否正确
- 模型名是否写对

MiniMax 常用地址：

```text
https://api.minimaxi.com/v1
```

### 主动消息太频繁

先暂停：

```text
.ai admin pause
```

再调整 `config/global.json` 中的主动消息相关配置，重启或 `.reload`。

### 修改角色卡后没有生效

管理员发送：

```text
.ai admin reload
```

如果仍不生效，重启 DicePP 并检查日志。
