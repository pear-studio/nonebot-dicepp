---
name: version-release
description: 在开发环境创建 DicePP 可部署版本。当用户要求发版、创建 release、上线前准备、递增版本、打 tag 或构建生产版本时使用；产出供生产 version-deploy 使用的 vX.Y.Z release。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# Version Release

在开发环境创建可部署的 DicePP 版本。该技能负责把代码状态固化为 `vX.Y.Z` release, 并产出生产部署所需的 release metadata。

## 适用场景

- 用户要求发版、创建 release、准备上线、递增版本号、打 tag 或构建生产版本。
- 用户要求把已合并到 `master` 的改动发布为新的 `vX.Y.Z` 版本。
- 不用于生产环境部署或回退；生产环境使用 `version-deploy`。

## 核心约定

- `pyproject.toml` 的 `[project].version` 是唯一手工维护的项目版本源。
- `bump-my-version` 默认 `commit = false`、`tag = false`：版本文件修改、`uv.lock`
  和 release notes 必须先组成一个可审查的 release commit，不能产生缺少 lockfile
  或 metadata 的自动 commit。
- Git tag 使用 `vX.Y.Z`，但本地版本递增和候选构建都不得创建或推送 tag。
  `.github/workflows/release.yml` 先建立 draft Release、上传并复验所有 assets、
  晋升版本镜像，最后发布 Release 时才创建 tag。
- 发版测试可使用 `vX.Y.ZrcN` 预发布版本（如 `v3.0.1rc1`）；RC 走同一候选与
  晋升链，创建 GitHub Prerelease 和同名镜像 tag，但不会更新 `latest`。
- `uv.lock` 必须与 `pyproject.toml` 的项目版本同步；tag 指向的 release commit 内不得出现 `pyproject.toml` 为新版本、`uv.lock` 仍记录旧 `dicepp` 版本的状态。
- Final Candidate workflow 会生成 `DicePP-vX.Y.Z-linux-amd64.zip`，内含 Linux
  amd64 Docker 镜像、`docker-compose.yml`、包内 `checksums.sha256` 和常用
  文档，用于国内或离线环境通过 `docker load` 导入镜像；兼容版本的 Manager
  自动安装也只使用该本地 image archive，不依赖 `docker pull`。
- Windows onedir 由 Velopack 包装为 Portable、Setup 和单一
  `velopack.win-x64.zip` 更新 bundle，不得包含 `DicePP-UpdateGuard.exe`。
  恢复入口是源 Manager 在具体升级事务切换前写入实例根的一次性
  `DicePP-Recover.cmd`，不是独立 Release asset 或常驻恢复进程。
- `.bot` / help / DiceHub 展示的运行版本应从已安装包版本派生, 不维护独立硬编码版本号。
- 生产更新风险摘要的唯一源头是 `docs/releases/vX.Y.Z.md`。GitHub Release body 以该文件为准；发布 workflow 不把该文件作为 release asset 上传。
- 日常发布只处理版本递增。补建当前版本基线属于一次性迁移/修复操作, 需用户明确要求后参考本技能的检查边界手工处理。
- `自动升级` 表示上一兼容 Release 的标准实例能否由现有 Manager 在 Dashboard
  经管理员确认后完成托管升级，不表示无人值守安装。`no` 只用于版本按设计仍可发布、
  但必须在 Dashboard 之外手工部署的兼容性边界；不得用来绕过候选构建、升级矩阵、
  恢复/回退或证据失败。
- 计划填写 `yes` 时，升级协议 registry 必须全部就绪；Final Candidate 必须产出与
  当前 commit、候选身份和最终发布字节绑定的 Windows/Linux 跨版本矩阵证据。
  任一验证失败、结果缺失或身份不匹配都阻止发布或晋升，修复后重新生成候选，
  不得由 agent 自动把同一 Release 降级为 `no`。

## Preconditions

- 只在开发环境中使用。
- Git 仓库初始化完成, 且有远程仓库写入权限。
- `bump-my-version` 已安装, 可通过 `uv sync --group dev` 安装。
- `uv lock` 可用且不会降级 lockfile 格式；如果本机 PATH 上的 `uv` 版本过旧或来自无关环境, 先更新或显式使用可保留当前 `uv.lock` `revision` 的 uv。
- 目标分支为 `master` 的最新状态。
- 工作区必须干净；存在未提交更改时拒绝执行 release。
- 首次真实晋升前，管理员已经完成 `docs/releases/README.md` 的三项一次性配置：
  Immutable Releases、禁止 update/deletion 但允许 workflow 创建新 tag 的 `v*` tag
  ruleset，以及两个 GHCR package 对本仓库的 Write access。
- 除 workflow 自动获得的 `GITHUB_TOKEN` 外，发布不要求额外凭据、管理员设置 ID
  变量或人工审批门禁，也不在每次 Promotion 中重复读取管理员设置。

## Release Metadata

每个 release 必须包含 `docs/releases/vX.Y.Z.md`。格式如下：

```markdown
# vX.Y.Z

- 数据变更: no
- 配置变更: no
- 变更范围: runtime, dashboard, deployment
- 自动升级: yes/no
- 最低 Manager 版本: 1.0

## Added
- 新增的功能

## Changed
- 行为调整

## Fixed
- Bug 修复

## Deprecated
- 废弃或移除的内容

## Risk Notes
- 升级注意事项，如数据迁移步骤、配置变更细节、手动操作等
```

### Manager 自动升级兼容性声明

`version-release` 只生成并验证 Release contract，不执行生产部署、不直接调用 Manager，也不替代 `version-deploy`。

- `yes` 是日常兼容 Release 的默认目标：当前标准 Manager 能对上一兼容 Release 的
  标准实例完成发现、下载、归档、安装和健康检查；Linux 失败时自动回退，Windows
  生成可用的一次性人工恢复入口。管理员仍需在 Dashboard 中确认安装。
- 普通 Runtime、Dashboard、Manager 或向下兼容配置变化不构成 `no`。目标包含
  Manager 新代码时，只要源 Manager 满足 `最低 Manager 版本`，Linux 声明受支持的
  handoff 协议，并通过双平台真实候选矩阵，就可以填写 `yes`。
- `no` 只用于有意发布的手工部署版本，例如：源 Manager 的缺陷使其无法获得自身
  修复；Compose service/volume/network、deployment schema、RuntimeUnit、安装布局
  或 handoff 协议发生现有事务无法处理的破坏性变化；或开发者明确决定采用 Manager
  无法安全完成的数据/配置人工迁移。
- 以数据或配置必须人工迁移为由判定 `no` 时，agent 不得自行决定。必须先说明
  Manager 为什么不能处理、影响哪些标准部署、人工步骤及可选兼容方案，然后暂停，
  在初次风险确认之外取得开发者单独且明确的二次确认；没有二次确认时继续以 `yes`
  为目标解决兼容问题，或将发布视为阻塞。
- 构建失败、产物缺失、摘要或身份不一致、升级矩阵失败、恢复/回退失败、证据缺失
  都是发布阻塞，不是填写 `no` 的理由。不得通过改成 `no` 绕过失败；若要把版本重新
  定义为手工部署版本，必须说明独立的设计兼容性理由、重新确认 metadata 并重建候选。

字段含义：

- `数据变更`: 是否影响 `data/`、数据库 schema、持久化数据结构或需要执行迁移脚本。
- `配置变更`: 是否影响运行环境变量、`config/`、配置 schema 或配置加载行为。
- `变更范围`: 逗号分隔的实际变更域；必须显式声明 `data` / `config`，并与
  前两个风险字段完全一致，否则发布会 fail closed。
- `自动升级`: 表示上一兼容 Release 的标准实例能否由现有 Manager 在 Dashboard
  经管理员确认后完成一次托管升级；`no` 会使 Dashboard 将 Release 标记为不兼容，
  禁止下载和安装，只保留外部手工部署路径。
- `变更范围` 包含 `manager` 时，Linux Release 必须声明当前支持的 Manager handoff
  协议并通过双平台候选矩阵；Manager 代码变化本身不强制 `no`。
- `最低 Manager 版本`: 能理解本次发布契约并执行安装事务的源 Manager 能力下限。
  目标版本包含新的 Manager 不等于源 Manager 不满足该下限。

- `Added / Changed / Fixed / Deprecated`: 面向所有用户的 changelog。
- `Risk Notes`: 面向部署者的详细风险说明。如包含数据迁移，在此写明迁移脚本路径和执行方式。

当任一风险字段无法确认时，`version-deploy` 按最坏情况处理：要求备份 + 用户明确确认。

### Release notes 受众与筛选规则

`docs/releases/vX.Y.Z.md` 是用户和部署者会看到的公开 release notes，不是开发流水账。编写时必须先从 diff/commit 中提炼“用户可见变化”和“部署风险”，再决定是否写入。

#### Comparison base

- RC release notes 默认以同目标版本的前一个 RC 为 comparison base；如果不存在前一个 RC，则以当前发布周期的前一个正式版本为 comparison base。
- 正式版 release notes 必须以前一个正式版本为 comparison base。生成时可参考期间的 RC release notes 和 commit log，但最终内容仍按用户/部署者可见变化重新整理，不机械复制 RC 内容。

必须写入：

- 新增或明显改变用户可操作能力，例如 Dashboard、Windows EXE、Linux Docker 部署、配置管理、Bot 行为、命令行为、数据迁移、权限/初始化流程。
- 影响部署、升级、回滚、备份、安全边界或兼容性的变化。
- 修复用户可能实际遇到的故障；描述故障现象和影响，不描述测试名或实现过程。

默认不要写入：

- 测试、mock、fixture、覆盖率、Playwright smoke、CI/CD job、构建缓存、workflow 重排等开发门禁细节。
- Agent / AI Skill / review 流程 / backlog / handoff / 内部协作工具变更。
- 仅为让 CI 通过的测试同步、测试夹具修正、内部重构、私有 API 清理。

例外：如果开发基础设施变化会直接改变用户获取产物或部署产物的方式，可以把“结果”写入 release notes，但不要写实现细节。例如：

- 好：`Windows 发布包现在包含 Dashboard 可执行文件。`
- 坏：`新增 Windows Playwright smoke 测试。`
- 好：`Dashboard 现在支持独立 Docker 镜像部署。`
- 坏：`新增 Dashboard 镜像控制通道 smoke test。`
- 好：`Linux Dashboard 首次初始化需通过命令行设置管理员密码。`
- 坏：`测试通过 route mock 覆盖 setup 表单校验。`

测试、CI、技能或内部流程内容若需要记录，应写在发版完成后给维护者的对话汇报中，作为“验证与内部处理”小节；也可在必要时写入开发文档/backlog，但不要写入 release metadata。

## Steps

1. **检查工作区状态**

   运行：

   ```bash
   git status --short
   ```

   如果有任何输出, 拒绝执行 release, 列出未提交文件并要求用户先提交或清理。

2. **记录当前分支**

   运行：

   ```bash
   git branch --show-current
   ```

   保存当前分支, 用于结束后切回。

3. **切换并同步 `master`**

   如果当前分支不是 `master`, 切换到 `master`。

   ```bash
   git checkout master
   git fetch origin master --tags
   git log HEAD..origin/master --oneline
   ```

   如果本地 `master` 落后于远程, 执行：

   ```bash
   git pull origin master
   ```

4. **读取当前版本和可递增选项**

   优先运行：

   ```bash
   uv run bump-my-version show-bump
   ```

   或从 `pyproject.toml` 解析当前版本。

5. **让用户选择递增级别**

   向用户展示并等待明确选择：

   - `patch`: bugfix / 小修补, 如 `3.0.0` -> `3.0.1`
   - `minor`: 向下兼容的新功能, 如 `3.0.0` -> `3.1.0`
   - `major`: 破坏性变更, 如 `3.0.0` -> `4.0.0`

6. **计算目标版本并准备 metadata**

   根据用户选择计算 `new_version` 和 `v{new_version}`。

   在执行 bump 前创建或检查：

   ```text
   docs/releases/v{new_version}.md
   ```

   要求用户确认 `数据变更`、`配置变更`、`变更范围`、`自动升级` 和
   `最低 Manager 版本`。可通过 diff 辅助判断，但不能把不确定风险默认为安全。
   Release workflow 会严格解析这些字段；缺失、重复、值非法或版本不一致时
   直接拒绝发布。

   如果初步分析认为数据或配置必须人工迁移、因而准备把 `自动升级` 写为 `no`，
   必须执行独立的二次确认流程：第一轮只展示不能由 Manager 处理的具体原因、受影响
   标准部署、人工步骤和可选兼容方案，不写入 metadata；开发者确认已理解后，再单独
   询问“是否确认将本版本定义为手工部署版本并填写 `自动升级: no`”。只有第二次得到
   明确肯定答复后才能写入；含糊答复、一般性发版授权或首次风险确认都不算二次确认。

   编写 metadata 前必须先列出本次 release 的用户可见主题，至少回答：

   - 用户现在能做什么以前不能做的事？
   - 现有部署/初始化/配置/运行方式有什么变化？
   - 哪些 bug 是用户实际可能遇到的，修复后现象如何变化？
   - 哪些只是测试、CI/CD、agent skill、review/backlog 或内部重构，应该从公开 release notes 中过滤掉？

   如果一项改动只能表述为“新增测试/CI/Skill/内部工具”，通常不要写入 `Added / Changed / Fixed`。把它保留给发版完成后的维护者汇报。

7. **执行版本递增并创建无 tag 的 release commit**

   运行：

   ```bash
   uv run bump-my-version bump --no-commit --no-tag <patch|minor|major>
   ```

   成功后确认：

   - `pyproject.toml` 已更新到新版本。
   - `uv.lock` 已同步到同一版本。运行：
     ```bash
     uv lock
     ```
     然后确认 diff 至少包含 `uv.lock` 中 `name = "dicepp"` 对应的 `version = "{new_version}"`，且没有不合理的大范围 lockfile 格式降级。如果本机 `uv` 会把 `revision` 写回旧值，先换用更新的 uv 再重新执行。
   - 将 `pyproject.toml`、`uv.lock`、release metadata 和本次发布所需的其他改动
     放入同一个 release commit：
     ```bash
     git add pyproject.toml uv.lock docs/releases/v{new_version}.md
     git commit -m "Bump version: {old_version} → {new_version}"
     ```
     提交后再次确认 `git show HEAD:uv.lock` 中 `dicepp` 版本等于 `{new_version}`。
   - release metadata 文件包含在版本 bump commit 中。
   - 本地和远端都还不存在 `v{new_version}` tag；若已存在，停止并调查，不删除、
     强制移动或覆盖它。

8. **验证并只推送 release commit**

   推送前必须在最终 release commit（即当前 HEAD）上成功运行完整离线回归：

   ```bash
   uv run pytest
   ```

   只有本次会话已在同一 HEAD 上成功运行且之后没有代码、配置或测试改动时可以复用结果。失败或未完成时不得推送 release commit。

   运行：

   ```bash
   git push origin master
   ```

   如果 push 失败，汇报错误和 release commit 状态，提醒用户处理；仍执行切回原分支。

9. **生成并确认不可变 Final Candidate**

   从 `master` 的精确 release commit 手工 dispatch candidate workflow：

   ```bash
   COMMIT_SHA=$(git rev-parse HEAD)
   gh workflow run candidate.yml --ref master \
     -f version={new_version} \
     -f commit_sha=${COMMIT_SHA}
   ```

   等待 run 成功；记录 Actions 展示的 `candidate_run_id`、`run_attempt`、
   `candidate_artifact_id`、artifact digest 和 URL。候选必须来自当前仓库的
   `.github/workflows/candidate.yml`、`workflow_dispatch`、`master`，其
   `head_sha` 必须等于 release commit。artifact 名必须是
   `dicepp-final-candidate-{run_id}-{run_attempt}`，并包含 receipt 及 receipt
   逐文件摘要声明的全部最终 Release 原字节。

   `自动升级: yes` 候选必须先确认升级协议 registry 全部就绪，并验证与该 commit、
   candidate identities 和最终发布字节绑定的 `dicepp-upgrade-evidence.json`；
   缺失、不完整、不匹配，或 Windows/Linux 升级、恢复、回退任一失败时，候选失败且
   不得晋升；修复后重新生成候选，不得降级 metadata 绕过。`automatic_upgrade: no`
   的 validation-only 矩阵只用于事先明确的手工部署版本验收，不得进入 Receipt 或
   Release assets。

   Candidate artifact 只保留 30 天，且 Promotion 要求它的 `head_sha` 仍等于
   当前 default branch HEAD。必须在 master 再次前进之前尽快晋升，30 天只是
   最长上限；过期或失去 HEAD 身份后重新生成 candidate，不延长或复制旧 artifact。

10. **显式晋升同一批候选字节**

   使用上一步明确记录的 run ID 和 artifact ID dispatch promotion：

   ```bash
   gh workflow run release.yml --ref master \
     -f version={new_version} \
     -f candidate_run_id={candidate_run_id} \
     -f candidate_artifact_id={candidate_artifact_id}
   ```

   Promotion 会在任何外部写入前验证 run/workflow/event/conclusion/head SHA/
   run attempt、artifact 归属与外层 digest、receipt closed schema、逐文件 size/SHA-256、
   自动升级证据和容器 manifest/Image ID。
   验证通过后先创建 draft Release，上传并复验候选原字节，再以 manifest digest
   晋升版本镜像；publish 紧邻时点重新验证 HEAD/tag/draft metadata/assets/digests/
   候选身份，发布 Release 时才创建 tag，正式版最后再次
   校验 HEAD 与 GitHub latest Release 后更新 `latest`。Promotion 全局串行，不按版本
   并发。它不会重新构建、执行 `vpk`、`docker save` 或重新压缩发布包。

   已有 tag、Release 或 GHCR tag 的身份、目标 commit、metadata/assets 或 digest 任一
   不一致时立即停止。精确同身份的中断 draft、已上传资产或同 digest 镜像仅为失败恢复
   允许幂等续传或复核成功，不覆盖或改写已发布资源。
   当前不自动清理候选 GHCR 镜像。

11. **验证发布资源可用**

   等待 Promotion run 完成后，确认：

   - `gh release view v{new_version}` 返回 release 信息。
   - Release assets 包含:
     - `docker-compose.yml`
     - `DicePP-v{new_version}-win64-Portable.zip`
     - `DicePP-v{new_version}-win64-Setup.exe`
     - `DicePP-v{new_version}-linux-amd64.zip`
     - `velopack.win-x64.zip`
     - `dicepp-release.json`
     - `dicepp-candidate.json`
     - 声明 `自动升级: yes` 时的 `dicepp-upgrade-evidence.json`
   - Windows Portable 解压后包含 `DicePP.exe` 和 `DicePP-Runtime.exe`，
     且不包含 `DicePP-UpdateGuard.exe`；Setup 安装后的程序目录具备
     相同入口与无 Guard 边界。
   - 目标 tag 下部署文档可读: `git show v{new_version}:docs/linux.md` 不报错。
   - GHCR 镜像 tag 存在: `docker pull ghcr.io/pear-studio/nonebot-dicepp:v{new_version}` 不报错。
   - Linux 发布包下载后可解压，包内 `checksums.sha256` 存在且可用于校验内部
     文件；`dicepp-package.json` 的 Compose 与当前发布拓扑一致，image archive
     可以 `docker load`，加载后的 immutable Image ID 与 manifest 声明一致，
     并可用 `docker compose up -d --pull never` 启动。
     GitHub Release asset digest 可作为外层 zip 的来源校验参考。
   - 对声明 `自动升级: yes` 的候选，在隔离测试目录执行一次真实 Windows
     Portable/Setup 首装 → Velopack 升级 → 健康提交，确认成功后旧
     `current/` 备份与恢复入口已清理；故障注入必须在目标 `current/`
     缺失或损坏时，由根 `DicePP-Recover.cmd` 换回旧程序、恢复
     pre-upgrade 数据和 RuntimeUnit 原状态。Linux 在临时 Compose project 中验证 bundle load、
     `--pull never` 切换和旧镜像回退。若当次发布没有完成这些平台烟测，不得把
     单元测试结果表述为“真实自动升级已验证”，应把未验边界写入发版摘要。
   - Release asset digest 必须与 `dicepp-candidate.json` 中相应文件的 SHA-256
     一致；GHCR 版本 tag 的 manifest digest 必须与 receipt 一致。
   - 如任一产物缺失，查看 Final Candidate 与 Promotion run 日志排查；不要手工
     补传、覆盖 asset 或移动 tag。

   GitHub Release 与 GHCR 是当前唯一发布目标。当前不做 Gitee 镜像同步，恢复需单独设计并经用户确认。

12. **切回原分支**

   如果发布前不在 `master`, 切回原分支。

13. **生成发版摘要**

    汇报：

   ```text
   版本: X.Y.Z -> A.B.C
   Tag: vA.B.C
   Release metadata: docs/releases/vA.B.C.md
   镜像: ghcr.io/pear-studio/nonebot-dicepp:vA.B.C
   Windows Portable: DicePP-vA.B.C-win64-Portable.zip
   Windows Setup: DicePP-vA.B.C-win64-Setup.exe
   Linux 发布包: DicePP-vA.B.C-linux-amd64.zip
   数据变更: yes/no
   配置变更: yes/no
   Commit 推送: 成功/失败
   Candidate run / artifact: <run ID> / <artifact ID + digest>
   Promotion run: <run URL>
   ```

## Baseline / Repair Notes

如果用户明确要求把当前 `pyproject.toml` 版本补建为发布基线, 不执行版本递增。执行以下步骤：

1. 确认当前版本号与目标 tag 一致。
2. 确认 `docs/releases/vX.Y.Z.md` 已存在且内容完整。
3. 确认 `.bot` 运行版本与包版本一致。
4. 确认 Final Candidate 与 Promotion workflow 及 `.dockerignore` 已准备好。
5. 确认工作区干净, 所有改动已提交到 master, 当前 commit 是想要固化的基线 commit。
6. 在当前 HEAD 上运行 `uv run pytest`。只有本次会话已在同一 HEAD 上成功运行且
   之后没有代码、配置或测试改动时可以复用；失败或未完成时不得继续。
7. 只推送 commit，然后按本技能 step 9–11 生成候选并以显式 run ID + artifact ID
   晋升；不得手工创建、强制移动或推送 tag。

## RC / Prerelease Test Notes

当用户要求先验证发版链路时, 优先使用 RC 预发布版本：

1. 选择目标正式版本作为基底；如果 `3.0.0` 尚未正式发布, 测试版从 `3.0.0rc1` 开始；已有正式版后再使用下一个版本的 RC。
2. 将 `pyproject.toml` 版本更新为目标 RC 版本, 并准备对应的 `docs/releases/vX.Y.ZrcN.md`。
3. 运行 `uv lock`, 并确认 `uv.lock` 中 `dicepp` 版本等于目标 RC 版本；把 `pyproject.toml`、`uv.lock` 和 `docs/releases/vX.Y.ZrcN.md` 提交到同一个 RC release commit。
4. 在最终 RC commit（当前 HEAD）上运行 `uv run pytest`。只有本次会话已在同一
   HEAD 上成功运行且之后没有代码、配置或测试改动时可以复用；失败或未完成时
   不得继续。
5. 只推送 RC commit，按本技能 step 9–11 先生成不可变候选，再用显式 run ID +
   artifact ID 晋升；不得手工创建或推送 RC tag。
6. Final Candidate workflow 构建 Docker 镜像和 Windows EXE，运行版本一致性检查和冒烟测试。
7. Promotion 只推送 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.ZrcN`，不更新 `latest`。
8. GitHub Release 标记为 Prerelease。

RC 测试通过后, 正式发布仍使用纯数字版本 `vX.Y.Z`。

## Important Notes

- 工作区有未提交更改时直接拒绝, 不自动 stash。
- release metadata 必须先于 bump 创建，保证 candidate commit 内能读取
  `docs/releases/vX.Y.Z.md`。
- 任何人工强制移动/推送版本 tag 或覆盖 Release asset 都不属于发版流程。
- 不在开发环境部署生产；生产更新或回退使用 `version-deploy`。
- 不自动调用真实 LLM、外部 API 或付费服务。
