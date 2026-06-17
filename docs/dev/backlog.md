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

## config

### [B-260616-afe771] 发布前提供网页配置管理入口
- 创建: 2026-06-16
- 优先级: P1
- 类型: feature
- 改动量: L
- 问题表现:
  - 当前部署文档已经不再鼓励小白直接编辑脚本或运行脚本，但配置仍主要依赖手写 JSON。
  - 用户需要编辑账号配置、master/admin、好友口令、Persona AI、API Key、角色选择等内容，直接改 JSON 容易写错格式、泄露密钥或改错文件。
  - Windows 未来会走 exe 部署，Linux 走 release + docker compose，小白不应再接触 scripts 或源码目录；缺少网页配置管理会让正式发布流程不闭环。
- 开发备忘:
  - 在正式发布前提供网页配置管理入口，覆盖账号配置、全局配置、secrets/API Key、Persona 开关、角色选择、基础校验和保存。
  - 配置保存需要区分可提交配置与本地敏感配置，避免把 API Key 写到错误位置。
  - 需要有 JSON/schema 校验、错误提示、重启/热重载提示，以及备份或回滚策略。
  - Windows exe 和 Linux Docker 都应能使用同一套配置管理体验；注意配置文件路径和 volume 挂载差异。
  - 可先做最小可用版本：读取当前配置、编辑常用字段、保存并提示重启；高级 Persona 参数后续再扩展。

## deploy

### [B-260615-19b0fa] 生产备份与恢复策略
- 创建: 2026-06-15
- 优先级: P1
- 类型: feature
- 改动量: M
- 问题表现:
    - 当前版本发布/回退流程即将切到镜像 tag 部署，但缺少对应的生产备份与恢复机制。
    - 镜像回退无法恢复已经变更的数据库、config、data、content 或运行时状态，容易让“可回退”产生误导。
    - 生产更新前、定时备份、恢复验证、保留策略和敏感数据处理规则尚未固化。
- 开发备忘:
    - 梳理需要备份的范围：config/、data/、content/、数据库文件、LLOneBot 相关持久化数据，以及生产环境中额外存在的本地环境变量文件。
    - 设计升级前备份、定时备份、恢复演练、保留周期和失败告警。
    - 后续可与 version-deploy / deploy-docker 联动：当 release metadata 标记 数据变更/配置变更 为 yes 时，生产更新前必须确认备份。
    - 注意恢复流程不能只写文档，至少需要可验证的恢复步骤或脚本入口。

## persona

### [B-260602-4263c4] user_stat/group_stat 的 read-modify-write 写竞争
- 创建: 2026-06-02
- 优先级: P1
- 类型: bug
- 改动量: M
- 问题表现: 多路径对 user_stat/group_stat 做全量覆写，tick_daily 的 daily_update() 可能被 stale 数据覆盖，update_group_info_all 修改 meta 字段同理。涉及路径：process_message（dicebot.py:577-585,735）、tick_daily（line 282-310）、update_group_info_all（line 856-866）、record_roll_stat（roll_dice_command.py:527-556）。meta_stat 同类竞争已修复（单一写者），这些路径仍有窗口。
- 开发备忘: 修复方向同 meta_stat 单写者模式或 Repository 原子更新：将 user_stat/group_stat 的读-改-写收敛到单一路径，或用原子 upsert 替代全量覆写。需先梳理各写路径的字段修改交集。

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

## release

### [B-260615-90ee20] GitHub Release 与多产物发布流程
- 创建: 2026-06-15
- 优先级: P2
- 类型: feature
- 改动量: L
- 问题表现:
    - 已决定 docs/releases/vX.Y.Z.md 作为 release metadata 源头，但未来 GitHub Release body、发布附件、镜像、可能的 Windows exe 产物如何统一发布尚未设计。
    - 当前第一阶段只计划 GHCR Docker 镜像，尚未覆盖桌面/Windows exe、checksums、构建矩阵、手动/自动发布边界等常见发布产物问题。
- 开发备忘:
    - 调研并设计后续 release 流程：以 docs/releases/vX.Y.Z.md 生成或同步 GitHub Release body。
    - 评估是否在 GitHub Release 附加构建产物，如 Windows exe、压缩包、checksums、SBOM 或签名文件。
    - 保持第一版实现克制：先不承诺具体 exe 技术路线，未来可比较 PyInstaller、zipapp、独立 Python runtime、Docker-only 等方案。
    - 需要决定哪些产物由 CI 自动生成，哪些必须人工确认后发布；避免 GitHub Actions 自动部署生产。

### [B-260617-1cc4a4] 改进 PyInstaller 打包结构以减少 hiddenimports 补丁
- 创建: 2026-06-17
- 优先级: P2
- 类型: refactor
- 改动量: M
- 问题表现: Windows rc1 包可生成并通过 --smoke-check，但普通启动时 NoneBot 加载 DicePP 插件失败：ModuleNotFoundError: No module named 'cryptography.fernet'。现场包内只有 cryptography/hazmat/bindings/_rust.pyd 和 dist-info，缺少 cryptography/fernet.py；原因是插件源码主要作为 datas 复制，PyInstaller 没有完整分析 DicePP 插件 import 链。短期可用 collect_submodules('cryptography') 修复，但类似动态依赖仍可能再次漏包。
- 开发备忘: 长期方向：重新梳理 Windows 打包结构，让 DicePP 插件代码尽量作为 PyInstaller 可静态分析的 Python 模块进入 Analysis，而不是主要依赖 datas 复制源码和手写 hiddenimports。需先验证 adapter/module/utils 等当前顶层导入路径是否能迁移或兼容；影响面包括 scripts/build/dicepp.spec、bot.py 的 frozen 路径、插件导入方式、release smoke test。风险点是改动可能影响开发环境插件加载和现有 NoneBot load_plugin 行为，适合在 RC 后续单独处理。

## runtime

### [B-260616-5f74ec] 重新设计 Standalone 无 QQ 服务入口
- 创建: 2026-06-16
- 优先级: P1
- 类型: refactor
- 改动量: L
- 问题表现: 当前 standalone_bot.py 是历史实验入口，混合了无 QQ 服务入口、DiceHub 注册、WebChat 启动和 /dpp runtime 绑定等职责。该模式目前不作为发布路径或新手路径使用，但未来网站可能需要单独部署 DicePP，绕过 QQ / NoneBot 直接提供服务。继续保留根目录入口和旧测试会让它看起来像现役能力，也会把未来重写约束在旧实现上。
- 开发备忘: 近期先将旧 standalone 入口归档为 docs/dev/archive/standalone_bot_legacy.py，删除围绕旧行为的测试，避免维护半成品运行模式。后续实现时重新设计无 QQ / 无 NoneBot 服务入口：明确 CLI/配置入口、HTTP/WebChat 边界、与 DiceHub 的显式启用策略、与 bot.py/NoneBot 入口的职责分离，并考虑是否提供 dicepp-standalone console script 或包内 runtime 模块。

