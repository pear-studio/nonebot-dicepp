# DicePP Configuration System

## Overview

DicePP uses a layered JSON-based configuration system (replaced the old Excel-based one).

## File Locations

| File | Purpose | Git-tracked? |
|------|---------|-------------|
| `Data/config.json` | Global defaults (public) | ✅ Yes |
| `Data/config.local.json` | Global secrets (API keys, etc.) | ❌ No (gitignored) |
| `Data/bots/{account}.local.json` | Account-specific settings | ❌ No (gitignored) |
| `Data/personas/{name}.json` | Persona definitions | ✅ Yes |

## Priority Order (highest → lowest)

1. **Environment variables** (`DICE_*` prefix, e.g. `DICE_LLM_API_KEY`)
2. **Account config** (`Data/bots/{account}.local.json`)
3. **Global secrets** (`Data/config.local.json`)
4. **Persona LLM personality** (`Data/personas/{persona}.json` → `llm_personality`)
5. **Global defaults** (`Data/config.json`)

## Quickstart

1. Copy `Data/bots/_template.json` → `Data/bots/{your_account}.local.json`
2. Set `master`, `admin`, and `llm.api_key` in the account file
3. Run the bot

## Account Config (`Data/bots/{account}.local.json`)

```json
{
  "master": ["123456789"],
  "admin": ["987654321"],
  "friend_token": [],
  "persona": "default",
  "nickname": "骰娘",
  "llm": {
    "api_key": "sk-..."
  }
}
```

## Global Defaults (`Data/config.json`)

Contains all default values. You can override any field here, or leave them at defaults.
Key fields:

| Field | Default | Description |
|-------|---------|-------------|
| `agreement` | (long string) | `.help协议` content |
| `command_split` | `"\\\\"` | Multi-command separator |
| `group_invite` | `true` | Accept group invites |
| `data_expire` | `false` | Auto-delete old user/group data |
| `llm.enabled` | `false` | Enable LLM chat module |
| `dicehub.api_url` | `""` | DiceHub registration URL |
| `roll.enable` | `true` | Roll commands enabled |
| `mode.default` | `"DND5E2024"` | Default game mode |

## Persona System

A persona bundles:
- **Localization overrides**: custom response texts per key
- **Chat patterns**: regex → response list (replaces built-in chat)
- **LLM personality**: system prompt override

### Creating a Persona

Create `Data/personas/mypersona.json`:

```json
{
  "name": "mypersona",
  "localization": {
    "login_notice": "我来了！",
    "bot_show": "有什么需要帮忙的吗~"
  },
  "chat": {
    "^你好$": ["你好呀！", "嗨~"],
    "帮助": "使用 .help 查看指令列表"
  },
  "llm_personality": "你是一个活泼可爱的助手。"
}
```

Set in account config: `"persona": "mypersona"`.

Unspecified keys fall back to code-level defaults.

## Hot Reload

Admins (permission ≥ 3) can run `.reload` to atomically reload config + personas without restarting:
- If validation fails, old config is preserved
- Use after editing any JSON config file

## Migration from Excel

The old `Config.xlsx`, `localization.xlsx`, `chat.xlsx` files are no longer used.

| Old | New |
|-----|-----|
| `Config.xlsx` → `[CFG_KEY]` rows | `Data/config.json` fields |
| `localization.xlsx` | `Data/personas/default.json` → `localization` section |
| `chat.xlsx` | `Data/personas/default.json` → `chat` section |
| `cfg_helper.get_config(CFG_KEY)` | `bot.config.field_name` |

## Standalone Mode CLI / Env Vars

| CLI | Env Var | Purpose |
|-----|---------|---------|
| `--bot-id` | `BOT_ID` | Bot account ID (required) |
| `--hub-url` | `HUB_URL` | DiceHub API URL |
| `--master-id` | `MASTER_ID` | Master user ID |
| `--nickname` | `NICKNAME` | Bot nickname |
| `--port` | `PORT` | HTTP listen port (default 8080) |

DICE_* env vars (e.g. `DICE_LLM_API_KEY`, `DICE_PERSONA`) map directly to config fields and always override JSON files.
