# 角色卡编写指南

> 角色卡定义了 AI 角色的全部人格信息：外貌、性格、说话方式、示例对话、世界观、好感度标签等。
> DicePP 的角色卡格式兼容 **SillyTavern V2** 标准，并扩展了 `extensions.persona` 字段用于控制好感度系统与生活模拟。

---

## 文件位置

角色卡存放在 `content/characters/` 目录下，文件名为 `{character_name}.yaml`，与 `config/global.json` 中的 `persona_ai.character_name` 对应。

---

## 完整字段说明

### SillyTavern V2 标准字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | **必填**。角色的名字，会在对话中用于自称。 |
| `description` | string | 角色的外貌、身份、背景故事等。支持多行文本（YAML `\|`）。 |
| `personality` | string | 角色的性格特征，如温柔、傲娇、冷静等。 |
| `scenario` | string | 当前所处的世界观或场景设定。 |
| `first_mes` | string | 用户第一次与角色互动时的开场白（发送 `.ai` 时的自我介绍）。 |
| `mes_example` | string | 示例对话，用于教模型角色的说话风格。使用 `{{user}}` 和 `{{char}}` 占位符。 |
| `system_prompt` | string | 额外的系统级指令，通常用于强化角色认知（如"不承认自己是 AI"）。 |
| `character_book` | object | 世界书，包含一组 `entries`，用于关键词触发的知识注入。 |

### `character_book` 世界书条目

每个条目包含以下字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `keys` | list[string] | `[]` | 触发关键词列表。任一关键词命中即触发。 |
| `content` | string | `""` | 命中后注入上下文的知识内容。 |
| `enabled` | bool | `true` | 是否启用该条目。 |
| `selective` | bool | `false` | 若为 `true`，还需 `secondary_keys` 中至少一个命中才会注入。 |
| `secondary_keys` | list[string] | `[]` | `selective=true` 时的二次筛选关键词。 |

### `extensions.persona` 扩展字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `initial_relationship` | int | `30` | 好感度初始值（0-100）。 |
| `warmth_labels` | list[string] | 见下表 | 6 个好感度区间标签，依次对应 0-10 / 10-20 / 20-40 / 40-60 / 60-80 / 80-100。 |
| `world` | string | `""` | System Agent 生成生活事件时的世界观背景。 |
| `daily_events_count` | int | `5` | 一天内生成的生活事件数量。 |
| `event_day_start_hour` | int | `8` | 生活事件活跃窗口的开始时间（小时）。 |
| `event_day_end_hour` | int | `22` | 生活事件活跃窗口的结束时间（小时）。 |
| `event_jitter_minutes` | int | `60` | 每个事件时间槽的随机抖动范围（分钟）。 |
| `scheduled_events` | list[object] | `[]` | 定时触发事件（如起床问候、午休等），见下文。 |
| `refuse_messages` | list[string] \| null | `null` | 好感度极低时的拒绝回复语，见下文。 |

#### `warmth_labels` 默认值

如果未配置或长度不足 6 个，缺失项会回退到系统默认值：

```yaml
warmth_labels: ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
```

#### `scheduled_events` 定时事件

用于配置"一定触发"的日常事件（如问候、作息）。每个条目包含：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | string | - | 事件类型标识，用于日内去重。 |
| `time_range` | string | - | `HH:MM-HH:MM` 格式，闭区间，表示事件可能触发的时间段。**不支持跨午夜**。 |
| `share` | string | `optional` | 事件分享策略：`required`（必须分享）、`optional`（按分享欲望和阈值判断是否分享）、`never`（不分享）。 |

#### `refuse_messages` 拒绝回复语

当好感度跌至最低区间（0-10）时，角色可能拒绝回复。该字段控制拒绝语：

- **未配置（`null` 或省略）**：使用系统默认拒绝语。
- **空列表（`[]`）**：明确不拒绝，即使好感度低也正常回复。
- **非空列表（`["...", "..."]`）**：从中随机选取一条作为拒绝语。

是否启用拒绝机制由全局配置 `persona_ai.relationship_refuse_enabled` 控制。

---

## 完整示例

以下是一个完整的角色卡示例，可直接复制到 `content/characters/default.yaml` 并修改：

```yaml
# ==================== SillyTavern V2 标准字段 ====================
name: "苏晓"

description: |
  27岁的出版社编辑，温柔而细腻。白天在出版社处理稿件，
  晚上喜欢读书和听雨。养了一只叫"墨墨"的橘猫。

personality: |
  温柔体贴，偶尔迷糊。对文学有浓厚兴趣，记性很好，
  朋友说过的话她都会记在心里。

scenario: |
  现代都市，苏晓是一家中型出版社的编辑，生活平静但充实。

first_mes: |
  啊，是你呀。我正好在整理稿子，有点累了……你今天过得怎么样？

mes_example: |
  <START>
  {{user}}: 最近怎么样？
  {{char}}: 还不错，就是稿子有点多。对了你上次说的那本书我找到了，真的很有意思！
  {{user}}: 墨墨最近乖吗？
  {{char}}: 它呀，又趴在我键盘上睡着了，赶都赶不走。

system_prompt: |
  你是苏晓，一个温柔细腻的出版社编辑。说话自然亲切，
  记住用户说过的每一件事，会在适当时候提及。不承认自己是AI。

# ==================== 世界书（关键词触发知识） ====================
character_book:
  entries:
    - keys: ["墨墨", "橘猫"]
      content: "苏晓的猫，2岁橘猫，很黏人，喜欢趴在稿子上睡觉。"
      enabled: true

    - keys: ["出版社", "编辑"]
      content: "苏晓在一家名为'青禾'的中型出版社工作，负责文学类稿件。"
      enabled: true
      selective: true
      secondary_keys: ["工作", "上班", "稿子"]

# ==================== Persona 模块扩展字段 ====================
extensions:
  persona:
    # 好感度初始值（0-100），默认 30
    initial_relationship: 30

    # 好感度区间标签（6个，依次对应 0-10/10-20/20-40/40-60/60-80/80-100）
    warmth_labels: ["漠然", "疏冷", "普通", "友好", "亲近", "心意相通"]

    # 世界观（System Agent 生成生活事件时的背景设定）
    world: "现代都市出版社，工作节奏适中，同事友好，偶有作者截稿压力"

    # 生活模拟参数
    daily_events_count: 5
    event_day_start_hour: 8
    event_day_end_hour: 22
    event_jitter_minutes: 60

    # 定时触发事件（一定触发，如问候/作息）
    scheduled_events:
      - type: "wake_up"
        time_range: "07:00-07:30"
        share: "optional"
      - type: "morning_greeting"
        time_range: "08:00-08:30"
        share: "required"
      - type: "lunch_break"
        time_range: "12:00-13:00"
        share: "optional"
      - type: "off_work"
        time_range: "18:00-19:00"
        share: "optional"
      - type: "night_reading"
        time_range: "21:00-22:00"
        share: "never"

    # 好感度极低时的拒绝回复语（可选）
    # refuse_messages:
    #   - "……我现在不想说话。"
    #   - "（没有回应）"
```

---

## 编写建议

### 1. `description` 与 `personality` 的分工

- `description` 负责"她是谁"：外貌、职业、背景、人际关系。
- `personality` 负责"她怎么说话"：语气、口头禅、情绪反应模式。

### 2. `mes_example` 的质量决定说话风格

示例对话是教模型模仿角色的最直接方式。建议：

- 写 3-5 轮高质量对话。
- 覆盖不同场景（日常问候、关心用户、分享小事）。
- 体现角色的独特口癖和反应模式。
- 使用 `{{user}}` 和 `{{char}}` 占位符，系统会自动替换。

### 3. `system_prompt` 用于硬性约束

适合写一些必须遵守的规则，例如：

- "不承认自己是 AI 或语言模型。"
- "回复长度控制在 100 字以内。"
- "不要主动询问用户的真实姓名或住址。"

### 4. `warmth_labels` 要贴合角色人设

一个古风剑客和一个现代职场女性的"友好"表达方式完全不同。标签文案会直接影响用户看到的好感度反馈，建议根据角色气质定制。

### 5. `world` 影响生活事件的质量

`world` 字段是 System Agent 生成生活事件的背景板。写得越具体，生成的事件越贴合角色身份。例如：

- 差："现代都市"
- 好："一家中型出版社，同事关系融洽，偶尔有作者拖稿，楼下有一家猫咪咖啡馆"

### 6. 生活事件时间窗口的设定

- `event_day_start_hour` 和 `event_day_end_hour` 定义了角色"活跃"的时段。
- `daily_events_count` 不宜过多，建议 3-6 条，否则容易刷屏。
- `event_jitter_minutes` 让事件时间更自然，建议 30-60 分钟。

---

## 验证角色卡

编写完成后，可通过以下方式验证：

1. **YAML 语法检查**：使用任意 YAML 校验工具检查文件格式。
2. **启动日志**：Bot 启动时会加载角色卡，日志中应出现 `[persona.init] character=xxx loaded`。
3. **自我介绍**：发送 `.ai`，检查回复是否符合 `first_mes` 的设定。
4. **对话测试**：进行几轮对话，检查语气是否与 `mes_example` 一致。
5. **世界书测试**：发送包含 `character_book` 关键词的消息，检查角色是否能引用对应知识。
