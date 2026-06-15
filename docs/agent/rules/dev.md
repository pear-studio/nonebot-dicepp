# DicePP Development Rules

当前目录是 DicePP 开发环境。可以在用户任务范围内修改代码、文档、测试和 agent 配置；完成前必须执行与风险相称的验证，并报告验证结果。

## 开发命令

```bash
# 初始化环境
uv venv .venv && uv pip install ".[dev]"

# 运行测试
uv run pytest

# 运行指定模块测试
uv run pytest tests/module/roll/ -v

# 启动机器人
uv run python bot.py
```

## 测试与验收

- **单元/集成测试**：优先使用 `run-tests` 技能，或直接运行 `uv run pytest`。
- **交互式验收**：新功能完成前，**必须**使用 `dicepp-shell` 技能进行交互式机器人测试，确认指令行为正确。
- **提交前**：必须跑通 `uv run pytest`，不自动 push。

## Persona AI 测试 key

开发分支测试 persona 模块时，在 `config/secrets.json` 的 `persona_ai.providers.<name>.api_key` 字段填入测试 API Key。

```json
{
  "persona_ai": {
    "providers": {
      "minimax": {
        "api_key": "sk-test-xxx"
      }
    }
  }
}
```

**用量约束**：测试 key 按量计费，单次全量跑测建议控制在 10 次 LLM 调用以内。如当前环境未配置测试 key，可向用户索取。
