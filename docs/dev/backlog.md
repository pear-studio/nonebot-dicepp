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

### [B-260731-a4e8ea] 硬切 Velopack 单 bundle Windows 更新契约
- 创建: 2026-07-31
- 优先级: P2
- 类型: refactor
- 改动量: XL
- 问题表现:
  - rc16 GitHub Release 的 8 个 assets 中，full nupkg、`releases.win-x64-prerelease.json`、`assets.win-x64-prerelease.json` 是 Manager/Velopack 机器组件，却与三个用户发行包并列展示，容易让普通用户误以为需要手动下载
  - rc16 `assets.win-x64-prerelease.json` 仍引用 Velopack 原始的 `DicePP-win-x64-prerelease-Setup.exe` / `Portable.zip`，但 Release 实际上传的是带 `v3.0.0rc16` 的重命名文件，feed 与公开 assets 不自洽
  - 当前 Manager 自行通过 `dicepp-release.json` 发现版本，并调用 `Update.exe apply -p <full.nupkg>` 安装；Velopack 官方说明 `assets.json` 仅供部署命令使用可删除，`releases.json` 用于 `UpdateManager` 发现版本，这两项都不是 DicePP 当前安装路径的必要输入
  - v3 尚未正式发布，无需为现有 RC 的旧三文件更新契约保留自动升级兼容；旧 RC 到硬切版本允许要求手动安装
- 开发备忘:
  - 直接升级 Windows release contract，使用单一机器资产 `velopack.win-x64.zip`；文件名不带 `DicePP-vX.Y.Z` 用户发行包前缀，也不重复写 version/channel
  - bundle 仅包含 `manifest.json` 与 Velopack full nupkg；内层 manifest 严格声明格式版本、DicePP/Velopack 版本、channel、平台、架构、nupkg 文件名、size 和 SHA-256，外层 `dicepp-release.json` 再校验整个 bundle
  - Manager 改为下载、安全解压和校验 bundle，再把经过版本/摘要验证的 nupkg 交给 UpdateGuard/`Update.exe apply -p`；回滚包获取和本地 packages 维护同步改用 bundle 契约
  - 删除旧的独立 full nupkg、`releases.json`、`assets.json` 发布、下载及校验路径，不实现双格式读取；使用新的 release contract version 让旧 Manager 明确拒绝而不是误解析
  - 安全验收覆盖路径穿越、绝对路径、符号链接/重解析点、重复成员、额外成员、压缩炸弹边界、nupkg 版本/摘要冲突、下载中断、升级失败回滚；发布验收覆盖硬切后相邻版本自动升级和回滚
  - 最终 GitHub Release assets 固定为三个 `DicePP-vX.Y.Z-*` 用户发行包、`velopack.win-x64.zip`、`dicepp-release.json`、`docker-compose.yml`
  - 关键位置: `.github/workflows/release.yml`、`scripts/build/generate_release_manifest.py`、`src/dicepp_manager/release.py`、`src/dicepp_manager/upgrade.py`、相关 release/upgrade tests 与发布文档

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

