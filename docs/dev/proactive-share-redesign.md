# 主动分享日程重新设计

## 背景

旧的主动分享机制基于 `schedule_share(desire, threshold, delay)` 模型——每个事件计算一个 `share_desire` 数值，与 `share_threshold` 比较决定是否分享。Phase 2-4 已清理 `share_desire` 数据模型、`schedule_share` 方法、`_pending_shares` 跟踪，但 `proactive_event_share_threshold` 配置残留为死配置。

当前系统在事件链生成后没有自动分享入口：`Simulator.tick()` 拿到 `event_chain` 后不触发分享逻辑。主动消息仅剩 `miss_you`（空闲检测+概率表触发），经 `CharacterAgent.share()` 生成。

## 目标

重新设计主动分享为**独立日程驱动**机制：角色按早晚仪式+可配置中间时间点主动说话，内容由 ChatAgent 通过工具自主查询事件/日记/历史生成，不依赖事件槽位触发。

## 设计决策摘要

| 决策点 | 选择 |
|--------|------|
| 触发机制 | 独立时间日程，不和事件槽位耦合 |
| 早晚时间点 | 角色卡 `event_day_start_hour + 5min` / `event_day_end_hour - 5min` |
| 中间时间点 | 用户可配置 `["14:00", "18:30"]` + jitter |
| 执行路径 | ShareScheduler → ChatOrchestrator → ChatAgent |
| ChatAgent 注入方式 | 新增 `trigger_proactive()` 独立方法，不复用 `execute_turn` |
| 工具集 | 和 `execute_turn` 共用同一套 |
| 发送目标 | 复用已有 `proactive_always_send_users` / `proactive_always_send_groups` |
| 好感度筛选 | 先不做 |
| miss_you | 注释掉，后续改造为 ChatOrchestrator 路径 |
| 死配置清理 | 移除 `proactive_event_share_threshold` |

---

## 1. 配置变更

### 1.1 新增字段 (PersonaConfig / pydantic_models.py)

| 字段 | 类型 | 默认值 | dashboard_section | 说明 |
|------|------|--------|-------------------|------|
| `proactive_share_schedule_enabled` | `bool` | `False` | proactive | 总开关 |
| `proactive_share_schedule_morning_enabled` | `bool` | `False` | proactive | 早安问候 |
| `proactive_share_schedule_evening_enabled` | `bool` | `False` | proactive | 晚间晚安 |
| `proactive_share_schedule_times` | `list[str]` | `[]` | proactive | 中间时段，格式 `HH:MM` |
| `proactive_share_schedule_jitter_minutes` | `int` | `15` | proactive | 时间点 ±X 分钟随机偏移 |

所有字段默认关闭/空，骰主需主动配置后才生效。

### 1.2 删除字段

| 字段 | 说明 |
|------|------|
| `PersonaConfig.proactive_event_share_threshold` | 死配置，无消费点 |
| `ProactiveConfig.share_threshold` | `__init__` 参数、属性、`from_persona` 映射 |
| `LifeConfig.proactive_event_share_threshold` | dataclass 字段、`from_persona` 映射 |

---

## 2. ShareScheduler（新建）

### 2.1 职责

- 每 60s tick 一次
- 管理今天已触发的时间点集合（防重复）
- 时间匹配逻辑（当前分钟是否命中某个日程点 ± jitter 窗口）
- 跨天自动重置

### 2.2 时间点计算

```python
def _compute_schedule_times(self) -> list[tuple[str, int]]:
    """返回今天的所有日程时间点 (label, minute_of_day)"""
    times = []
    if self.config.morning_enabled:
        start_hour = self.character.extensions.event_day_start_hour
        times.append(("morning", (start_hour * 60 + 5) % 1440))
    if self.config.evening_enabled:
        end_hour = self.character.extensions.event_day_end_hour
        times.append(("evening", (end_hour * 60 - 5) % 1440))
    for t_str in self.config.schedule_times:
        h, m = map(int, t_str.split(":"))
        times.append((f"midday_{t_str}", (h * 60 + m) % 1440))
    return times
```

### 2.3 tick 流程

```
tick()
  → 检查 enabled；若 enabled=True 但无任何日程点则日志提示
  → 60s 节流
  → 跨天重置 _fired 集合
  → 活跃检查：早晚时间点使用角色卡原始 start/end hour（非 jittered 边界），
     中间时段使用 jittered 边界。避免早安在 jitter 延迟后错过窗口（见 9.7）。
  → 计算当前分钟 now_m
  → 对于每个未触发的日程点：
      if now_m 进入 jitter 窗口（含午夜包裹处理）：
        if 窗口末尾 or 随机命中：
          → _fired_times.add(label)  ← 先标记、后执行（防同一 tick 重复）
          → 从 TargetSelector 读 force 目标（白名单）
          → 按 scope 去重（同一 group:<id> 或 user:<id> 只触发一次）
          → 每个目标 → ChatOrchestrator.trigger_proactive(scope, trigger_msg, ...)
  → finally: 持久化状态（无论成功/失败都落盘）
```
### 2.3.1 活跃检查策略

早晚时间点与角色卡原始活跃小时绑定，**不使用 CharacterLife 的 jittered 边界**。中间时段使用 jittered 边界。原因：如果早安时间点（start_hour+5min）在抖动后的活跃窗口之前，用 jittered 边界会导致早安被跳过且窗口过期后不再触发。

### 2.4 Jitter 逻辑

```
center = minute_of_day  # 时间点分钟数
jitter = config.jitter_minutes
low = (center - jitter) % 1440     # 窗口下端（支持午夜包裹）
high = (center + jitter) % 1440    # 窗口上端

# 窗口内命中判断（含午夜包裹）
def in_window(now_m):
    if low <= high:
        return low <= now_m <= high
    else:
        return now_m >= low or now_m <= high  # 窗口跨午夜
```

每个 tick 进入窗口后以低概率随机触发，到达 `high` 时（窗口末尾）强制触发。Jitter 建议上限 60 分钟。

### 2.4.1 每日种子

用当日日期 + 角色名 + 时间点 label 构造 `random.Random(seed)`，确保同一天内每个时间点的随机决策可复现（防重启后行为漂移），但不在窗口内持续重复判定——只在**首次进入窗口的 tick**时用种子决定触发时刻偏移，后续 tick 比对即可。

### 2.5 白名单目标读取

复用已有 `TargetSelector.select_share_targets()`——它已返回 force（白名单）+ normal（好感度/群活跃）目标。

ShareScheduler 现阶段只用 force 目标（`policy="force"`），忽略 normal 目标。后续好感度开关可扩展。

**去重**：多个 force 条目可能指向同一个 scope（如同一 group_id 同时出现在 users 和 groups 白名单中）。触发前按 `ConversationScope` 去重，同一 scope 只调用一次 `trigger_proactive`。

### 2.6 持久化

新增 persist key `PERSONA_SK_SHARE_SCHEDULER = "persona_share_scheduler"` 到 `persist_keys.py`。

```python
payload = {
    "date": today,
    "fired_times": list(self._fired_times),  # 已触发的时间点 label 列表
}
```

存入 `PersonaDataStore.set_setting(PERSONA_SK_SHARE_SCHEDULER, ...)`。

沿用 ProactiveScheduler 的脏数据检查模式（比较 blob 避免无变更写 DB）。在 `finally` 块中持久化，确保即使 trigger_proactive 抛异常也落盘。

### 2.7 Simulator 集成

```python
class LifeSimulator:
    def __init__(self, ..., share_scheduler: Optional[ShareScheduler] = None,
                 chat_orchestrator=None):
        self.share_scheduler = share_scheduler
        # chat_orchestrator 转交给 ShareScheduler
        if share_scheduler and chat_orchestrator:
            share_scheduler.set_trigger_callback(chat_orchestrator.trigger_proactive)

    async def tick(self):
        # 角色生活事件生成（不变）
        if self.character_life:
            event_chain = await self.character_life.tick()
        # 分享日程（注意：先 event 后 share，share 需要今天的 daily_events）
        if self.share_scheduler:
            await self.share_scheduler.tick()
        # miss_you（暂时注释掉）
        # if self.scheduler:
        #     proactive_msgs = await self.scheduler.tick()
```

**ChatOrchestrator 注入**：ShareScheduler 通过回调函数持有 ChatOrchestrator 引用（而非直接依赖完整对象）。Factory 在构建 LifeSimulator 时传入 `chat_orchestrator`，Simulator 调用 `share_scheduler.set_trigger_callback(orchestrator.trigger_proactive)`。这保持了窄接口（ShareScheduler 只需要 `trigger_proactive` 方法，不需要完整的 ChatOrchestrator）。

**执行顺序**：`character_life.tick()` 在 `share_scheduler.tick()` 之前，确保早安触发时当天可能有事件已生成（wake_up 槽位先触发），也确保事件数据对 ChatAgent 工具可用。晚间分享使用同一顺序：good_night 槽位和晚间分享在同一 tick 窗口内先后触发，但 share 不使用 jittered 边界做活跃检查（见 2.3.1），因此不受 good_night 槽位影响活跃状态。

### 2.8 BoundaryReceiver

ShareScheduler 实现 `BoundaryReceiver` 接口（`set_jittered_boundaries`），与 ProactiveScheduler 并列接收 CharacterLife 同步的活跃边界。Factory 中同时注册两个 receiver：

```python
character_life.set_boundary_receiver(scheduler)       # 旧 ProactiveScheduler
character_life.set_boundary_receiver(share_scheduler)  # 新 ShareScheduler
```

这要求 CharacterLife 支持多个 receiver（改为 list），或者 ShareScheduler 直接读取 CharacterLife 的公开 jitter 属性。

---

## 3. ChatOrchestrator.trigger_proactive()

### 3.1 签名

```python
async def trigger_proactive(
    self,
    scope: ConversationScope,
    trigger_message: str,
    message_type: MessageType = MessageType.PROACTIVE,
    user_id: str = "",
    group_id: str = "",
) -> ChatOutcome:
```

### 3.2 流程

```
trigger_proactive(scope, trigger_message, user_id, group_id)
  → 门控：跳过 sleep gate / 信誉检查 / 配额检查（非用户触发）
  → target_key = f"group:{group_id}" if group_id else f"user:{user_id}"
  → coordinator.submit(target_key, None, proactive_call_fn)  ← 串行化
    → for attempt in range(2):  ← Stage B 轮换重试
      → _registry.run_guard(scope)
      → _ensure_conversation(scope)
      → _ensure_agent(scope, conv)
      → agent.trigger_proactive(trigger_message, user_id, group_id, message_type)
      → if result.reason == "rotation_needed":
          → _registry.rotate(scope)
          → _agents.pop(scope, None)
          → continue  # 重试
      → return result
  → 返回 ChatOutcome（不调 after_response 评分）
```

### 3.2.1 Stage B 轮换重试

与 `chat()` 一致：token 预算耗尽导致 `rotation_needed` 时，close + rotate Conversation，清除缓存的 ChatAgent，重试一次。主动消息不应因为 token 轮换而无声丢失。

### 3.3 与 chat() 的差异

| | chat() | trigger_proactive() |
|---|---|---|
| 触发源 | 用户消息 | 分享日程 |
| sleep gate | 检查 | 跳过 |
| 信誉门控 | 检查 | 跳过 |
| 配额 | 扣用户配额 | 不扣 |
| coordinator 串行化 | submit(target_key, ...) | 同样走 coordinator |
| after_response | 触发评分 | 跳过 |
| image_data_urls | 支持 | 不支持 |
| transient_message | 支持 | 用于注入 trigger |

---

## 4. ChatAgent.trigger_proactive()

### 4.1 签名

```python
async def trigger_proactive(
    self,
    trigger_message: str,
    user_id: str = "",
    group_id: str = "",
    message_type: MessageType = MessageType.PROACTIVE,
) -> ChatOutcome:
```

### 4.2 与 execute_turn 共享的部分

- DeliveryQueue 构建
- ToolKit 构建（完全相同的工具集，user_id/group_id 参数不同）
- OutputSpec (send_reply)
- `conv.run()` 调用
- delivery.drain + append_ref
- ChatOutcome 构造
- rotation_needed 透传

### 4.3 差异点

| | execute_turn | trigger_proactive |
|---|---|---|
| `user_input` | 用户消息文本 | `""` (空) |
| `system_prompt` | `context_builder.build_static_prompt()` | `context_builder.build_static_prompt_proactive()` |
| transient | 说话者状态 + 图片 + transient_message | 仅 trigger_message 作为系统消息注入（跳过 `_group_speaker_status`） |
| `record_user_input` | False | 不录制 |
| R2 兜底 (补录 user_input) | 有 | **跳过**（user_input="" 时显式跳过整个 R2 回退块） |
| 配额 | `check_daily_quota` + `increment_usage` | **跳过**（整个 `_router_has_quota` 块不执行） |
| `run_tag` | `"chat"` | `"proactive"` |
| `agent_name` | `"Chat"` | `"Chat"` |

### 4.3.1 可提取的共享逻辑

Delivery drain + append_ref + ChatOutcome 构造在 `execute_turn` 和 `trigger_proactive` 中完全一致。建议提取为 `_finalize_turn()` 私有方法，避免两处复制 40 行相同代码。

### 4.4 trigger_message 作为 transient 注入

```python
transient_list = [{
    "role": "user",
    "name": "系统",
    "content": trigger_message  # 如 "（天亮了，跟大家说早安。）"
}]
```

### 4.5 system_prompt 变体

在现有 `context_builder.build_static_prompt()` 基础上新增一个 proactive 变体。区别：

- 改 "你正在跟人聊天，收到了一条消息" → "你想主动跟大家说点什么"
- 不强调"回复"，强调"主动发言"
- 角色人格、工具使用方式不变

具体 prompt 调整：

```diff
- 你正在和{user_name}聊天，收到了一条消息：{user_input}
- 你需要回复这条消息
+ 你想主动跟大家说点什么
```

---

## 5. trigger 提示内容

| 时间类型 | 群聊 | 私聊 |
|---------|------|------|
| 早安 | `"（天亮了，跟大家说早安。）"` | `"（天亮了，跟{target_name}说早安。）"` |
| 中间时段 | `"（和大家聊聊吧。）"` | `"（和{target_name}聊聊吧。）"` |
| 晚安 | `"（夜深了，跟大家说晚安。）"` | `"（夜深了，跟{target_name}说晚安。）"` |

`{target_name}` 私聊时填入目标用户昵称（从 UserProfile 或关系表获取）。

---

## 6. ProactiveScheduler 注释范围

以下方法全部注释，保留类定义但 `tick()` 返回 `[]`：

- `_check_missed_users`
- `_create_miss_you_message`
- `_build_and_generate_share_message`
- `share_event_to_targets`
- `_format_user_profile_facts`
- `_format_recent_history`
- `_sanitize_prompt_text` (如仅被上述方法使用)

`_MISS_PROBABILITY`、miss 相关配置（`miss_enabled`、`miss_min_hours`、`miss_min_score`）保留不删，后续 miss_you 改造时复用。

---

## 7. 测试清理

### 7.1 删除残留引用

| 文件 | 改动 |
|------|------|
| `tests/.../test_models.py:186` | 从字段列表移除 `proactive_event_share_threshold` |
| `tests/.../test_life_simulator.py` | 移除 `share_threshold` 参数，移除 `schedule_share` mock |
| `tests/.../test_send_msg_reflow.py` | 同上 |
| `tests/.../test_scheduler.py` | 移除 `share_threshold` 参数引用 |

### 7.2 保留不删

- `tests/.../test_scheduler.py` 中 `TestScheduleShare` 类保留（已 `@pytest.mark.skip`）
- `test_data_store.py:724` 的 docstring 提到 `share_desire` 但实际测的是 `context_summary` 和 `duration_minutes`——docstring 修正即可

---

## 8. 数据流全景

```
┌─────────────────────────────────────────────────────────────┐
│ Simulator.tick()                                             │
│                                                              │
│  character_life.tick()          share_scheduler.tick()       │
│  → DM 生成事件                  → 时间点命中？               │
│  → Character 反应               → TargetSelector.get_targets │
│  → 存入 daily_events            → for each target:           │
│       (不触发分享)              ┌────────────────────────┐   │
│                                 │ ChatOrchestrator        │   │
│                                 │  .trigger_proactive()   │   │
│                                 │                         │   │
│                                 │  → ChatAgent            │   │
│                                 │    .trigger_proactive() │   │
│                                 │    → transient: trigger │   │
│                                 │    → conv.run()         │   │
│                                 │    → read_events 工具    │   │
│                                 │    → send_reply         │   │
│                                 │    → delivery.drain     │   │
│                                 │    → port.send          │   │
│                                 └────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. 边界情况与风险

### 9.1 跨天边界

- 角色 `event_day_end_hour ≥ 24`（活跃窗口跨午夜）：晚安时间点按 `% 1440` 计算，放在凌晨
- ShareScheduler `_fired_times` 在新一天的第一次 tick 被清空

### 9.2 凌晨无事件

- 早安触发时，角色可能刚起床，今天还没有事件。agent 可以通过 `read_events` 查昨天的事、`read_diary` 查昨天的日记
- 如果昨天也没有事件/日记，agent 至少可以说早安——不需要素材

### 9.3 白名单为空

- `proactive_always_send_users` 和 `proactive_always_send_groups` 都为空时，`select_share_targets()` 只返回 normal 目标
- ShareScheduler 只取 force 目标，此时目标列表为空，跳过该时间点，记录 debug 日志

### 9.4 ChatOrchestrator coordinator 串行化

- `trigger_proactive` 需要与 `chat()` 走同一 `coordinator.submit()` ——同一 scope 的主动分享和用户聊天不应并发
- coordinator key: 群聊用 `group:<id>`，私聊用 `user:<id>`

### 9.5 Delivery 失败

- 和 `execute_turn` 一致：最好努力发送，记录 warning，不抛异常
- 即使发送失败，该时间点也标记为已触发（避免无限重试）

### 9.6 早安与 Jittered 活跃边界

CharacterLife 给活跃边界加随机抖动后，早安时间点（`start_hour+5min`）可能落在 jittered 窗口之前。此时若用 jittered 边界检查 `_is_character_active()`，早安会被跳过且窗口过了就不再触发。解决方法：早晚时间点的活跃检查使用角色卡**原始** `start_hour`/`end_hour`，中间时段使用 jittered 边界（详见 2.3.1）。

### 9.7 角色卡缺失活跃小时

若角色卡未配置 `event_day_start_hour` / `event_day_end_hour`（或为 None/0），早安/晚安无法计算时间点。ShareScheduler 应检测并跳过早晚问候，记录 warning。

### 9.8 空配置检测

当 `proactive_share_schedule_enabled=True` 但 `morning_enabled`、`evening_enabled`、`schedule_times` 全部关闭/为空时，ShareScheduler 不做任何事。首次 tick 时记录一条 info 日志提醒骰主。

### 9.9 配置变更后 fired_times 失效

`_fired_times` 使用时间点 label（如 `"midday_14:00"`）作为 key。若骰主修改 `schedule_times`，旧 label 不再匹配新计算的时间点，旧时间点视为未触发。这通常符合预期（改了配置就是想要不同行为），但需在文档备注。

### 9.10 多实例

当前设计仅保证单实例正确性（持久化 + 内存 `_fired_times`）。多实例部署需额外机制（DB 级排他锁或唯一约束），不在本次范围内。

---

## 10. 实施顺序

1. **配置变更**：`pydantic_models.py` 新增 5 字段 + 删除 `proactive_event_share_threshold`
2. **配置清理**：`proactive_config.py` 删 `share_threshold`、`simulator.py` 删 `LifeConfig.proactive_event_share_threshold`
3. **persist key**：`persist_keys.py` 新增 `PERSONA_SK_SHARE_SCHEDULER`
4. **注释 miss_you**：`proactive_scheduler.py` tick 返回 `[]`，注释相关方法
5. **ChatAgent.trigger_proactive()**：`chat_agent.py` 新增方法 + 提取 `_finalize_turn()`
6. **context builder**：`context.py` 新增 `build_static_prompt_proactive()`
7. **ChatOrchestrator.trigger_proactive()**：`orchestrator.py` 新增方法（含 Stage B 重试 + coordinator）
8. **CharacterLife 多 receiver**：支持多个 BoundaryReceiver（或 ShareScheduler 直接读公开属性）
9. **ShareScheduler**：新建 `share_scheduler.py`
10. **Simulator + Factory 接入**：注入 chat_orchestrator 回调 + 注册 boundary receiver
11. **测试清理**：移除死配置/旧方法引用
12. **运行测试验证**
