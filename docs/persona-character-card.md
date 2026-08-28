# Persona 角色卡

Persona 角色卡定义 AI 角色是谁、怎么说话、记得什么世界观、如何安排日常事件。

角色卡格式兼容 SillyTavern V2，并额外支持 `extensions.persona` 字段。

## 文件位置

每个角色一个目录：

```text
content/characters/{角色名}/
  character.yaml
  skin.yaml
```

`config/bots/{账号}.json` 中 `persona_ai.character_name` 要和目录名一致；
同时将 `persona_ai.enabled` 设为 `true` 才会启用 Persona。

例如：

```json
"persona_ai": {
  "enabled": true,
  "character_name": "mychar"
}
```

会加载：

```text
content/characters/mychar/character.yaml
```

程序包内的 `templates/characters/default/` 只是 Dashboard 未来创建角色时可使用的只读模板。Bot 只读取实例 `content/characters/`，不会自动复制或加载模板。

## 最小角色卡

```yaml
name: "苏晓"

description: |
  27岁的出版社编辑，温柔而细腻。白天处理稿件，
  晚上喜欢读书和听雨。养了一只叫墨墨的橘猫。

personality: |
  温柔体贴，偶尔迷糊。说话自然亲切，会记得用户提过的小事。

scenario: |
  现代都市，苏晓在一家中型出版社工作，生活平静但充实。

mes_example: |
  <START>
  {{user}}: 最近怎么样？
  {{char}}: 还不错，就是稿子有点多。对了，你上次说的那本书我找到了，真的很有意思。
  {{user}}: 墨墨最近乖吗？
  {{char}}: 它呀，又趴在我键盘上睡着了，赶都赶不走。

system_prompt: |
  你是苏晓，一个温柔细腻的出版社编辑。不要承认自己是 AI。
```

## 常用字段

| 字段 | 作用 |
|------|------|
| `name` | 角色名字 |
| `description` | 角色身份、外貌、背景 |
| `personality` | 性格、语气、反应方式 |
| `scenario` | 当前世界观或场景 |
| `mes_example` | 示例对话，影响说话风格 |
| `system_prompt` | 硬性规则 |
| `character_book` | 世界书，按关键词注入知识 |
| `extensions.persona` | DicePP Persona 扩展配置 |

## 写好角色的建议

### description 写“她是谁”

适合写：

- 年龄、身份、职业
- 生活环境
- 重要人际关系
- 喜欢和讨厌的东西

### personality 写“她怎么说话”

适合写：

- 语气
- 情绪反应
- 口头禅
- 面对用户时的距离感

### mes_example 最重要

示例对话是教模型模仿角色的直接材料。

建议写 3 到 5 轮，覆盖：

- 日常问候
- 关心用户
- 分享自己的生活
- 角色的独特口癖

使用 `{{user}}` 和 `{{char}}`，系统会自动替换。

### system_prompt 写硬约束

适合写：

- 不承认自己是 AI
- 回复长度限制
- 不询问真实隐私
- 不跳出角色设定

## 世界书

世界书用于关键词触发知识。

```yaml
character_book:
  entries:
    - keys: ["墨墨", "橘猫"]
      content: "墨墨是苏晓养的 2 岁橘猫，很黏人，喜欢趴在稿子上睡觉。"
      enabled: true
```

当用户提到 `墨墨` 或 `橘猫`，这段知识会更容易进入对话上下文。

## Persona 扩展

`extensions.persona` 控制生活模拟。

```yaml
extensions:
  persona:
    world: "现代都市出版社，同事关系融洽，偶尔有作者拖稿。"
    daily_events_count: 5
    event_day_start_hour: 8
    event_day_end_hour: 22
    event_jitter_minutes: 60
```

常用字段：

| 字段 | 作用 |
|------|------|
| `world` | 生成生活事件时使用的世界观 |
| `daily_events_count` | 每天生活事件数量，建议 3 到 6 |
| `event_day_start_hour` | 生活事件开始时间 |
| `event_day_end_hour` | 生活事件结束时间 |
| `event_jitter_minutes` | 事件时间随机偏移 |

## 验证角色卡

1. 检查 YAML 缩进，不要混用奇怪的制表符。
2. 重启 DicePP，或发送 `.ai admin reload`。
3. 日志中应出现 `[persona.init] character=... loaded`。
4. 私聊 `.ai` 或群聊 `@机器人 你好`。
5. 发送世界书关键词，观察角色是否引用相关设定。

## 常见问题

### YAML 报错

最常见原因是缩进错误。多行文本建议用：

```yaml
description: |
  第一行
  第二行
```

### 角色说话不像

优先改 `mes_example`，再改 `personality`。

### 生活事件太多

降低：

```yaml
daily_events_count: 3
```

生活事件数量由 `daily_events_count` 控制；如仍然过多，可继续调低该值。
