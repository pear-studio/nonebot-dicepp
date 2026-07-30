# 延后项 Backlog

记录所有需要后续 PR 处理的延后项。
对应实现 commit 自行删除条目；脚本只负责追加与排序。

每条包含：
- **优先级**：P0(阻塞)/P1(应该修)/P2(可修可不修)
- **类型**：bug / feature / refactor
- **改动量**：S(<30行单文件) / M(<300行单模块) / L(300~999行单模块) / XL(≥1000行或跨模块)，不含测试和文档行数
- **问题表现**：症状、错误日志、量化指标、复现路径
- **开发备忘**：历史背景、相关线索、可能方向（仅供参考，agent 应独立诊断，允许推翻）

---

## dev

### [B-260728-82b7c7] release 流水线产物命名不统一且存在重复构建
- 创建: 2026-07-28
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: release 全流程约 10-11 分钟, 其中 quality gate 与 release 各完整构建一遍镜像和 PyInstaller EXE(规格相同, 串行相接, 合计约 4-5 分钟重复); 产物命名三种版本格式混用: Portable/Setup/linux zip 用 tag 格式(v3.0.0rc16, workflow 重命名), nupkg 和 releases/assets feed 用 Velopack SemVer2(3.0.0-rc.16, 原样保留), dicepp-release.json 内 version 又是去 v 的 3.0.0rc16; publish 阶段 docker save ~1.2GB + zstd -19 重压缩约 1 分多钟。
- 开发备忘: 调查于 2026-07-28(基于 rc15 run 30355233453 / rc16 run 30373296372 实测)。关键位置: .github/workflows/test-suite.yml:111-113 与 release.yml:177-181 的重复 PyInstaller; release.yml:258-264 只重命名 Portable/Setup 不重命名 nupkg; 版本派生逻辑散在 generate_release_manifest.py(velopack_version/velopack_channel)与各 job 的 Extract version info。方向: gate 产物经 artifact 复用或 release 触发时跳过重复 job; 版本派生收进单一脚本统一输出; build-push-action 配 GHA 缓存; zstd 降档或多线程。优化时注意 nupkg/feed 命名是 Velopack 更新 contract 的一部分, 改名需确认 Manager 侧消费兼容。

### [B-260729-4cb6ec] Manager 配置读取与统一校验
- 创建: 2026-07-29
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
    - Manager 当前只有 PUT /v1/config/user 和 PUT /v1/config/bots/{bot_id}，没有对应 GET，Agent 无法只通过 Manager 完成配置读取与修改闭环。
    - Dashboard 在自身进程中读取和校验配置，Manager 的 PUT 主要接收任意对象后原子写入；Agent 直接调用时可能绕过 Dashboard 的完整校验。
    - 配置模型、默认值和错误信息若分别存在于 Dashboard 与 Manager，会形成两套规则并随代码演进产生漂移。
    - Manager 保存配置后不负责现有 Dashboard WebSocket 热重载，Agent 容易误以为保存已经让 Runtime 即时生效。
- 开发备忘:
    - 增加用户配置和单 Bot 配置的 GET 接口，并用类型化响应明确配置来源、保存结果和应用状态。
    - 将配置解析、默认值补全和完整校验收敛到 Manager 可复用层；Dashboard 和 Agent 均复用同一套规则，避免复制校验逻辑。
    - PUT 必须先完成校验，再使用现有原子写入机制保存；校验失败不得产生部分修改，并返回稳定的字段级错误。
    - 在 Bot 控制通道迁移前，Dashboard 保存后继续使用现有热重载；Agent 保存后如果要求立即应用，应显式调用 Runtime 重启，响应中明确 deferred/restart-required 状态。
    - 当前同一用户通常不会同时操作 Dashboard 和 Agent，暂不增加 revision、ETag、锁服务或复杂冲突合并。
    - 影响面包括 Manager 配置 API、配置模型/校验、Dashboard 配置读写客户端以及契约和回归测试；应先盘点现有 Dashboard 特有校验，避免迁移时丢失语义。

### [B-260729-817c43] Bot 控制通道由 Dashboard 迁移到 Manager
- 创建: 2026-07-29
- 优先级: P1
- 类型: refactor
- 改动量: XL
- 问题表现:
    - Bot Runtime 当前直接连接 Dashboard 的 /ws/control，Dashboard 同时负责心跳、在线状态、重载请求和结果转发。
    - Manager 为判断 Runtime 控制通道状态需要探测 Dashboard /api/health；当 Dashboard 未启动时，Manager 和同机 Agent 无法直接获得完整状态或触发热重载。
    - Dashboard 将控制心跳写入 bots_meta 并维护状态事件，导致 UI 服务拥有基础运行控制职责，不利于后台启动、Agent 运维和后续无 Dashboard 部署。
    - 相关代码横跨 Bot、Dashboard、Manager、Docker 拓扑、升级兼容和大量控制通道测试，若混入配置 API 小改会显著扩大单次改动和回归风险。
- 开发备忘:
    - 由 Manager 承载 Bot WebSocket 控制通道、心跳/在线状态、ping/pong、配置重载请求及执行结果，形成与调用方无关的控制协议。
    - Bot 控制 token 与 Manager HTTP API token 必须保持完全独立；迁移前核实 token 的创建者、存储位置、轮换和升级保留语义。
    - Bot 改为连接 Manager，调整 Compose 网络、健康门禁、启动顺序和 Manager 状态存储；Manager 健康接口直接基于所持有的控制会话报告状态。
    - Dashboard 改为通过 Manager 获取状态、订阅事件和发起热重载，切换完成后禁止 Dashboard 直接控制 Bot，并移除旧 /ws/control 和 bots_meta 中的控制通道职责。
    - 同机 Agent 通过相同 Manager HTTP 能力读取状态和触发重载，不再引入第二套 Agent 专用控制协议。
    - 制定旧版本 Bot、Dashboard、Manager 混合升级的兼容窗口和失败回退策略，避免升级过程中双方都等待对方或控制通道永久断开。
    - 补充断线重连、重复会话、心跳超时、重载成功/失败、Dashboard 状态展示、Agent 调用、容器网络及跨版本升级测试。

## persona

### [B-260601-ef9e5a] 用户自带 API Key 功能（.ai key config）
- 创建: 2026-06-01
- 优先级: P2
- 类型: feature
- 改动量: M
- 问题表现:
  当前 .ai key config 命令返回"升级中，暂不可用"，用户无法配置自己的 API Key。
  - command.py:436 硬编码了占位回复
  - errors.py:163 已提示用户使用 .ai key config 配置 API Key 可解除限制，但功能未实现
  - data/models.py 已有 primary_api_key / auxiliary_api_key 字段，但缺少命令入口和路由集成
  - 所有对话只能使用全局 provider 配置，用户无法配置自有 key 来解除限流或使用自己的额度
- 开发备忘:
  实现 .ai key config 命令，允许用户配置自己的 API Key：
  - 实现 command.py 中的 key config 子命令（设置/查看/删除）
  - 加密存储用户 API Key 到数据库（复用 data/models.py 已有字段）
  - LLM 路由中优先使用用户自有 key（若已配置），回退到全局 provider
  - 影响面：command.py、data/store.py、llm/router.py
  - 风险点：用户 key 的安全存储与传输，key 校验机制

### [B-260630-46af37] compact_conversation 改为 LLM 摘要压缩
- 创建: 2026-06-30
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - 当前 compact_conversation 仅做 clear() 清空
    - 日终前的所有对话记忆被丢弃而非提炼为叙事线索
    - 跨事件累积的叙事上下文（人物/地点/事件/线索）完全丢失
- 开发备忘:
    - 将 compact 从 clear 改为 LLM summary 压缩
    - 调用一次轻量 LLM 将 _messages 压缩为叙事摘要，保留关键信息
    - 需评估压缩 LLM 的 token 消耗和延时
    - 影响面: life/agent.py compact_conversation()

## statistics

### [B-260622-d85176] StatManager 规模化运维
- 创建: 2026-06-22
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - (a) tick_daily 逐行 get+upsert 替代 batch upsert_many，O(1)→O(N) commit，万级用户时 daily tick DB 写入次数显著增加
    - (b) StatManager._user_locks / _group_locks 字典无上限无清理，每个新 key 创建一个 asyncio.Lock 永不删除，百万级历史 ID 时内存持续增长
- 开发备忘:
    - 正确性优先于性能，R2 per-row 异常保护已减轻逐行失败影响
    - 优化方向：(1) StatManager 增加批量更新方法（update_user_stat_batch / update_group_stat_batch），单次事务内顺序获取 per-key 锁但合并 commit，需注意多锁获取顺序避免死锁
    - (2) 锁池增加 LRU 清理或 weakref 防护，需记录最后使用时间戳
    - 触发条件：实测 daily tick 耗时超阈值，或锁池 dict 大小超过 100K keys

