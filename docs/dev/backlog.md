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

### [B-260730-67a90e] Windows 并行系统测试服务启动偶发超时
- 创建: 2026-07-30
- 优先级: P2
- 类型: bug
- 改动量: M
- 问题表现: Windows 上 uv run pytest 的 xdist 完整回归连续两次在不同系统服务启动用例失败：Dashboard/Manager 本地 HTTP 服务 15 秒内未就绪；相同用例以 -n0 串行重跑均通过。失败位置漂移，未见 TokenSecurityError 或固定端口占用，说明当前并行启动存在时序或资源竞争。
- 开发备忘: 先建立低成本的重复并行反馈环并捕获子进程 stderr、端口和资源使用；确认是否需要为共享浏览器、Manager 或端口资源标记串行执行，或调整启动就绪等待。影响面：pytest xdist 配置、tests/system/browser、tests/system/process 及 dashboard/manager 测试辅助。不要通过无界延长全局 timeout 掩盖问题。

### [B-260728-82b7c7] release 流水线产物命名不统一且存在重复构建
- 创建: 2026-07-28
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: release 全流程约 10-11 分钟, 其中 quality gate 与 release 各完整构建一遍镜像和 PyInstaller EXE(规格相同, 串行相接, 合计约 4-5 分钟重复); 产物命名三种版本格式混用: Portable/Setup/linux zip 用 tag 格式(v3.0.0rc16, workflow 重命名), nupkg 和 releases/assets feed 用 Velopack SemVer2(3.0.0-rc.16, 原样保留), dicepp-release.json 内 version 又是去 v 的 3.0.0rc16; publish 阶段 docker save ~1.2GB + zstd -19 重压缩约 1 分多钟。
- 开发备忘: 调查于 2026-07-28(基于 rc15 run 30355233453 / rc16 run 30373296372 实测)。关键位置: .github/workflows/test-suite.yml:111-113 与 release.yml:177-181 的重复 PyInstaller; release.yml:258-264 只重命名 Portable/Setup 不重命名 nupkg; 版本派生逻辑散在 generate_release_manifest.py(velopack_version/velopack_channel)与各 job 的 Extract version info。方向: gate 产物经 artifact 复用或 release 触发时跳过重复 job; 版本派生收进单一脚本统一输出; build-push-action 配 GHA 缓存; zstd 降档或多线程。优化时注意 nupkg/feed 命名是 Velopack 更新 contract 的一部分, 改名需确认 Manager 侧消费兼容。

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

