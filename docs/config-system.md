# DicePP 配置系统

DicePP 使用分层 JSON 配置系统（已替代旧的 Excel 配置）。

## 目录结构

```
config/                 # 配置目录（可提交到版本库）
├── global.json        # 全局默认配置
├── secrets.json       # 敏感信息（API密钥等）- gitignored
├── bots/              # 账号配置
│   ├── _template.json # 配置模板
│   └── {账号}.json    # 具体账号配置 - gitignored
└── personas/          # 人设配置
    ├── default.json
    └── {自定义}.json

data/                  # 运行时数据（可写）
├── bots/{账号}/       # 各账号数据
└── local_images/      # 本地图片

content/               # 内容资源（可写）
├── characters/        # 角色卡（Persona AI 使用）
├── decks/             # 牌组数据
├── excel/             # Excel 配置
├── queries/           # 查询数据库
└── random/            # 随机生成数据
```

## 配置优先级（高 → 低）

1. **环境变量** (`DICE_*` 前缀，如 `DICE_MASTER`)
2. **账号配置** (`config/bots/{账号}.json`)
3. **全局密钥** (`config/secrets.json`)
4. **全局默认** (`config/global.json`)

**合并规则**：深度合并。`secrets.json` 中的子对象会与 `global.json` 递归合并，而非完全替换。

## 快速开始

### 1. 复制模板创建账号配置

```bash
cp config/bots/_template.json config/bots/你的QQ号.json
```

### 2. 编辑账号配置

```json
{
  "master": ["你的QQ号"],
  "admin": [],
  "friend_token": ["添加好友口令"],
  "persona": "default",
  "nickname": "骰娘"
}
```

### 3. 启用 Persona AI（可选）

编辑 `config/global.json`：

```json
{
  "persona_ai": {
    "enabled": true,
    "character_name": "default",
    "character_path": "./content/characters",
    "primary_base_url": "https://api.minimaxi.com/v1",
    "primary_model": "MiniMax-M2.7",
    "max_concurrent_requests": 2,
    "timeout": 30,
    "daily_limit": 20
  }
}
```

编辑 `config/secrets.json`：

```json
{
  "persona_ai": {
    "primary_api_key": "你的API密钥"
  }
}
```

**⚠️ 注意**：`secrets.json` 只需包含敏感字段（如 API key），其他配置保留在 `global.json` 中。

## 全局默认配置 (`config/global.json`)

关键字段说明：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `agreement` | (长文本) | `.help协议` 内容 |
| `command_split` | `\\` | 多指令分隔符 |
| `group_invite` | `true` | 自动同意群邀请 |
| `bot_default_enable` | `true` | 默认开启响应 |
| `llm.enabled` | `false` | 旧版 LLM 模块（已弃用） |
| `persona_ai.enabled` | `false` | Persona AI 模块 |
| `mode.default` | `DND5E2024` | 默认游戏规则 |

## 人设配置 (`config/personas/`)

人设包含：
- **localization**: 覆盖本地化文本
- **chat**: 自定义对话触发规则
- **llm_personality**: AI 人格描述

示例 (`qiqi.json`)：

```json
{
  "name": "qiqi",
  "localization": {
    "login_notice": "……七七……早上好……"
  },
  "chat": {
    "^你好$": ["……你好……", "……你也好……"],
    "^.*椰奶.*$": ["……椰奶……喜欢……"]
  },
  "llm_personality": "你是原神中的七七，僵尸少女……"
}
```

## 角色卡配置 (`content/characters/`)

Persona AI 模块使用 SillyTavern V2 格式的角色卡：

```yaml
name: "角色名"
description: "角色背景"
personality: "性格描述"
scenario: "当前场景"
first_mes: "首次见面开场白"
mes_example: "示例对话"
system_prompt: "系统提示词"

# Persona 扩展
extensions:
  persona:
    initial_relationship: 20
    warmth_labels: ["陌生", "熟悉", "友好", "亲密"]
```

## 热重载

管理员（权限 ≥ 3）可运行 `.reload` 原子重载配置，无需重启：
- 验证失败时保留旧配置
- 编辑 JSON 后使用

## 环境变量

支持的环境变量：

| 变量 | 作用 |
|------|------|
| `DICE_MASTER` | 设置 master（逗号分隔多个） |
| `DICE_ADMIN` | 设置 admin（逗号分隔多个） |
| `DICE_NICKNAME` | 设置昵称 |
| `DICE_PERSONA` | 设置人设 |
| `DICEPP_PROJECT_ROOT` | 覆盖项目根目录 |

## 故障排除

### 配置未生效

检查配置是否正确挂载到容器：
```bash
docker exec dicepp cat /app/config/global.json
docker exec dicepp cat /app/config/secrets.json
```

### Docker 修改后未生效

代码修改需要重新构建镜像：
```bash
docker compose down
docker compose build
docker compose up -d
```

### 更多问题

参见 [MiniMax API 配置指南](./deploy-minimax-guide.md)

## 从旧版本迁移

### 从 Excel 配置迁移

| 旧文件 | 新位置 |
|--------|--------|
| `Data/Config.xlsx` | `config/global.json` |
| `Data/Localization.xlsx` | `config/personas/default.json` → `localization` |
| `Data/Chat.xlsx` | `config/personas/default.json` → `chat` |

### 从旧 Data/ 目录迁移

项目已重构为 `config/` + `data/` + `content/` 三个目录：
- **config/**: 只读配置，可版本控制
- **data/**: 运行时数据，容器挂载
- **content/**: 内容资源，容器挂载
