# Persona 模块部署指南

> 本指南面向希望启用 DicePP AI 对话功能的部署者，涵盖 API Key 配置、角色卡准备、启动验证等步骤。

---

## 前置条件

1. DicePP 本体已按 [`docs/deploy.md`](../../deploy.md) 完成部署，可正常收发消息。
2. 拥有一个 LLM API Key（推荐 MiniMax，也兼容任何 OpenAI 格式接口）。
3. 了解配置文件合并规则：`config/global.json` < `config/secrets.json` < `config/bots/{账号}.json`。

---

## 第一步：配置 API Key

**敏感信息只写入 `config/secrets.json`**，不要提交到版本库。

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

如果需要多个 provider（如同时使用 MiniMax 和 DeepSeek），可配置多个：

```json
{
  "persona_ai": {
    "providers": {
      "minimax": {
        "api_key": "sk-minimax-xxx",
        "base_url": "https://api.minimaxi.com/v1"
      },
      "deepseek": {
        "api_key": "sk-deepseek-xxx",
        "base_url": "https://api.deepseek.com/v1"
      }
    }
  }
}
```

- 密钥 `api_key` 建议只写在 `secrets.json` 中，`base_url` 和 `models` 写在 `global.json`。
- 所有配置会被深度合并进 `global.json` 的 `persona_ai` 对象，无需在 `secrets.json` 中重复写非敏感字段。

---

## 第二步：启用 Persona 模块

编辑 `config/global.json`，将 `persona_ai.enabled` 设为 `true`：

```json
"persona_ai": {
  "enabled": true,
  "character_name": "default",
  "character_path": "./content/characters",
  "providers": {
    "minimax": {
      "base_url": "https://api.minimaxi.com/v1",
      "models": [
        {"name": "MiniMax-M3", "category": "llm", "capabilities": ["text", "tool_calls"], "quality": 0.7, "cost": 0.5}
      ]
    }
  },
  "max_concurrent_requests": 2,
  "timeout": 30,
  "timezone": "Asia/Shanghai",
  "daily_limit": 20
}
```

常用字段说明：

| 字段 | 说明 |
|------|------|
| `character_name` | 角色卡子目录名，对应 `content/characters/{name}/character.yaml` |
| `character_path` | 角色卡存放目录，默认 `./content/characters` |
| `providers.<name>` | LLM 提供者配置，每个 provider 下含 `api_key`、`base_url`、`models` 列表 |
| `providers.<name>.models[*]` | 模型定义：`name`、`category`（llm/gen）、`capabilities`、`quality`（0-1）、`cost`（0-1） |
| `daily_limit` | 每日调用次数上限（白名单用户不受此限制） |
| `timezone` | 业务时区，影响日记、事件、安静时段，须为 IANA 名称（如 `Asia/Shanghai`） |

> 完整字段列表见 [`config-example.md`](./config-example.md)。

---

## 第三步：创建角色卡

角色卡是 YAML 文件，存放于 `content/characters/{name}/` 子目录下，目录名与 `character_name` 对应。每个角色目录包含 `character.yaml`（角色定义）和 `skin.yaml`（皮肤/回复风格）。

仓库已提供一个示例 `content/characters/default/`，可直接复制修改：

```bash
cp -r content/characters/default content/characters/mychar
```

然后编辑 `config/global.json`：

```json
"character_name": "mychar"
```

角色卡格式兼容 SillyTavern V2，并扩展了 `extensions.persona` 字段。完整字段说明与示例见 [`character_card.md`](./character_card.md)。

---

## 第四步：启动并验证

### 本地（uv）

```bash
uv run python bot.py
```

### Docker

```bash
docker compose up -d
```

### 验证清单

1. **日志检查**：启动日志中应出现 `[persona.init] character=xxx loaded`。
2. **自我介绍**：私聊发送 `.ai`，应收到角色的自我介绍文本。
3. **白名单**：若 `whitelist_enabled=true` 且已设置口令，发送 `.ai join <口令>` 后应提示加入成功；再次发送 `.ai` 应能正常对话。
4. **对话测试**：私聊 `@bot 你好`，应收到角色化回复。
5. **配额检查**：非白名单用户每日主模型调用超过 `daily_limit` 次后，应收到配额耗尽提示。

---

## 第五步：管理员命令设置口令（可选但推荐）

默认情况下，如果**未设置口令**，白名单功能不激活，所有用户都能使用 AI 对话（适合测试）。

生产环境建议由管理员设置口令：

```
.ai admin code <你的口令>
```

设置后，只有私聊发送 `.ai join <口令>` 的用户才能使用 AI 对话；群聊需要管理员手动加入群白名单：

```
.ai admin whitelist add group <group_id>
```

---

## 故障排查

### 启动日志没有 `[persona.init]`

- 检查 `persona_ai.enabled` 是否为 `true`。
- 检查角色卡文件是否存在且 YAML 语法正确。

### 发送 `.ai` 无反应

- 检查是否已加入白名单（发送 `.ai status` 查看）。
- 检查日志中是否有白名单拦截或配额耗尽记录。

### LLM 调用报错

- 检查 `providers.<name>.base_url` 和 `providers.<name>.api_key` 是否正确。
- 查看 `[persona.llm]` 日志中的 `status` 字段（`timeout` / `rate_limit` / `auth_error` 等）。
- 开启 trace 调试（`trace_enabled: true`），用 `.ai admin errors` 查看最近错误摘要。

### 配置修改后未生效

- Docker 部署需重新构建镜像：`docker compose down && docker compose build && docker compose up -d`。
- 本地 uv 直接重启即可生效。
