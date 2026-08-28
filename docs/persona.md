# Persona AI 使用

Persona AI 可以让 DicePP 扮演一个有角色设定、记忆和正常对话能力的 AI 角色。

开始前先确认 DicePP 已能正常收发 `.help`。

## 准备 API Key

先准备一个 DeepSeek API Key。

API Key 只写到实例级 `config/user.json`。Bot 配置不包含模型连接信息。

在 `config/user.json` 中加入：

```json
{
  "deepseek_api_key": "把你的 API Key 放在这里",
  "deepseek_model": "deepseek-v4-flash",
  "deepseek_base_url": "https://api.deepseek.com"
}
```

如果文件里已经有其他内容，只把这几个字段合并进去，不要重复写两个最外层 `{}`。

## 启用 Persona

Persona 的设置写在 `config/bots/{QQ号}.json` 的 `persona_ai` 段；未写出的字段使用程序内置默认值。

新手只需要先确认这几项：

```json
"enabled": true,
"character_name": "qiqi.local",
"daily_limit": 20
```

`persona_ai.enabled` 默认是 `false`。只有设为 `true` 时才会启动 Persona；角色名由同一段配置中的
`persona_ai.character_name` 指定，并对应 `content/characters/{名字}/` 目录。默认角色名是
`qiqi.local`。

启用 Persona 时，目标角色目录必须包含 `skin.yaml`；空文件也可以，表示使用该角色的内置默认文本。
如果文件缺失或格式错误，Bot 会明确启动失败，不会静默回退到 `default` 皮肤。

启用并成功初始化 Persona 后：

- `.jrrp` 由 Persona 处理，私聊和群聊都支持。
- 群聊中的正常 `@机器人` / `.ai` 对话路径始终可用。
- 对话始终注入内置的分段回复引导和长度策略。

含义：

| 字段 | 说明 |
|------|------|
| `enabled` | 设为 `true` 才会启用 Persona（在该 Bot 配置中设置） |
| `character_name` | 角色卡目录名，在 `persona_ai` 段中设置 |
| `daily_limit` | 普通用户每日主模型调用次数 |

DeepSeek 的模型配置由实例级 `config/user.json` 管理，默认接口地址是：

```text
https://api.deepseek.com
```

`deepseek_api_key`、`deepseek_model` 和 `deepseek_base_url` 只放在 `config/user.json`。

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

然后在 `config/bots/{账号}.json` 的 `persona_ai` 段中设置：

```json
"persona_ai": {
  "enabled": true,
  "character_name": "mychar"
}
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
4. 私聊或群聊发送 `.jrrp`，确认由 Persona 生成结果。

## AI 限额豁免名单

Persona 对话默认对所有用户开放。管理员可维护不计入 AI 限额的用户和群聊名单：

```text
.ai admin whitelist add user 用户号
.ai admin whitelist add group 群号
.ai admin whitelist remove 用户号
.ai admin whitelist remove group 群号
.ai admin whitelist clear
```

## 常用管理员命令

| 命令 | 作用 |
|------|------|
| `.ai admin` | 查看当前 Persona 状态 |
| `.ai admin reload` | 重新加载角色卡 |
| `.ai admin events` | 查看角色事件配置 |
| `.ai admin today` | 查看今天的日记和事件 |
| `.ai admin whitelist` | 查看 AI 限额豁免名单 |

这些命令只对 `master` 有效。

## 常见问题

### 启动日志没有 persona.init

检查：

- `persona_ai.enabled` 是否为 `true`
- `config/bots/{账号}.json` 中 `persona_ai.character_name` 是否对应 `content/characters/{name}`
- `character.yaml` 是否存在
- YAML 缩进是否正确

### 发送 .ai 无反应

检查：

- Persona 模块是否已启用，可发送 `.ai status`
- 是否超过 `daily_limit`
- 日志中是否有 LLM 错误

### LLM 调用失败

检查：

- `api_key` 是否写在实例级 `config/user.json`
- `base_url` 是否正确
- 模型名是否写对

DeepSeek 常用地址：

```text
https://api.deepseek.com
```

### 修改角色卡后没有生效

管理员发送：

```text
.ai admin reload
```

如果仍不生效，重启 DicePP 并检查日志。
