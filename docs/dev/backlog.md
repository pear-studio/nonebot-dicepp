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

## core/bot

### [B-260804-2929ef] 移除 bot 内嵌内存监控与自重启机制
- 创建: 2026-08-04
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现:
    - MemoryMonitorConfig（core/config/pydantic_models.py:596）功能为死代码：生产 memory_monitor.enable=false
    - 阈值语义损坏：percent = bot自身RSS / 系统总内存，3.6GB 小机上 90% = 3.3GB 未触发机器已先冻死；警告档 master 通知代码被注释（dicebot.py:377），预警名存实亡
    - .m reboot 自重启（core/bot/dicebot.py:523 reboot_async，os.exec 原地替换）对 Manager 完全不可见：无 audit、无状态上报，与 Manager 统一管理 bot 生命周期方向相悖
    - Dashboard 已提供完整重启链路（UI 按钮 + Manager docker/process adapter restart，含 Windows 托盘），.m reboot/.m memory 价值已被覆盖
- 开发备忘:
    - 删除：MemoryMonitorConfig 模型及 dashboard 元数据、_check_memory_and_handle 及 tick 挂载（dicebot.py:316,351）、get_memory_status()、.m memory/.m mem 指令（module/common/master_command.py:206）、.m reboot 全套（立即/延迟/rebooter 汇报，master_command.py:71-121 及 dicebot.py:660-671 启动回报分支）、reboot()/reboot_async()、config/common.py:27-30 相关常量
    - 需验证：旧 config/global.json 含 memory_monitor 键，删除模型后 pydantic 对未知字段的兼容行为（extra 策略）
    - 同步清理相关测试与文档（docs/ 中 .m reboot/.m memory 说明）
    - 自愈需求后续由 Manager watchdog 承接（见性能监控展示条目），不在 bot 内重建
    - 来源：.temp/prod-handoff/20260804-memory-monitor-enhancement.md

## dashboard

### [B-260731-cf6a1d] 设计 Dashboard 查询资料与群私设管理
- 创建: 2026-07-31
- 优先级: P2
- 类型: feature
- 改动量: XL
- 问题表现:
    - 查询库写入管理仍依赖手工文件或本机工具；旧群私设命令与 XLSX 链路已停止支持，未来如需群私设需按新模型重新设计
    - 当前架构文档只允许 Dashboard 对 Persona 角色卡进行类型受限写入，尚未定义查询库写入、安全事务和 Bot 在线刷新规则
    - 未来查询资料格式可能调整，并可能将查询、随机库、牌库等资料统一到同一文件；具体格式与迁移规则尚未确定
- 开发备忘:
    - 在新的统一资料格式及产品需求明确后，再设计普通资料管理和群私设管理
    - 未来统一格式与简易查询格式采用独立界面和双轨设计，不把当前表名或列名视为长期写入契约
    - 不要求新管理能力兼容当前 XLSX、群私设指令、`HomebrewCommand` 或现有群私设管理流程；需要时按新模型重新设计和迁移
    - 设计资料创建、编辑、删除、导入及群私设关联等页面流程，批量操作需预览和确认
    - 只提供类型受限的查询库操作，不提供任意 SQL、任意表写入或通用 `content/` 写入
    - 所有写操作需要登录鉴权、输入校验、事务保护和审计记录
    - 明确 Dashboard 提交后 Bot 如何看到变更，以及新建、替换、删除数据库时的连接刷新规则
    - 明确完整归档与 Dashboard 并发写入时的一致性策略
    - 按最终写入归属更新 Manager 架构文档

## dice_hub

### [B-260731-93a733] 重新设计并实现 DiceHub 命令
- 创建: 2026-07-31
- 优先级: P2
- 类型: feature
- 改动量: XL
- 问题表现:
    - 旧 DiceHub 命令被禁用后，用户将暂时无法通过机器人完成注册、查看节点、心跳或连接配置
    - 旧实现把命令解析、远端调用、配置持久化和回复生成混在一起，且现有测试没有覆盖完整命令调用链
    - 当前尚未确定重新实现时需要保留的 DiceHub 使用场景和远端契约
- 开发备忘:
    - 实现前重新确认 DiceHub 的实际用途、远端协议、认证方式、隐私要求和失败语义，不默认兼容旧命令行为
    - 使用现有异步调用链完成远端操作，不恢复 `run_async`
    - 集中命令执行、错误转换和用户回复，避免命令 Adapter 了解远端调用细节
    - 明确旧 DiceHub 配置和数据的保留、迁移或废弃策略
    - 使用本地 Fake Adapter 覆盖完整命令行为；真实外部 DiceHub 验收需要另行确认

## dicepp_manager

### [B-260804-63f4b2] 性能监控展示（Manager 采集 + Dashboard 实时值与短窗口趋势）
- 创建: 2026-08-04
- 优先级: P1
- 类型: feature
- 改动量: XL
- 问题表现:
    - Dashboard 目前无任何内存/CPU/进程级资源指标，Overview 仅展示 bot online/version/last_heartbeat
    - bot 侧 get_memory_status()（core/bot/dicebot.py:380）采集的数据不出进程：不落库、不上报，仅 .m memory 指令可见
    - 2026-08-04 生产 3.6GB 小机内存耗尽整机冻死，失控源在 bot 之外（napcat/宿主机进程），bot 内监控完全无感知
    - 生产已补 earlyoom + atop 兜底，但不能假设其他部署设备（Windows / Windows Server / 其他 Linux）也有，需产品级跨平台性能展示
- 开发备忘:
    - Manager 侧采集：按 DICEPP_MANAGER_RUNTIME 分派——process（Windows/原生）用 psutil 采宿主机+子进程；docker 用 docker.sock Engine API（/info + /containers/{id}/stats），compose 模板给 manager 加 /proc:ro 挂载以读真实整机内存，无 /proc 时降级到 docker /info 视角
    - 放宽容器枚举标签过滤（当前仅 io.dicepp.* 标签容器，docker_runtime.py:242），纳入 napcat
    - Manager 内存环形缓冲保留 1~2 小时采样（10~30s 间隔），不落库、无保留策略
    - 数据链路：扩展现有控制通道/Manager API（/v1/control/bots 聚合点），RuntimeUnitStatus 加指标字段；Dashboard 轮询 Manager 展示实时值卡片 + 迷你趋势曲线
    - 注意 docker stats 为流式接口，需低开销采样方式；compose 变更影响部署合同测试 tests/integration/manager/test_deployment_contract.py
    - 后续方向（不在本期）：Manager watchdog 自愈（RSS 超阈值→restart runtime unit，替代已删除的 bot 自重启）、落库时序长周期曲线、cgroup limit 百分比阈值
    - 来源：.temp/prod-handoff/20260804-memory-monitor-enhancement.md

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
