## 本地改动 Review

**改动范围**: 9 个已修改文件 + 5 个新文件，+641 行, -29 行

**改动主题**: Persona AI Phase 2 — 好感度时间衰减、角色生活模拟、主动消息调度、群聊观察、群活跃度追踪

---

### 实现质量

#### 严重

1. **`self.config` 未初始化 — AttributeError 必定触发** (`command.py`)

   `delay_init()` 中 `config` 是局部变量（`config = self.bot.config.persona_ai`），从未赋值给 `self.config`。但新增代码在多处访问 `self.config.observe_group_enabled`、`self.config.group_activity_enabled` 等。这会导致群聊旁听模式和群活跃度更新直接崩溃。父类 `UserCommandBase` 也不提供 `self.config`。

   修复：在 `delay_init()` 中添加 `self.config = self.bot.config.persona_ai`。

2. **群活跃度衰减硬编码，不与配置联动** (`store.py:543`)

   `decay = days_since * 10` 写死了衰减值。`PersonaConfig` 定义了 `group_activity_decay_per_day: float = 10.0`，但 `store.py` 的查询衰减和 `get_all_group_activities()` 都没有引用配置值。如果修改配置，行为不会改变。

3. **`today_added` 估算逻辑无效** (`store.py:578-582`)

   `today_added = score_delta` 直接设成本次增量值，不追踪当日累计。这导致 `max_daily_add=20` 的限制形同虚设 — 每次调用都只检查"本次增量是否超过上限"，而不是"当天累计是否超过上限"。

#### 警告

4. **`group_activity_floor_whitelist` 定义但未使用** (`pydantic_models.py:85`)

   配置中存在 `group_activity_floor_whitelist: float = 50.0`，但 `store.py:590` 硬编码 `activity.score 。

   修复：要么删除该配置字段，要么在 store.py 中使用它。

5. **LSP 类型错误 — 新文件存在多处类型不匹配**

   - `character_life.py:24`: `event_hours: List[int] = None` 应为 `event_hours: List[int] | None = None` 或 `event_hours: Optional[List[int]] = None`
   - `character_life.py:115`: `self.character.extensions.scenario` 属性不存在于 `PersonaExtensions` 类型定义中
   - `scheduler.py:69`: `shared_with: Set[str] = None` 应为 `shared_with: Optional[Set[str]] = None`
   - `event_agent.py:97,148,215`: `temperature` 不是 `LLMRouter.generate()` 的有效参数
   - `observation_buffer.py:286`: 同上，`temperature` 参数无效

   这些类型错误在运行时可能不会立即导致崩溃（Python 不强制类型检查），但说明代码未经类型检查验证。`temperature` 参数如果 LLMRouter 不接受，会在运行时抛出 TypeError。

6. **`tick()` 同步/异步混合 — fire-and-forget 导致消息丢失** (`command.py:896-939`)

   当事件循环已在运行时（正常部署场景），`asyncio.create_task(self.orchestrator.tick())` 不等待结果直接返回空列表。意味着 tick 生成的主动消息会丢失。开发者已在 TODO 注释中承认此问题，但目前没有修复方案。

#### 建议

7. **`get_top_relationships()` 跨群查询使用 `group_id = ''` 过滤** (`store.py:490`)

   调用时使用默认 `group_id=""`，SQL 是 `WHERE group_id = ''`。如果私聊用户的 `group_id` 存储为 `NULL` 而非 `""`，这些用户会被遗漏。建议检查数据库实际存储方式。

8. **`asyncio.Lock()` 在 `__init__` 中创建** (`scheduler.py:102`)

   `self._share_lock = asyncio.Lock()` 在同步上下文中创建。如果 NoneBot 的事件循环尚未运行，锁可能绑定到错误的 loop。实际在 NoneBot 环境下大概率能工作，但属于脆弱设计。

9. **零测试覆盖**

   5 个新增模块（decay.py, character_life.py, observation_buffer.py, scheduler.py, event_agent.py）没有任何对应测试。新增的 store.py 方法也无测试。建议至少为衰减计算、群活跃度更新、调度器节流逻辑编写单元测试。

---

### 设计质量

#### 严重

10. **配置表示三分裂**

    DecayConfig 在三个地方存在：
    - `pydantic_models.py` — 扁平字段（启用/禁用、速率、上限、下限）
    - `models.py` — 独立 Pydantic 模型（与配置不连通）
    - `game/decay.py` — 普通 Python 类

    orchestrator 初始化时手动从 pydantic 配置复制参数到 DecayConfig。每新增一个参数需要改三处。这不是"先跑起来"的问题，这是架构性重复。

#### 警告

11. **内存状态不持久化，重启丢失**

    - `CharacterLife._generated_hours`（已生成事件的小时集合）— 重启后可能重复生成同一时段事件
    - `ProactiveScheduler._pending_shares`（待分享事件队列）— 重启后清空，事件分享中断
    - `ProactiveScheduler._scheduled_events_today`（今日已触发事件）— 重启后可能重复触发早安/晚安等
    - `PersonaCommand._observation_buffers`（群聊观察缓冲）— 重启后已缓冲消息丢失

    对于骰子机器人来说，重启不算罕见（部署、升级、崩溃恢复）。上述状态丢失可能导致：重复问候打扰用户、事件分享突然中断、观察数据消失。

12. **衰减在每次对话时即时应用** (`orchestrator.py:_check_and_apply_decay`)

    每次用户与 AI 对话都触发数据库查询 + 计算 + 写回。对活跃用户群来说造成不必要的数据库压力。业界常见做法是查询时惰性计算（不写库）或每日定时批量更新。

13. **问候时间表写死在代码中** (`scheduler.py:183-189`)

    `("wake_up", "07:00-08:00", "早上好！")` 等 5 个时段硬编码在方法体内。无法通过配置调整，不同时区用户需求无法满足。

14. **ObserverationBuffer 动态阈值逻辑缺乏文档**

    `_adjust_threshold()` 在快速触发时 +10、慢速触发时 -5。30 分钟/3 小时的阈值是硬编码的，不了解算法的人难以调参。群聊爆发时阈值可增长至 60，可能导致观察永远无法提取。

15. **ProactiveConfig.share_time_window_minutes 定义但未使用** (`scheduler.py:28`)

    定义了字段但没有任何代码引用。属于死代码。

---

### 综合评估

整体改动是一个功能丰富的 Phase 2 实现，模块职责划分清晰（decay、character_life、scheduler、observation 各司其职），代码结构合理。但存在一个**阻碍功能运行的严重 Bug**（`self.config` 未初始化），以及多处配置与行为脱节的问题（硬编码衰减、未使用配置字段）。内存状态不持久化会在重启后产生用户体验问题。

**建议**：修复 `self.config` 初始化后可先试运行验证核心功能，但合并 main 前应解决硬编码衰减和 daily_add 估算问题，否则配置与行为不一致会成为持续的技术债。内存状态持久化可作为 Phase 2.1 跟进。
