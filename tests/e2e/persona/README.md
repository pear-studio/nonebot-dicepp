# Persona 端到端验收测试（真实 LLM）

本目录包含使用**真实 LLM API** 的端到端测试，验证角色一天完整生命周期的事件生成质量和内容连贯性。

## ⚠️ 前置条件

运行本目录下的测试需要：

1. `config/secrets.json` 中配置有效的 `persona_ai.primary_api_key`
2. `config/secrets.json` 中配置 `primary_base_url`（如 `https://api.minimaxi.com/v1`）
3. `config/secrets.json` 中配置 `primary_model`（如 `MiniMax-M2.7`）

示例 `config/secrets.json`：

```json
{
  "persona_ai": {
    "primary_api_key": "sk-your-key-here",
    "primary_base_url": "https://api.minimaxi.com/v1",
    "primary_model": "MiniMax-M2.7"
  }
}
```

## 运行方式

### 作为 pytest 测试

```bash
# 运行 e2e 测试（默认不运行，需显式指定 -m e2e）
uv run pytest tests/e2e/persona/ -v -m e2e

# 运行全部测试（包含 e2e）
uv run pytest -v -m e2e
```

默认 `uv run pytest` 会自动排除 e2e 测试。无 API key 时，e2e 测试会自动跳过。

### 独立运行（不经过 pytest）

```bash
uv run python tests/e2e/persona/test_character_lifecycle_real_llm.py
```

独立运行在无 API key 时会打印错误信息并退出。

## 测试内容

`test_character_lifecycle_real_llm.py` 模拟完整一天：

- 起床边界事件（wake_up）
- 槽位事件链（1~3 个连续事件，由 LLM 自主续写）
- 睡觉边界事件（good_night）
- 日记生成（基于当天所有事件）

每次运行消耗约 **7~10 次 LLM 调用**（视链深度而定）。

## 验收标准

测试自动断言：

- 槽位事件链深度 >= 1
- 日记长度 50~300 字
- 日记自然提及当天事件（包含角色名或活动关键词）
- 最终状态值在 0~100 范围内

人工审阅重点：

- 事件描述是否连贯（如"冲咖啡"→"端着咖啡看书"→"书签滑落"）
- 反应内容是否体现角色性格
- 日记语气是否与角色设定一致
