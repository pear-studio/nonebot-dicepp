# Manager 归档与恢复

第三批完成后，归档仓库和恢复事务只属于 Manager。Dashboard 保留原有
`/api/archives` 用户 API 外形，但所有请求都代理到 Manager；它不再枚举
DataAsset、不打开 ZIP，也不直接写实例数据。

## 归档范围

- `regular` 是默认范围：配置与 Catalog 管理的 `data/`，不读取、修改或删除
  `content/`。
- `full` 在 regular 基础上加入整个用户 `content/`。大型内容会延长 RuntimeUnit
  停机时间，Dashboard 在创建前显示输入大小、文件数和 Manager 存档卷可用空间。
- `manual` 归档永不被保留策略自动删除；事务生成的 `system` 安全归档默认保留
  最近 5 份，活跃或失败事务引用的归档受保护。

归档位于 `<instance>/manager/backups/`。创建时 Manager 取得实例维护锁，记录并
停止原先运行的 RuntimeUnit，直接流式写入 `*.zip.inprogress`，同遍计算摘要，
完整验证后原子发布，最后只恢复原先运行的单元。不会建立第二份原始数据快照目录。

## format v2

`manifest.json` 记录 profile、DicePP 版本、来源平台、DataAsset Catalog 描述及
摘要、每个 DataAsset 的 schema 与敏感标记，以及每个文件的逻辑路径、大小和
SHA-256。配置资产被标为敏感，因为可能包含 API Key。

读取时先检查 ZIP 结构，再在 2 MiB 上限内读取 manifest；路径穿越、反斜杠路径、
重复 member、加密或未知压缩方式、symlink/special member、超限 member、异常
压缩比、未声明或摘要不一致的 payload 都会被拒绝。来源平台只用于展示，Windows
与 Linux 使用同一逻辑路径。

Catalog 摘要用于诊断，不要求新旧版本逐字一致。v2 按归档中实际声明的资产逐项
检查：当前版本不认识的资产、schema 身份变化或归档 schema 更新时会阻止恢复；
当前版本新增、但旧归档没有声明的资产会保留，不会被精确删除。`format_version=1`
被保守解释为 regular，并按旧 manifest 的 `scope.included` 决定精确同步范围；
其中 SQLite 会在预览时只读检查 `schema_metadata`，不允许用旧程序恢复由新程序
写出的 schema。

## 精确恢复事务

恢复预览返回 `create`、`overwrite`、`remove`、`blocked`。remove 只来自归档
已声明的资产：regular 永远不会触碰 content；full 会把归档当时声明的 content
精确同步到归档状态，同时保留当前版本后来新增且旧归档不知道的资产。

用户确认后，Manager：

1. 取得维护锁并暂停正在运行的 RuntimeUnit；
2. 以相同 profile 创建、验证 system pre-restore；
3. 写入持久 journal，并把 `data_switch_started` 作为数据切换提交边界；
4. 逐文件临时写入、fsync、原子替换，然后执行精确删除；
5. 调用现有 `SchemaTarget` 事实来源执行 forward migration；
6. 启动原先运行的单元，以有界重试和连续成功窗口检查 Manager store、配置、
   schema、独立 Dashboard `/api/health` 端点、RuntimeUnit 存活和该端点报告的
   Bot 控制心跳；控制心跳必须晚于本次启动前记录的基线，Manager 不直接读取
   Dashboard 数据库；
7. 健康通过后写入 `health_passed` 提交点并收尾。

任一步失败都会应用 pre-restore、再次执行 schema/健康检查并恢复原运行状态。
Manager 重启时，提交点之前的未切换事务会清理临时文件并恢复事务开始前的运行
状态；已经开始数据切换但尚未健康提交的事务自动完整回退；已写入健康标记的事务
完成提交收尾。回退失败会保留 `rollback_failed` journal 与其安全归档，下一次
Manager 启动继续重试。journal 与原 ManagerOperation 会同步收口。

NapCat、QQ、GitHub、LLM 等外部依赖只形成 warning，不参与硬回退判断。

## 导入与导出

- 导出从 Manager 读取并下载现有、已验证的 ZIP。
- 导入流式上传到 Manager 安全临时文件，经过大小、ZIP 结构、Catalog 与摘要校验
  后原子加入列表。
- 导入不会自动恢复，仍须预览和显式确认。

Manager 私有 API 返回持久 `operation_id`；Dashboard 刷新或短暂断线后会从
operation 列表重新发现正在执行的归档操作，再通过
`/api/manager/operations/{operation_id}` 继续查询。
