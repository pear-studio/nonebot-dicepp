# 跑团 Log 功能架构与行为契约

本文定义 DicePP 当前跑团聊天日志功能的用户行为、数据不变量、模块边界和验收契约。
实现与测试应持续满足本文；未来的 HTML 与图片归档能力也必须沿用这里定义的生命周期、
内容投影和外部副作用边界。

相关代码：

- `src/plugins/DicePP/module/common/log/command.py`
- `src/plugins/DicePP/module/common/log/service.py`
- `src/plugins/DicePP/module/common/log/recorder.py`
- `src/plugins/DicePP/module/common/log/projection.py`
- `src/plugins/DicePP/module/common/log/export_service.py`
- `src/plugins/DicePP/module/common/log/exporters/`
- `src/plugins/DicePP/module/common/log/publisher.py`
- `src/plugins/DicePP/module/common/log/providers/dice_log_v105.py`
- `src/plugins/DicePP/module/common/log/runtime.py`
- `src/plugins/DicePP/core/data/log_repository.py`
- `src/plugins/DicePP/core/data/models/log.py`
- `src/plugins/DicePP/core/data/schema/bot_log.py`
- `src/plugins/DicePP/adapter/nonebot_adapter.py`
- `src/plugins/DicePP/core/command/cq_extractor.py`

数据目录与 schema lifecycle 的上层约束见
[data-architecture-3.0.md](./data-architecture-3.0.md)。

## 核心原则

- 保留用户真正需要的跑团聊天录制能力。
- 保持 `on/off` 使用习惯，不为用户增加不必要的状态概念。
- 让日志状态和消息在 Bot 重启后可靠恢复。
- 玩家指令与 Bot 回复通过统一消息 hook 记录，不依赖命令优先级。
- 始终保存录制期间的完整文本数据，默认导出跑团正文，可显式导出全部记录。
- TXT 作为稳定、可恢复的基础导出格式。
- 导出和 Web 发布与日志生命周期解耦。
- TXT 与 DOCX 作为当前标准文件输出；为未来的图片归档和单文件 HTML
  富媒体导出预留接口与数据结构。
- 让每个模块可以单独测试，避免继续维护单个超大命令文件。

## 当前能力边界与扩展方向

当前不包含以下能力：

- 日志创建者、群管理员或骰主权限限制。
- 高级日志统计。
- 基于 Bot 回复文本猜测检定、属性变化或骰运。
- 默认启用图片二进制归档。
- 自动发布第三方云端日志。
- HTML 文件导出；当前只识别该选项并明确回复暂未支持。
- 自动删除已经上传到 QQ 群文件或第三方服务的副本。
- 论坛 BBCode 导出。

HTML 和图片归档属于已定义边界的扩展能力：HTML 必须保持单文件，图片必须在消息到达后
异步归档，并遵守容量、MIME 与引用清理约束。它们不得改变当前文本记录的权威地位，也
不得让普通导出隐式访问第三方服务。

## 设计决策摘要

| 决策点 | 选择 |
|--------|------|
| 用户可见状态 | 只区分正在记录与未记录，不增加不可继续的 closed 状态 |
| 数据兼容 | 不保留旧 Log 业务数据；已知旧结构在启动时事务性重建为当前 schema |
| 当前日志 | 每群保留一个 `current_log_id` |
| 开关指令 | 只保留 `.log on` / `.log off`，不再区分 new/end/halt |
| `.log on [名称]` | 统一创建、续录和切换日志 |
| `.log off` | 停止当前录制、保留当前选择并执行标准导出 |
| `.log set` | 删除；不再在采集时永久丢弃消息 |
| 导出内容 | 默认跑团正文，可用 `all` / `完整` 导出全部记录 |
| 统计 | 当前不提供 `.log stat` 和 `.stat log` |
| 权限 | 沿用全局命令权限，不在 Log 内新增权限门控 |
| 标准导出 | 当前为正文版 TXT + DOCX；未来 HTML 可加入默认组合 |
| TXT | 保留为基础权威格式和失败兜底 |
| DOCX | 已纳入标准导出 |
| HTML | 未来输出内嵌 CSS 与图片的单个 `.html` 文件 |
| 图片 | 当前保存结构化消息段；未来可增加二进制下载和归档 |
| Web 发布 | 合并到 `.log export <名称> web`，任何普通导出都不得隐式发布 |
| 已有链接 | `.log export <名称> link` 只读，不产生网络副作用 |
| 论坛代码 | 删除 |
| 千条提醒 | 删除 |
| 长时间提醒 | 当前不启用；`last_warn_at` 不产生用户可见行为 |

---

## 1. 用户指令契约

### 1.1 主帮助

当前主帮助展示：

```text
.log on [名称]              开始、继续或切换日志
.log off                    停止当前日志并导出
.log list                   查看本群日志
.log export <名称> [选项]   重新导出日志
.log del <名称>             删除日志
```

导出二级选项：

```text
.log export <名称>                  标准导出：正文版 TXT + DOCX
.log export <名称> txt              只导出正文版 TXT
.log export <名称> docx             只导出正文版 DOCX
.log export <名称> html             单文件 HTML；当前明确回复暂未支持
.log export <名称> web              发布正文版网页
.log export <名称> link             查看最近一次成功发布的网页链接
.log export <名称> all              标准导出全部记录
.log export <名称> <类型> all       导出或发布全部记录
```

`完整` 是 `all` 的中文别名。默认内容称为“跑团正文”，不会把内部实现使用的
`curated` / `complete` 等枚举暴露给玩家。

### 1.2 `.log on [名称]`

不带名称：

- 当前日志存在且未记录时继续记录。
- 当前日志已经在记录时返回已有状态，不重复重置会话。
- 没有当前日志时提示需要提供名称。

带名称：

- 按群和名称大小写不敏感查找日志。
- 找到目标时，目标成为当前日志并继续记录。
- 当前有另一份正在记录的日志且目标已经存在时，原子地停止旧日志并切换到目标；状态
  提交后为旧日志执行标准导出，导出失败不回滚已经完成的切换。
- 未找到目标且当前没有正在记录的日志时，创建该日志并立即开始记录。
- 未找到目标但当前有另一份日志正在记录时拒绝创建，避免名称输入错误导致时间线
  被意外切断；用户需要先执行 `.log off`。
- 回复必须明确区分“已新建并开始”和“已继续”，但玩家不需要预先判断名称是否存在。

不提供 `.log new`；创建能力已经完整合并到 `.log on <名称>`。

### 1.3 `.log off`

- 仅在当前日志 `recording=true` 时执行状态转换。
- 将当前日志设为未记录状态，保留 `current_log_id`。
- 状态可靠提交后执行标准导出：生成正文版 TXT 与 DOCX，并尝试分别上传 QQ
  群文件。
- 未来 HTML 可加入标准导出组合，但 Web 发布永远不属于标准导出。
- 导出或上传失败不恢复 recording；数据库记录仍可通过 `.log export` 重试。
- 已经处于 off 状态时只返回当前状态，不重复生成和上传文件。
- 之后可以用不带名称的 `.log on` 继续。

不提供 `.log end` 或 `.log halt`。系统没有不可继续的封存状态，因此不再为相同的
`recording=false` 结果维护多个用户命令。

### 1.4 `.log list`

列表至少显示：

- 日志名称；
- 是否为当前日志；
- 是否正在记录；
- 创建时间；
- 最后消息时间；
- 数据库消息数量；
- 最近导出时间。

消息数量直接从数据库查询，不维护独立内存统计。

### 1.5 `.log export <名称> [选项]`

导出不改变日志状态。

当前支持：

```text
.log export <名称>
.log export <名称> txt
.log export <名称> docx
.log export <名称> web
.log export <名称> link
.log export <名称> all
.log export <名称> <txt|docx|web> all
```

不指定类型时执行与 `.log off` 相同的标准导出组合，但不改变 recording。只指定 `all`
时使用相同格式组合导出全部记录。显式指定 `txt` 或 `docx` 时只生成一种格式。`web`
显式发布到配置的第三方 provider；`link` 只读取最近一次成功链接，不发起网络请求。

`html` 和 `html all` 已保留在指令语法中，但当前明确回复暂未支持，不生成占位文件：

```text
.log export <名称> html
.log export <名称> html all
```

每次导出读取一个稳定的 records 快照并生成独立版本，不覆盖旧文件。各格式分别记录
生成和群文件交付状态；标准导出中的某一种格式失败不影响其他格式。

### 1.6 `.log del <名称>`

- 正在记录的日志不能删除。
- 删除日志时级联删除消息记录。
- 删除当前但已 off 的日志时清空 `current_log_id`。
- 沿用全局命令权限和单步删除行为，不新增确认流程。
- 若日志曾上传到群文件或第三方服务，回复中应明确这些外部副本不会自动删除。

### 1.7 已移除指令

以下指令不保留隐藏别名，只返回功能已移除或新入口提示：

| 旧指令 | 当前响应 |
|--------|----------|
| `.log new <名称>` | 提示使用 `.log on <名称>` |
| `.log end` / `.log halt` | 提示使用 `.log off` |
| `.log get <名称>` | 提示使用 `.log export <名称> link`，不得触发上传 |
| `.log publish <名称>` | 提示使用 `.log export <名称> web` |
| `.log set ...` | 回复当前日志始终保存完整数据，请在导出时使用默认正文或 `all` |
| `.log stat ...` | 回复统计功能暂未提供 |
| `.stat log ...` | 回复统计功能暂未提供 |

这些响应只提供新入口提示，不执行旧命令的业务别名行为。

---

## 2. 日志生命周期

用户只感知 on/off，不增加 closed、paused、ended 等额外状态名称。

核心状态由两个维度组成：

```text
群级 current_log_id
日志级 recording
```

可表达：

| 状态 | current_log_id 指向该日志 | recording |
|------|---------------------------|-----------|
| 当前且记录中 | 是 | true |
| 当前但未记录 | 是 | false |
| 未选择日志 | 否 | false |

不应出现“非当前但 recording=true”的持久状态。`LogService` 以群为粒度串行化生命周期
操作，并在一个 SQLite 事务中同时更新旧日志、目标日志与 `current_log_id`。数据库使用
同群 `recording=true` 的部分唯一索引作为最后防线。

状态转换：

```text
on <不存在名称>，且当前没有日志正在记录
  -> 创建日志 -> 当前且记录中

on <已有名称>
  -> 必要时停止旧日志 -> 目标日志成为当前且记录中 -> 为旧日志执行标准导出

on，无名称
  -> 当前且未记录 -> 当前且记录中

off
  -> 当前且记录中 -> 当前但未记录 -> 标准导出
```

导出结果是日志的一个版本，不是日志生命周期的终点。

---

## 3. 消息采集

### 3.1 统一 Hook

当前采集结构：

```text
入站消息 Hook
  -> LogRecorder.record_user_message()

出站消息 Hook
  -> LogRecorder.record_bot_message()
```

Log 的入站 Hook 以一条平台消息事件为记录粒度，不复用当前按 `command_split` 拆分后逐个
`msg_cur` 触发的 Persona 入站流。一条 QQ 消息无论包含多少段 DicePP 指令，都只生成一条
record，并保留同一个 `raw_content`、`segments_json` 与 `message_id`。

两条路径共享：

- 当前日志查询；
- recording 状态检查；
- 消息结构化；
- 数据库存储；
- 撤回关联；
- 失败日志。

### 3.2 记录范围

记录：

- 日志开启后的群玩家消息；
- 日志开启后的 Bot 群消息；
- 玩家骰点指令；
- 骰点结果；
- 回复、@、图片和其他 CQ 段的结构化元数据。

不记录：

- 私聊；
- 日志未开启或已 off 期间的消息；
- QQ 历史消息；
- 发送失败的 Bot 消息；
- 群成员变动等未建模系统事件。

### 3.3 Log 管理消息

`.log` 自身的管理指令和对应 Bot 确认消息若在 recording 边界内自然被采集，应标记为
`log_control`。系统不为了让管理指令与回复成对出现而补造消息或跨状态强制写入；全部记录
表示“录制开启期间实际保存的全部消息”，不是操作审计日志。

跑团正文默认隐藏：

- `.log on/off/list/export/del`；
- 这些操作对应的 Bot 管理提示；
- Log 自己产生的长时间提醒。

`all` / `完整` 导出保留这些已经记录的内容。

骰点、角色卡等游戏指令不是管理噪音，跑团正文继续保留。

### 3.4 记录字段

每条消息至少保存：

```text
id
log_id
time
user_id
nickname
source
message_type
plain_content
raw_content
segments_json
message_id
recalled_at
```

其中：

- `source`: `user` 或 `bot`。
- `message_type`: `ambient`、`command`、`log_control`、`file` 等。
- `plain_content`: 纯文本视图。
- `raw_content`: 原始 CQ 内容。
- `segments_json`: 结构化消息段。
- `message_id`: 用于撤回定位。
- `recalled_at`: 撤回标记，默认导出排除。

昵称使用记录时保存的值，导出不会用新的群名片覆盖历史昵称。

---

## 4. 跑团正文与全部记录

系统始终保存录制期间的完整文本记录，只在导出时决定展示视图。`.log set` 不再提供
采集过滤，避免不可恢复的数据丢失。

### 4.1 跑团正文

默认用户可读版本：

- 隐藏 Log 管理指令和管理回复；
- 隐藏整条由中英文圆括号包围的场外发言；
- 保留骰点和其他游戏指令；
- 保留 Bot 骰点结果；
- 回复消息转换为引用块；
- @转换为可读昵称；
- 图片转换为图片或占位符；
- 文件转换为 `[文件：名称]`；
- 被撤回消息不显示。

### 4.2 全部记录

录制期间的完整已保存时间线：

- 保留 Log 管理消息；
- 保留场外发言；
- 保留玩家与 Bot 指令；
- 保留媒体占位和结构化信息；
- 被撤回消息不显示；当前不提供审计导出模式。

用户通过 `all` 或中文别名 `完整` 选择该视图。代码内部可以使用稳定枚举，但用户帮助、
回复和文件元数据统一使用“跑团正文”与“全部记录”。

### 4.3 不再提供采集过滤

当前不提供以下采集开关：

- outside；
- command；
- bot；
- media；
- forum_code。

这避免设置错误导致无法恢复的数据丢失。

---

## 5. 导出器

内容选择与格式渲染必须分层。`LogProjector` 先把同一个 records 快照转换为
`LogProjection`，统一完成跑团正文或全部记录的过滤、回复引用、@、媒体占位和撤回排除；
TXT、DOCX、HTML 与 Web 不得各自重复实现内容筛选。

定义独立 Exporter 接口，使格式扩展不进入 LogCommand：

```python
class LogExporter(Protocol):
    format: ClassVar[LogExportFormat]
    suffix: ClassVar[str]

    async def generate(
        self,
        projection: LogProjection,
        target: ExportTarget,
    ) -> GeneratedArtifact:
        ...
```

当前实现与扩展位置：

```text
TextLogExporter       当前实现
DocxLogExporter       当前实现
HtmlLogExporter       未来单文件 HTML 扩展
```

一次导出请求固定一个 records 快照，并记录 `record_upper_id`。不指定格式的标准导出
可以从同一快照生成多个 artifact；每个 artifact 分别登记生成与交付状态，并通过共同的
`request_id` 归属于同一次用户操作。

### 5.1 TXT

TXT 是基础权威格式，至少保留：

- 群号；
- 日志名称；
- 日志 ID；
- 创建时间；
- 昵称；
- 用户 ID；
- 消息时间；
- 可读正文；
- 回复引用；
- 媒体占位符。

文件名应避免跨群覆盖：

```text
<日志名>_群<group_id>_<log_id短码>_<request_id短码>_<导出时间>.txt
```

每次导出生成独立版本，不覆盖旧导出文件。

### 5.2 DOCX

DOCX 已与 TXT 一起组成标准导出。显式指定 `docx` 时只生成 DOCX。

至少包含：

- 消息时间；
- 清晰的发言分隔；
- 稳定的用户颜色；
- 回复引用样式；
- 当前图片未归档占位符；
- 未来图片归档启用后的尺寸限制和失败占位符。

### 5.3 HTML

HTML 是已定义边界的未来离线格式。启用后始终输出一个自包含 `.html` 文件，不输出 ZIP
或外部 assets 目录：

```text
<日志名>_群<group_id>_<log_id短码>_<request_id短码>_<导出时间>.html
```

约束：

- CSS 内嵌；
- 图片归档启用后通过 `data:` URI / Base64 内嵌；
- 不引用外部脚本、样式或图片资源；
- 对用户内容做 HTML 转义，不执行消息中的脚本；
- 支持图片和长图；
- 支持回复引用；
- 支持不同消息类型样式；
- 支持点击查看原图；
- 不依赖 Word 对 WebP、GIF 等格式的兼容性。

Base64 会增加文件体积，因此图片归档能力必须同时落地单图大小、附件数量和单日志
总容量限制。

HTML 启用后加入标准导出组合。标准导出当前生成 TXT + DOCX，届时生成
TXT + DOCX + HTML；显式指定格式的 `.log export` 始终只生成一种文件。

### 5.4 QQ 群文件

- `.log off` 默认上传标准导出的正文版 TXT 与 DOCX；未来 HTML 启用后同时上传 HTML。
- 不指定格式的 `.log export` 上传相同标准组合，但不改变日志状态。
- 显式 `.log export <名称> <txt|docx>` 只生成并上传指定格式；未来 HTML 启用后遵循
  同一规则。
- 优先查找已有“跑团log”文件夹。
- 文件夹不存在时上传根目录。
- 不要求自动创建文件夹。
- 上传失败不应回滚已经成功保存的日志状态。
- 同一次标准导出中某个文件失败不影响其他文件；结果回复分别说明各 artifact 状态。
- QQ 适配器或 ClientProxy 必须把每个文件的结构化交付结果返回给导出编排层，不能仅创建
  `BotSendFileCommand` 就把 `delivery_status` 记为成功。

---

## 6. 图片与附件预留

### 6.1 当前消息段能力

当前通过 `core.command.cq_extractor.extract_segments()` 保存结构化消息段，TXT、DOCX 和
未来格式共享同一份 `LogProjection`。图片二进制尚不归档，日志资源也不复用 Persona
图片目录；两者具有不同的生命周期、引用和清理策略。

### 6.2 当前保存契约

当前实现：

- 保存 `raw_content`；
- 保存 `segments_json`；
- 图片段保存 URL、file、sub_type 等元数据；
- Exporter 接口能够识别图片段；
- 没有本地资源时输出 `[图片未归档]`。

当前不下载图片二进制。由于 QQ URL 可能过期，未归档的历史图片以后可能无法补救；这是
明确接受的文本优先取舍，不应在用户提示中暗示图片已经归档。

### 6.3 图片归档扩展契约

QQ 图片 URL 可能过期。真正归档必须在收到消息后尽快异步下载，不能等到导出时。

启用图片归档时增加：

```text
log_assets
  id
  sha256
  mime_type
  size
  local_path
  original_url
  original_file
  download_status
  created_at

log_record_assets
  record_id
  asset_id
  segment_index
```

资源目录：

```text
data/bots/<bot_id>/log_assets/
```

不要直接复用 `data/persona_images`，因为 Persona 与 Log 的清理、引用和保留策略不同。

扩展能力必须包含：

- 内容 hash 去重；
- 单图片大小限制；
- 单日志附件数量或容量限制；
- 表情包默认是否归档；
- 删除日志后的无引用资源清理；
- MIME 校验；
- DOCX 图片嵌入策略；
- HTML 使用 `data:` URI / Base64 的单文件嵌入策略；
- 单日志附件总容量限制，控制单文件 HTML 膨胀。

配置建议沿用以下结构：

```json
{
  "log": {
    "archive_images": false,
    "max_image_size_mb": 10,
    "max_assets_per_log": 500,
    "max_asset_total_mb_per_log": 200
  }
}
```

默认关闭图片二进制归档。

---

## 7. Web 发布

当前行为：

```text
.log off / .log export <名称>
  -> 标准文件导出
  -> QQ 群文件
  -> 不访问第三方服务

.log export <名称> web [all]
  -> 显式发布正文或全部记录到 Web provider

.log export <名称> link
  -> 只读取已有链接
```

约束：

- 配置模型不包含 `upload_enable`。配置 loader 只负责把旧 `upload_endpoint` /
  `upload_token` 一次性整理到 `log.web` 并移除旧键；它不会恢复自动上传语义。
- provider endpoint 为空时 Web 发布不可用；endpoint 已配置时，也只有显式 `web`
  选项才能触发网络请求。
- 默认发布跑团正文，`web all` / `web 完整` 才发布全部记录，并明确提示其中包含场外与
  Log 管理消息。
- `.log export <名称> link` 不得产生上传副作用。
- 网络调用使用异步客户端或后台任务，不阻塞 Bot 事件循环。
- 单元与集成测试不得调用真实外部服务。
- `LogCommand` 只解析统一的 export 入口；内部仍由独立 `LogPublisher` 执行发布，不把
  Web provider 混入 TXT、DOCX、HTML Exporter。
- 保存 provider、内容视图、records 快照上界、URL、发布时间和结果。
- `.log del` 明确提示远端副本不会自动删除。
- 不提供 `.log unpublish`；远端副本删除不属于当前能力。

---

## 8. 统计与提醒

### 8.1 统计

当前不提供高级统计：

- `.log stat`；
- `.stat log`；
- 成功/失败；
- 大成功/大失败；
- 属性变化；
- 骰运；
- 活跃 TOP5；
- 内存 stats、participants、dice_faces 和 color_map。

`.log list` 中的消息数量通过数据库 `COUNT` 取得，不建立独立统计系统。

任何统计扩展都必须消费 Roll 模块提供的结构化事件，不得解析 Bot 文本。

### 8.2 提醒

当前不提供每 1000 条玩家消息提醒或连续记录时间提醒。`last_warn_at` 作为 schema 字段保留，
但不产生用户可见行为，也不维护独立 session message count。

---

## 9. 消息撤回

撤回不能依赖当前日志。

当前流程：

```text
收到群撤回事件
  -> 按 bot + group_id + message_id 查找记录
  -> 设置 recalled_at
  -> 后续导出排除该消息
```

选择标记而不是直接硬删除，便于：

- 保持数据操作可追踪；
- 避免增量统计回滚问题；
- 将来支持明确的审计策略。

当前没有高级统计，因此撤回只影响记录查询和导出。

已经生成或上传的文件不会自动更新。云端副本和群文件也不会自动删除，用户提示必须如实说明。

---

## 10. 权限

当前沿用全局命令权限，不新增：

- 日志创建者专属权限；
- 群管理员权限；
- 骰主权限；
- 发布确认权限。

日志会话可以保存 `created_by` 作为元数据，但不用于门控。

权限治理如需扩展，应单独设计，不与 Log 生命周期绑定。这意味着普通群成员仍可 off、
del 或显式 Web 发布；Log 内部不手写一套局部权限规则。

---

## 11. 数据模型

当前数据库使用：

```text
data/bots/<bot_id>/log.db
```

### 11.1 `log_group_state`

```text
group_id         TEXT PRIMARY KEY
current_log_id   TEXT NULL
updated_at       TEXT NOT NULL
```

只保存群级当前选择，不再保存 `.log set` 过滤。

### 11.2 `logs`

```text
id                 TEXT PRIMARY KEY
group_id           TEXT NOT NULL
name               TEXT NOT NULL
recording          INTEGER NOT NULL
created_by         TEXT
created_at         TEXT NOT NULL
updated_at         TEXT NOT NULL
last_message_at    TEXT
record_begin_at    TEXT
last_warn_at       TEXT
```

约束：

- `current_log_id` 可以指向 recording 或 off 日志。
- 不增加 closed。
- 使用 `(group_id, name COLLATE NOCASE)` 唯一索引保证同群名称唯一。
- 使用 `WHERE recording = 1` 的 `group_id` 部分唯一索引保证同群最多一份正在记录。

### 11.3 `records`

```text
id                 INTEGER PRIMARY KEY AUTOINCREMENT
log_id             TEXT NOT NULL
time               TEXT NOT NULL
user_id            TEXT NOT NULL
nickname           TEXT
source             TEXT NOT NULL
message_type       TEXT NOT NULL
plain_content      TEXT NOT NULL
raw_content        TEXT NOT NULL
segments_json      TEXT
message_id         TEXT
recalled_at        TEXT
```

索引至少包含：

- `log_id, id`；
- `message_id`；
- `time`；
- 通过 logs 关联的 group 查询。

### 11.4 `log_exports`

```text
id                 INTEGER PRIMARY KEY AUTOINCREMENT
request_id         TEXT NOT NULL
log_id             TEXT NOT NULL
format             TEXT NOT NULL
view               TEXT NOT NULL
record_upper_id    INTEGER
created_at         TEXT NOT NULL
local_path         TEXT
group_file_name    TEXT
generation_status  TEXT NOT NULL
delivery_status    TEXT NOT NULL
note               TEXT
```

保存多次导出历史，不覆盖最近一次记录。标准导出的 TXT、DOCX，以及未来的 HTML 使用同一个
`request_id`，但每种 artifact 独立记录成功或失败。

### 11.5 `log_publications`

Web 发布记录使用：

```text
id                 INTEGER PRIMARY KEY AUTOINCREMENT
request_id         TEXT NOT NULL
log_id             TEXT NOT NULL
provider           TEXT NOT NULL
view               TEXT NOT NULL
record_upper_id    INTEGER
created_at         TEXT NOT NULL
published_at       TEXT
url                TEXT
status             TEXT NOT NULL
note               TEXT
```

不要继续把本地导出和 Web 发布混在同一组 `upload_*` 字段中。

### 11.6 附件表

当前 schema 不创建附件表。未来启用图片归档时再创建 `log_assets` /
`log_record_assets`；由于 records 已保存 `segments_json`，该扩展不需要重写消息采集结构。

---

## 12. 模块边界

当前目录结构：

```text
module/common/log/
  command.py
  service.py
  recorder.py
  projection.py
  export_service.py
  exporters/
    base.py
    text.py
    docx.py
  publisher.py
  providers/
    dice_log_v105.py
  runtime.py
  types.py
  errors.py
```

职责：

| 模块 | 职责 |
|------|------|
| `LogCommand` | 解析指令、生成用户回复并调用 Runtime |
| `LogRuntime` | 组装服务、注册消息 Hook、刷新 Web provider |
| `LogService` | 生命周期、群级串行化、事务不变量与导出请求快照 |
| `LogRepository` | SQLite 查询与持久化 |
| `LogRecorder` | 入站/出站 Hook、消息结构化 |
| `LogProjector` / `LogProjection` | 生成并承载跑团正文或全部记录的结构化展示模型 |
| `LogExportCoordinator` | 多格式生成、群文件交付与逐 artifact 审计 |
| `LogExporter` | 单一文件格式生成；当前实现 TXT 与 DOCX |
| `LogPublisher` | `.log export ... web` 的显式第三方发布 |
| `DiceLogV105Provider` | Dice Log v105 协议适配与异步 HTTP |

Command 不直接执行 SQL、HTTP 或文件格式细节。未来的 `HtmlLogExporter` 和
`LogMediaStore` 分别进入 exporters 与媒体存储边界，不改变 Command 或 Repository 的
职责。

---

## 13. Schema 所有权与启动校验

Log 数据库固定为：

```text
data/bots/<bot_id>/log.db
```

旧 Log 业务数据不在兼容范围内。系统不提供记录级迁移、一次性导入工具或运行时 legacy
fallback，也不为这套结构建立 `bot_log` v2 双轨或 forward migration；启动时只接受当前
latest schema，或对已知旧结构执行破坏性重建。

初始化在使用业务表前检查结构是否符合本文定义：

1. 表、必需列、关键索引或约束缺失，或命中已知旧结构时，视为旧损坏库。
2. 在一个 SQLite 事务中删除 Log 业务表，按当前 latest schema 重建表、
   索引与约束；旧记录不读取、不转换。
3. 重建失败时整体回滚，不能留下半套业务表。
4. 重建成功后，下次启动的结构检查必须直接通过，不得再次清空新数据。
5. 未知的 unmanaged 表或额外业务表不执行破坏性删除，初始化明确拒绝并报告表名。

`LogRepository` 不通过查询路径私自创建业务表；schema 的唯一 owner 是
`BOT_LOG_TARGET`。结构探测和破坏性重建属于 schema 初始化责任，不应散落到
Repository 查询路径。

---

## 14. 错误与事务边界

- 日志状态持久化失败时，不返回成功提示。
- 消息记录失败要有可检索日志，不得 `except Exception: pass`。
- `.log off` 先可靠停止并保存状态，再执行标准导出。
- 导出失败不能恢复成 recording，也不能丢失日志记录。
- 标准导出中的 TXT、DOCX、未来 HTML 分别记录状态；一种格式失败不影响其他格式。
- QQ 群文件上传失败只影响对应 artifact 的 delivery 状态。
- Web 发布失败不影响本地日志和本地导出。
- 导出临时文件使用独立路径，成功后再登记 `log_exports`。
- 生命周期状态转换以群为粒度串行化，并在一个 SQLite 事务中维护 recording 不变量。
- 不在持有群锁或数据库事务期间执行文件生成、QQ API 或 Web 网络请求。
- 所有 async 数据库与网络调用由上游正确 await。

---

## 15. 验收场景

当前验收至少覆盖以下真实调用链。

### 15.1 生命周期

```text
.log on 团A
玩家普通发言
玩家骰点指令
Bot 骰点回复
.log off
off 期间发言
.log on
继续发言
.log off
```

验证：

- 开启期间消息完整；
- off 期间消息不记录；
- 玩家指令和 Bot 结果均存在；
- 第一次 `on 团A` 自动创建，第二次 bare `on` 继续当前日志；
- 每次 recording -> off 都保留当前选择并生成正文版 TXT + DOCX；
- 已处于 off 时重复执行 `.log off` 不重复导出；
- 之后 `.log on 团A` 仍可继续。

### 15.2 多日志

```text
.log on 团A
.log off
.log on 团B
.log on 团A
```

验证：

- 两份日志独立；
- `on 团B` 在没有日志 recording 时自动创建；
- `on 团A` 在团B recording 时原子停止团B并切换到已有团A，同时为团B执行标准导出；
- 同群只能一份 recording；
- `.log list` 状态正确。

另测当前团A recording 时执行 `.log on 不存在名称`：拒绝创建，团A 继续 recording。

### 15.3 重启恢复

```text
.log on 团A
记录消息
重启 Bot
.log list
.log off
.log on
```

验证当前日志、recording 和 records 均能恢复。

### 15.4 内容视图与格式

记录：

- `.log` 管理指令；
- 场外括号消息；
- 玩家骰点指令；
- Bot 骰点回复；
- 普通发言；
- @、reply、图片 CQ。

验证：

- 默认跑团正文隐藏管理与纯场外；
- 跑团正文保留骰点指令和结果；
- `all` / `完整` 保留录制期间的全部已保存消息；
- 两者不修改数据库记录。
- TXT 与 DOCX 使用同一个 projection 快照，内容选择一致。
- 不指定格式时生成 TXT + DOCX；显式指定格式时只生成一种。
- HTML 当前明确回复暂不支持，不生成占位文件。

### 15.5 撤回

- 在日志 A 记录消息。
- 切换到日志 B。
- 撤回日志 A 的消息。
- 导出日志 A。

验证撤回不依赖当前日志，导出不包含已撤回消息。

### 15.6 外部能力

- `.log off` 与普通 `.log export` 不调用第三方服务。
- `.log export <名称> web [all]` 使用 mock HTTP。
- `.log export <名称> link` 只读。
- QQ 上传失败时日志与本地导出仍存在。
- 标准导出中 TXT 或 DOCX 单独失败时，另一种格式仍可成功。

### 15.7 图片预留

- 图片 CQ 被解析为 segment。
- `segments_json` 正确持久化。
- 没有下载资源时 TXT 输出占位符。
- 未来启用图片归档时不需要改变 records 基本结构。

---

## 16. 测试与验收契约

### 16.1 Repository

- 群状态 CRUD；
- 同群名称大小写不敏感唯一；
- 同群 `recording=true` 部分唯一索引；
- 当前日志切换；
- records 插入、查询、撤回；
- 日志级联删除；
- export request/artifact 历史；
- publication 历史；
- 新建数据库直接得到当前 schema；
- 已知旧损坏表结构会被破坏性重建；
- 重建后写入新数据并再次启动，新数据不会被重复清空。

### 16.2 Service

- on 创建、续录与 bare on；
- active -> 已有目标的原子切换；
- active -> 已有目标切换后为旧日志执行标准导出；
- active -> 不存在目标时拒绝创建；
- off 状态转换与标准导出编排；
- 重复 off 不导出；
- 多日志切换；
- recording 不变量；
- off 后仍可 on；
- 删除当前 off 日志；
- 多 artifact 导出失败隔离。

### 16.3 Recorder

- 普通消息；
- 已识别命令；
- Bot 回复；
- off 跳过；
- log_control 标记；
- CQ segments；
- message_id；
- 一条包含多个 `command_split` 片段的平台消息只生成一条 record。

### 16.4 Projection 与 Exporter

- 跑团正文/全部记录；
- reply；
- at；
- 图片占位；
- 文件名安全；
- 不同群与多次导出不覆盖；
- TXT/DOCX 内容视图一致；
- 标准导出共享 request_id 和 record_upper_id；
- HTML 当前未启用时明确拒绝。

### 16.5 Publisher

- 只有显式 `web` 触发请求；
- 默认正文与 `web all` payload 区分；
- provider 成功、失败和超时；
- `link` 只读；
- 不调用真实外部服务。

### 16.6 指令 E2E

使用 `dicepp-shell` 覆盖核心生命周期，不调用真实 QQ、真实云端服务或付费 API。

---

## 17. 行为不变量与维护原则

- 所有 DB、昵称、网络和导出异步调用正确 await。
- Bot 重启后当前日志和 recording 状态正确恢复。
- 玩家骰点指令与 Bot 结果均被记录。
- 一条平台消息只生成一条 record，不因多指令拆分重复。
- `.log on <名称>` 统一创建和续录，并保护 active -> 不存在名称的误切换。
- `.log off` 可靠停止、保留当前选择并生成正文版 TXT + DOCX；重复 off 不重复导出。
- 不提供 new/end/halt 的业务别名。
- `.log set` 不再永久丢弃消息。
- 普通导出不访问第三方；只有 `.log export <名称> web [all]` 发布。
- `.log export <名称> link` 无副作用。
- 标准导出各 artifact 独立记录生成和群文件交付状态。
- 高级统计代码从核心路径移除。
- 图片消息至少能持久化结构化 segment。
- 核心生命周期拥有一条真实 E2E 测试。
- 已知旧损坏 Log 表结构会在事务中破坏性重建，不保留旧业务数据。
- 重建后的数据库再次启动不会重复清空，当前日志、recording 和 records 继续保留。
- 配置兼容只负责把旧 `upload_*` 键整理到 `log.web`；不得恢复默认上传行为。
- 旧指令只返回迁移提示，不得成为隐藏的业务别名。
- 新格式必须复用 `LogProjection`、稳定快照和逐 artifact 审计，不得在 Command 中复制
  内容筛选、文件生成或网络逻辑。
- HTML 当前未启用；未来启用时提供内嵌 CSS 与图片的单个 `.html`，并加入标准导出。
- 图片归档扩展不得改变现有 records 语义，也不得默认开启不受限的二进制下载。
