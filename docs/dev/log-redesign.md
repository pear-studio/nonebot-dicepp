# 跑团 Log 功能重构设计

本文记录 DicePP 跑团聊天日志功能的现状诊断、产品取舍和重构目标，供后续 agent
直接拆分任务与实施。

本文描述的是已讨论确认的目标设计，不代表当前代码已经实现。

相关代码：

- `src/plugins/DicePP/module/common/log_command.py`
- `src/plugins/DicePP/core/data/log_repository.py`
- `src/plugins/DicePP/core/data/models/log.py`
- `src/plugins/DicePP/core/data/schema/bot_log.py`
- `src/plugins/DicePP/adapter/nonebot_adapter.py`
- `src/plugins/DicePP/core/command/cq_extractor.py`

数据目录与 schema lifecycle 的上层约束见
[data-architecture-3.0.md](./data-architecture-3.0.md)。

## 背景

现有 log 功能最初作为一套完整的跑团日志系统合入，包含：

- 每群多份命名日志；
- 玩家消息和 Bot 消息自动记录；
- `new/on/off/halt/end/list/stat/get/del/set` 指令；
- TXT、DOCX 和论坛代码导出；
- QQ 群文件上传；
- 第三方云端日志发布；
- 消息、参与者、检定、属性变化和骰面统计；
- 长时间与大消息量提醒；
- 消息撤回同步删除。

随后项目把旧 DataManager 和同步 `log_db.py` 迁移到 `BotDatabase` 与
`LogRepository`，但业务命令没有完成接线，形成半迁移状态。

当前生产问题最先表现为 `.log new` 抛出：

```text
NameError: name 'get_connection' is not defined
```

进一步诊断确认，这不是单一漏 import：

1. 旧同步 `log_db.py` 已删除，但 `get_connection`、`upsert_log`、
   `insert_record`、`fetch_records` 等调用点仍然存在。
2. `_load_group_payload()` 每次返回新的空 payload。
3. `_save_group_payload()` 是 no-op，群级当前日志、过滤和统计状态无法跨消息保存。
4. `_generate_file()` 已改成 async，但 `_handle_end()` 仍同步调用，稳定触发
   coroutine 未 await 的错误。
5. 新 `LogRepository` 没有按群列出日志、按名称查询日志或保存当前日志指针的 API。
6. 当前测试只覆盖纯函数和 Repository CRUD，没有覆盖完整日志生命周期。

最小复现已确认：

```text
修改群 payload 后重新加载       -> 状态丢失
执行 .log new                  -> NameError
执行 .log end                  -> cannot unpack non-iterable coroutine object
```

因此本次不采用恢复旧同步函数的桥接修复，而是按新异步数据层重新建立业务模型。

## 目标

- 保留用户真正需要的跑团聊天录制能力。
- 保持现有 `on/off` 使用习惯，不为用户增加不必要的状态概念。
- 让日志状态和消息在 Bot 重启后可靠恢复。
- 玩家指令与 Bot 回复通过统一消息 hook 记录，不依赖命令优先级。
- 始终保存完整文本数据，在导出时选择 clean 或 full 视图。
- TXT 作为稳定、可恢复的基础导出格式。
- 导出和云端发布与日志生命周期解耦。
- 为未来的图片归档、HTML 和 DOCX 富媒体导出预留接口与数据结构。
- 让每个模块可以单独测试，避免继续维护单个超大命令文件。

## 不纳入第一阶段

- 新增日志创建者、群管理员或骰主权限限制。
- 高级日志统计。
- 基于 Bot 回复文本猜测检定、属性变化或骰运。
- 默认启用图片二进制归档。
- 自动发布第三方云端日志。
- 自动删除已经上传到 QQ 群文件或第三方服务的副本。
- 论坛 BBCode 导出。

## 设计决策摘要

| 决策点 | 选择 |
|--------|------|
| 用户可见状态 | 只区分正在记录与未记录，不增加不可继续的 closed 状态 |
| 当前日志 | 每群保留一个 `current_log_id` |
| 开关指令 | 保留 `.log on` / `.log off` |
| `.log end` | 停止、取消当前选择并导出，之后仍可 `.log on <名称>` |
| `.log halt` | 从主帮助隐藏，暂时作为兼容指令保留 |
| `.log set` | 删除；不再在采集时永久丢弃消息 |
| 导出视图 | 默认 clean，可显式导出 full |
| 统计 | 第一阶段删除 `.log stat` 和 `.stat log` |
| 权限 | 保持现状，不新增权限门控 |
| TXT | 保留为基础权威格式 |
| DOCX | 改为按需导出，第一阶段可暂缓 |
| HTML | 为富媒体日志预留，优先级低于核心 TXT |
| 图片 | 保存结构化消息段；二进制下载和归档后续启用 |
| 云端发布 | 从 `.log end` 拆出为显式 `.log publish` |
| `.log get` | 不再触发上传；由 `export` 和 `link` 分别承担 |
| 论坛代码 | 删除 |
| 千条提醒 | 删除 |
| 长时间提醒 | 可保留或延后，不作为第一阶段阻塞项 |

---

## 1. 用户指令契约

### 1.1 主帮助

第一阶段主帮助只展示：

```text
.log new <名称>             创建并开始新的日志
.log on [名称]              继续当前日志或切换到指定日志
.log off                    暂停当前日志
.log end                    停止并导出当前日志
.log list                   查看本群日志
.log export <名称> [选项]   重新导出日志
.log del <名称>             删除日志
```

低频可选功能：

```text
.log publish <名称>         发布到云端日志服务
.log link <名称>            查看已经保存的云端链接
```

### 1.2 `.log new <名称>`

- 创建命名日志并立即开始记录。
- 日志名称在同一群内大小写不敏感唯一。
- 当前有正在记录的日志时拒绝创建，避免无意拆分时间线。
- 当前日志已 `off` 暂停时，允许直接创建新日志。
- 创建新日志后，旧暂停日志仍保留，新日志成为当前日志。

### 1.3 `.log on [名称]`

不带名称：

- 当前日志存在且已暂停时继续记录。
- 当前日志已经在记录时返回已有状态，不重复重置会话。
- 没有当前日志时提示需要提供名称或创建新日志。

带名称：

- 按群和名称查找日志。
- 当前有另一份正在记录的日志时，沿用旧行为：先暂停旧日志，再切换到目标日志。
- 目标日志成为当前日志并开始记录。
- 即使目标日志以前执行过 `.log end`，仍允许继续记录。

### 1.4 `.log off`

- 将当前日志设为未记录状态。
- 保留 `current_log_id`。
- 之后可以用不带名称的 `.log on` 继续。
- 不导出文件，不取消当前选择。

### 1.5 `.log end`

- 停止当前日志。
- 清空本群 `current_log_id`。
- 生成默认 clean TXT。
- 尝试上传到 QQ 群文件。
- 记录一次导出结果。
- 不自动发布第三方云端日志。
- 不将日志标记为不可继续，之后仍可 `.log on <名称>`。

`.log end` 的语义是“停止本次记录并导出”，不是不可逆封存。

### 1.6 `.log list`

列表至少显示：

- 日志名称；
- 是否为当前日志；
- 是否正在记录；
- 创建时间；
- 最后消息时间；
- 数据库消息数量；
- 最近导出时间。

消息数量直接从数据库查询，不维护独立内存统计。

### 1.7 `.log export <名称> [选项]`

导出不改变日志状态。

第一阶段支持：

```text
.log export <名称>
.log export <名称> clean
.log export <名称> full
.log export <名称> txt
```

默认等价于：

```text
.log export <名称> clean
```

后续可扩展：

```text
.log export <名称> docx
.log export <名称> html
```

### 1.8 `.log del <名称>`

- 正在记录的日志不能删除。
- 删除日志时级联删除消息记录。
- 删除当前但已暂停的日志时清空 `current_log_id`。
- 第一阶段保持现有权限行为和单步删除行为，不新增确认流程。
- 若日志曾上传到群文件或第三方服务，回复中应明确这些外部副本不会自动删除。

### 1.9 兼容指令

以下指令不在主帮助中展示：

| 旧指令 | 兼容行为 |
|--------|----------|
| `.log halt` | 停止并清空当前选择，但不导出 |
| `.log get <名称>` | 等价于只读 `.log link <名称>`，不得触发上传 |
| `.log set ...` | 回复新版始终保存完整数据，请在导出时选择 clean/full |
| `.log stat ...` | 回复统计功能暂未提供 |
| `.stat log ...` | 回复统计功能暂未提供 |

兼容行为后续可在正式版本周期中移除。

---

## 2. 日志生命周期

不增加用户可见的 closed 状态。

核心状态由两个维度组成：

```text
群级 current_log_id
日志级 recording
```

可表达：

| 状态 | current_log_id 指向该日志 | recording |
|------|---------------------------|-----------|
| 当前且记录中 | 是 | true |
| 当前但已暂停 | 是 | false |
| 未选择日志 | 否 | false |

不应出现“非当前但 recording=true”的持久状态。Repository 或 LogService 在切换日志时负责
维持这一不变量。

状态转换：

```text
new
  -> 当前且记录中

off
  -> 当前但已暂停

on
  -> 当前且记录中

halt
  -> 未选择日志

end
  -> 未选择日志 + 导出

on <旧日志>
  -> 当前且记录中
```

导出结果是日志的一个版本，不是日志生命周期的终点。

---

## 3. 消息采集

### 3.1 统一 Hook

现有玩家消息通过低优先级 `LogRecorderCommand` 捕获，导致已识别指令可能在到达记录器前
被命令系统截断。

目标结构：

```text
入站消息 Hook
  -> LogRecorder.record_user_message()

出站消息 Hook
  -> LogRecorder.record_bot_message()
```

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
- 日志未开启或已暂停期间的消息；
- QQ 历史消息；
- 发送失败的 Bot 消息；
- 群成员变动等未建模系统事件。

### 3.3 Log 管理消息

`.log` 自身的管理指令和对应 Bot 确认消息仍可进入完整记录，但应标记消息类型。

clean 导出默认隐藏：

- `.log new/on/off/end/list/export/del/publish/link`；
- 这些操作对应的 Bot 管理提示；
- Log 自己产生的长时间提醒。

full 导出保留这些内容。

骰点、角色卡等游戏指令不是管理噪音，clean 模式继续保留。

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

昵称以记录时值为主。导出时可以补查昵称，但不应默认用新群名片覆盖历史昵称。

---

## 4. clean 与 full 导出

旧 `.log set` 在采集前永久丢弃消息，且群级过滤与日志级数据库字段语义不一致。

新设计始终保存完整文本记录，在导出时决定展示视图。

### 4.1 clean

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

### 4.2 full

完整时间线：

- 保留 Log 管理消息；
- 保留场外发言；
- 保留玩家与 Bot 指令；
- 保留媒体占位和结构化信息；
- 被撤回消息默认仍不显示，除非未来增加单独的审计导出模式。

### 4.3 不再提供采集过滤

第一阶段不提供以下采集开关：

- outside；
- command；
- bot；
- media；
- forum_code。

这避免设置错误导致无法恢复的数据丢失。

---

## 5. 导出器

定义独立 Exporter 接口，使格式扩展不进入 LogCommand：

```python
class LogExporter(Protocol):
    format_name: str

    async def export(
        self,
        session: LogSession,
        records: list[LogRecord],
        mode: str,
    ) -> ExportResult:
        ...
```

实现按优先级拆分：

```text
TextLogExporter       第一阶段
HtmlLogExporter       低优先级
DocxLogExporter       低优先级
```

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
<日志名>_群<group_id>_<log_id短码>_<导出时间>.txt
```

每次导出生成独立版本，不覆盖旧导出文件。

### 5.2 DOCX

DOCX 改为按需导出，不由 `.log end` 默认生成。

未来实现至少应包含：

- 消息时间；
- 清晰的发言分隔；
- 稳定的用户颜色；
- 回复引用样式；
- 图片尺寸限制；
- 下载失败占位符。

### 5.3 HTML

HTML 更适合富媒体日志，建议输出 ZIP：

```text
<日志名>.zip
  index.html
  assets/
    <hash>.jpg
    <hash>.png
```

优势：

- 支持图片和长图；
- 支持回复引用；
- 支持不同消息类型样式；
- 支持点击查看原图；
- 不依赖 Word 对 WebP、GIF 等格式的兼容性。

单文件 base64 HTML 可作为小日志选项，但不作为默认，避免大日志文件膨胀。

### 5.4 QQ 群文件

- `.log end` 默认上传 clean TXT。
- `.log export` 生成的文件也可以上传。
- 优先查找已有“跑团log”文件夹。
- 文件夹不存在时上传根目录。
- 第一阶段不要求自动创建文件夹。
- 上传失败不应回滚已经成功保存的日志状态。

---

## 6. 图片与附件预留

### 6.1 当前基础

项目已有：

- `core.command.cq_extractor.extract_segments()`；
- Persona 的异步图片下载与缓存实现；
- `httpx`；
- 内容 hash；
- 图片大小限制；
- `python-docx`；
- Jinja2。

因此保存图片技术上可行，但需要独立处理日志生命周期和磁盘容量。

### 6.2 第一阶段

第一阶段应做到：

- 保存 `raw_content`；
- 保存 `segments_json`；
- 图片段保存 URL、file、sub_type 等元数据；
- Exporter 接口能够识别图片段；
- 没有本地资源时输出 `[图片未归档]`。

第一阶段不要求下载图片二进制。

### 6.3 后续图片归档

QQ 图片 URL 可能过期。真正归档必须在收到消息后尽快异步下载，不能等到导出时。

建议增加：

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

后续需要：

- 内容 hash 去重；
- 单图片大小限制；
- 单日志附件数量或容量限制；
- 表情包默认是否归档；
- 删除日志后的无引用资源清理；
- MIME 校验；
- HTML 和 DOCX 嵌入策略。

可预留配置：

```json
{
  "log": {
    "archive_images": false,
    "max_image_size_mb": 10,
    "max_assets_per_log": 500
  }
}
```

默认关闭图片二进制归档。

---

## 7. 云端发布

现有 `.log end` 默认把群聊内容、QQ ID、昵称、时间和消息 ID 上传到外部服务。
zlib 只提供压缩，不提供内容加密。

目标行为：

```text
.log end
  -> 本地导出
  -> QQ 群文件
  -> 不访问第三方服务

.log publish <名称>
  -> 显式发布到云端

.log link <名称>
  -> 只读取已有链接
```

约束：

- `upload_enable` 默认值改为 false，或由新显式发布行为取代。
- `.log get` / `.log link` 不得产生上传副作用。
- 网络调用使用异步客户端或后台任务，不阻塞 Bot 事件循环。
- 单元与集成测试不得调用真实外部服务。
- 保存 provider、URL、发布时间和结果。
- `.log del` 明确提示远端副本不会自动删除。
- 后续只有在服务提供可靠删除 API 时才考虑 `.log unpublish`。

---

## 8. 统计与提醒

### 8.1 统计

第一阶段删除高级统计：

- `.log stat`；
- `.stat log`；
- 成功/失败；
- 大成功/大失败；
- 属性变化；
- 骰运；
- 活跃 TOP5；
- 内存 stats、participants、dice_faces 和 color_map。

`.log list` 中的消息数量通过数据库 `COUNT` 取得，不建立独立统计系统。

未来需要统计时，应消费 Roll 模块提供的结构化事件，不解析 Bot 文本。

### 8.2 提醒

删除每 1000 条玩家消息提醒。

连续记录时间提醒不是第一阶段阻塞项。若保留：

- 使用持久化时间；
- 提醒不进入 clean 日志；
- 阈值使用配置而非硬编码；
- 文字明确为连续录制时间；
- 不维护独立 session message count。

---

## 9. 消息撤回

撤回不能依赖当前日志。

目标流程：

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

第一阶段没有高级统计，因此撤回只需影响记录查询和导出。

已经生成或上传的文件不会自动更新。云端副本和群文件也不会自动删除，用户提示必须如实说明。

---

## 10. 权限

第一阶段保持现有命令权限，不新增：

- 日志创建者专属权限；
- 群管理员权限；
- 骰主权限；
- 发布确认权限。

日志会话可以保存 `created_by` 作为元数据，但不用于门控。

权限治理如果以后需要，应单独设计，不和本次数据迁移绑定。

---

## 11. 数据模型

目标数据库继续使用：

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

- 同群日志名称大小写不敏感唯一。
- 同群最多一份 `recording=true`。
- `current_log_id` 可以指向 recording 或 paused 日志。
- 不增加 closed。

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
log_id             TEXT NOT NULL
format             TEXT NOT NULL
mode               TEXT NOT NULL
created_at         TEXT NOT NULL
local_path         TEXT
group_file_name    TEXT
status             TEXT NOT NULL
note               TEXT
```

保存多次导出历史，不覆盖最近一次记录。

### 11.5 `log_publications`

云端发布落地时增加：

```text
id                 INTEGER PRIMARY KEY AUTOINCREMENT
log_id             TEXT NOT NULL
provider           TEXT NOT NULL
published_at       TEXT NOT NULL
url                TEXT
status             TEXT NOT NULL
note               TEXT
```

不要继续把本地导出和云端发布混在同一组 `upload_*` 字段中。

### 11.6 附件表

第一阶段可以只保留设计与 Repository 接口位置，图片归档实施时再创建
`log_assets` / `log_record_assets`。

如果第一阶段已经保存 `segments_json`，后续增加附件表不需要重写消息采集结构。

---

## 12. 模块边界

现有 `log_command.py` 同时负责：

- 命令解析；
- 生命周期；
- 内存状态；
- 数据库操作；
- 消息采集；
- 统计；
- 文件生成；
- QQ 群文件；
- 第三方 HTTP；
- 撤回；
- 兼容迁移。

目标拆分：

```text
module/common/log/
  command.py
  service.py
  recorder.py
  formatter.py
  exporters/
    base.py
    text.py
    html.py
    docx.py
  publisher.py
  media.py
```

职责：

| 模块 | 职责 |
|------|------|
| `LogCommand` | 解析指令并调用 Service |
| `LogService` | 生命周期、不变量、导出编排 |
| `LogRepository` | SQLite 查询与持久化 |
| `LogRecorder` | 入站/出站 Hook、消息结构化 |
| `LogExporter` | 格式生成 |
| `LogPublisher` | 显式第三方发布 |
| `LogMediaStore` | 后续图片下载、去重与清理 |

Command 不直接执行 SQL、HTTP 或文件格式细节。

---

## 13. 旧数据迁移

历史实现可能存在两类数据：

```text
旧 DataManager log_session payload
旧 DATA_PATH/log/log.db
```

新路径是：

```text
data/bots/<bot_id>/log.db
```

迁移风险：

- 旧同步数据库没有 bot ID，多个 Bot 共用时难以自动归属。
- DataManager 保存 current、filters、stats 等群级状态。
- SQLite 保存日志元数据和 records。
- 同一消息可能同时存在旧 payload 与旧数据库。
- 旧时间格式为 `YYYY/MM/DD HH:MM:SS`。
- 新模型使用 ISO datetime。
- 当前生产半迁移期间可能只存在空 session 或部分元数据。

实施前应先检查真实生产数据量，再选择：

1. 不迁移旧数据，只修复后续新日志；
2. 提供一次性只读导入工具；
3. 由用户指定旧数据库所属 Bot；
4. 仅迁移 records 和基础日志元数据，不迁移旧统计。

不建议在运行时长期保留 legacy fallback。

一次性导入需要：

- 转换时间格式；
- 指定 bot；
- 按 group + name/log ID 合并；
- 按 message ID 和内容去重；
- 不迁移旧统计；
- 输出导入摘要；
- 可重复执行或有明确幂等键。

---

## 14. 错误与事务边界

- 日志状态持久化失败时，不返回成功提示。
- 消息记录失败要有可检索日志，不得 `except Exception: pass`。
- `.log end` 先可靠停止并保存状态，再执行导出。
- 导出失败不能恢复成 recording，也不能丢失日志记录。
- QQ 群文件上传失败只影响该次 export 状态。
- 云端发布失败不影响本地日志和本地导出。
- 导出临时文件使用独立路径，成功后再登记 `log_exports`。
- 所有 async 数据库与网络调用由上游正确 await。

---

## 15. 验收场景

第一阶段至少覆盖以下真实调用链。

### 15.1 生命周期

```text
.log new 团A
玩家普通发言
玩家骰点指令
Bot 骰点回复
.log off
暂停期间发言
.log on
继续发言
.log end
```

验证：

- 开启期间消息完整；
- 暂停期间消息不记录；
- 玩家指令和 Bot 结果均存在；
- end 后当前日志为空；
- TXT 导出成功；
- 之后 `.log on 团A` 可以继续。

### 15.2 多日志

```text
.log new 团A
.log off
.log new 团B
.log on 团A
```

验证：

- 两份日志独立；
- 切换正确；
- 同群只能一份 recording；
- `.log list` 状态正确。

### 15.3 重启恢复

```text
.log new 团A
记录消息
重启 Bot
.log list
.log off
.log on
```

验证当前日志、recording 和 records 均能恢复。

### 15.4 clean/full

记录：

- `.log` 管理指令；
- 场外括号消息；
- 玩家骰点指令；
- Bot 骰点回复；
- 普通发言；
- @、reply、图片 CQ。

验证：

- clean 隐藏管理与纯场外；
- clean 保留骰点指令和结果；
- full 保留完整时间线；
- 两者不修改数据库记录。

### 15.5 撤回

- 在日志 A 记录消息。
- 切换到日志 B。
- 撤回日志 A 的消息。
- 导出日志 A。

验证撤回不依赖当前日志，导出不包含已撤回消息。

### 15.6 外部能力

- `.log end` 不调用第三方服务。
- `.log publish` 使用 mock HTTP。
- `.log link` 只读。
- QQ 上传失败时日志与本地导出仍存在。

### 15.7 图片预留

- 图片 CQ 被解析为 segment。
- `segments_json` 正确持久化。
- 没有下载资源时 TXT 输出占位符。
- 后续启用图片归档时不需要改变 records 基本结构。

---

## 16. 测试策略

### 16.1 Repository

- 群状态 CRUD；
- 同群名称唯一；
- 当前日志切换；
- records 插入、查询、撤回；
- 日志级联删除；
- export 历史。

### 16.2 Service

- new/on/off/end/halt；
- 多日志切换；
- recording 不变量；
- end 后仍可 on；
- 删除当前暂停日志；
- 导出失败隔离。

### 16.3 Recorder

- 普通消息；
- 已识别命令；
- Bot 回复；
- paused 跳过；
- log_control 标记；
- CQ segments；
- message_id。

### 16.4 Exporter

- clean/full；
- reply；
- at；
- 图片占位；
- 文件名安全；
- 不同群与多次导出不覆盖。

### 16.5 指令 E2E

使用 `dicepp-shell` 覆盖核心生命周期，不调用真实 QQ、真实云端服务或付费 API。

---

## 17. 实施顺序

1. 为 Log 功能建立正确的端到端失败测试。
2. 扩展 schema 与 `LogRepository`，覆盖群状态、按群/名称查询、records 和 exports。
3. 新建 `LogService`，实现 new/on/off/end/list/del 不变量。
4. 把玩家记录从低优先级 Command 迁到入站 Hook。
5. 保留并清理出站 Hook，统一写入 `LogRecorder`。
6. 实现 clean/full `TextLogExporter`。
7. 接入 `.log export` 和 QQ 群文件上传。
8. 实现撤回标记。
9. 删除旧同步裸函数调用、空 payload loader 和旧统计代码。
10. 添加 `.log set/stat/get/halt` 兼容响应。
11. 把云端发布拆成可选 `LogPublisher`。
12. 评估真实旧数据后决定是否提供一次性导入。
13. 低优先级实现图片归档、HTML、DOCX。

不要先恢复同步 bridge 再二次迁移。第一步应直接建立 async 端到端行为。

---

## 18. 完成标准

- `log_command.py` 不再引用已删除的同步 DB 函数。
- 所有 DB、昵称、网络和导出异步调用正确 await。
- Bot 重启后当前日志和 recording 状态正确恢复。
- 玩家骰点指令与 Bot 结果均被记录。
- `.log set` 不再永久丢弃消息。
- `.log end` 默认只进行本地/群文件导出。
- `.log link` 无副作用。
- 高级统计代码从核心路径移除。
- 图片消息至少能持久化结构化 segment。
- 核心生命周期拥有一条真实 E2E 测试。
- 旧数据处理策略在实施前有明确结论。
