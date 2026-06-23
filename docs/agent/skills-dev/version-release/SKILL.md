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
- Git tag 使用 `vX.Y.Z`, 由 `bump-my-version` 根据新版本号创建。
- 发版测试可使用 `vX.Y.ZrcN` 预发布 tag（如 `v3.0.1rc1`）；RC 会创建 GitHub Prerelease 并推送同名镜像 tag，但不会更新 `latest`。
- `.bot` / help / DiceHub 展示的运行版本应从已安装包版本派生, 不维护独立硬编码版本号。
- 生产更新风险摘要的唯一源头是 `docs/releases/vX.Y.Z.md`。未来 GitHub Release body 以该文件为准生成或同步。
- 日常发布只处理版本递增。补建当前版本基线属于一次性迁移/修复操作, 需用户明确要求后参考本技能的检查边界手工处理。

## Preconditions

- 只在开发环境中使用。
- Git 仓库初始化完成, 且有远程仓库写入权限。
- `bump-my-version` 已安装, 可通过 `uv sync --group dev` 安装。
- 目标分支为 `master` 的最新状态。
- 工作区必须干净；存在未提交更改时拒绝执行 release。

## Release Metadata

每个 release 必须包含 `docs/releases/vX.Y.Z.md`。格式如下：

```markdown
# vX.Y.Z

- 镜像: ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z
- Windows: DicePP-vX.Y.Z-win64.zip
- 数据变更: yes/no
- 配置变更: yes/no

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

字段含义：

- `镜像`: 生产部署使用的 GHCR 镜像 tag。
- `数据变更`: 是否影响 `data/`、数据库 schema、持久化数据结构或需要执行迁移脚本。
- `配置变更`: 是否影响运行环境变量、`config/`、配置 schema 或配置加载行为。
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

   要求用户确认 `数据变更` 和 `配置变更`。可通过 diff 辅助判断, 但不能把不确定风险默认为安全。

   编写 metadata 前必须先列出本次 release 的用户可见主题，至少回答：

   - 用户现在能做什么以前不能做的事？
   - 现有部署/初始化/配置/运行方式有什么变化？
   - 哪些 bug 是用户实际可能遇到的，修复后现象如何变化？
   - 哪些只是测试、CI/CD、agent skill、review/backlog 或内部重构，应该从公开 release notes 中过滤掉？

   如果一项改动只能表述为“新增测试/CI/Skill/内部工具”，通常不要写入 `Added / Changed / Fixed`。把它保留给发版完成后的维护者汇报。

7. **执行版本递增**

   运行：

   ```bash
   uv run bump-my-version bump <patch|minor|major>
   ```

   成功后确认：

   - `pyproject.toml` 已更新到新版本。
   - release metadata 文件包含在版本 bump commit 中。
   - Git tag 为 `v{new_version}`。

8. **推送 release commit 与 tag**

   运行：

   ```bash
   git push origin master --tags
   ```

   如果 push 失败, 汇报错误和本地 commit/tag 状态, 提醒用户处理；仍执行切回原分支。

   push 成功后, GitHub Actions (release.yml) 将自动：
   - 构建并推送 GHCR 镜像 (:vX.Y.Z + :latest)，运行冒烟测试
   - 在 Windows 上构建 DicePP EXE，运行冒烟测试
   - 将 EXE 打包为 DicePP-vX.Y.Z-win64.zip
   - 创建 GitHub Release（body 为 docs/releases/vX.Y.Z.md 内容）
   - 上传 docker-compose.yml 和 DicePP-vX.Y.Z-win64.zip 作为 release assets

9. **验证构建产物可用**

   等待 GitHub Actions 完成后, 确认：

   - `gh release view v{new_version}` 返回 release 信息。
   - Release assets 包含:
     - `docker-compose.yml`
     - `DicePP-v{new_version}-win64.zip`
   - 目标 tag 下部署文档可读: `git show v{new_version}:docs/linux.md` 不报错。
   - GHCR 镜像 tag 存在: `docker pull ghcr.io/pear-studio/nonebot-dicepp:v{new_version}` 不报错。
   - 如任一产物缺失, 查看对应 GHA run 日志排查。

10. **切回原分支**

   如果发布前不在 `master`, 切回原分支。

11. **生成发版摘要**

    汇报：

   ```text
   版本: X.Y.Z -> A.B.C
   Tag: vA.B.C
   Release metadata: docs/releases/vA.B.C.md
   镜像: ghcr.io/pear-studio/nonebot-dicepp:vA.B.C
   Windows EXE: DicePP-vA.B.C-win64.zip
   数据变更: yes/no
   配置变更: yes/no
   推送: 成功/失败
   GitHub Actions: <run URL>
   ```

## Baseline / Repair Notes

如果用户明确要求把当前 `pyproject.toml` 版本补建为发布基线, 不执行版本递增。执行以下步骤：

1. 确认当前版本号与目标 tag 一致。
2. 确认 `docs/releases/vX.Y.Z.md` 已存在且内容完整。
3. 确认 `.bot` 运行版本与包版本一致。
4. 确认 GHCR workflow (release.yml) 与 `.dockerignore` 已准备好。
5. 确认工作区干净, 所有改动已提交到 master, 当前 commit 是想要固化的基线 commit。
6. 手工创建并推送 tag:
   ```bash
   git tag vX.Y.Z
   git push origin master --tags
   ```
7. 等待 GitHub Actions 完成, 验证镜像和 GitHub Release。参考本技能 step 9。

## RC / Prerelease Test Notes

当用户要求先验证发版链路时, 优先使用 RC 预发布版本：

1. 选择目标正式版本作为基底；如果 `3.0.0` 尚未正式发布, 测试版从 `3.0.0rc1` 开始；已有正式版后再使用下一个版本的 RC。
2. 将 `pyproject.toml` 版本更新为目标 RC 版本, 并准备对应的 `docs/releases/vX.Y.ZrcN.md`。
3. 创建并推送 tag:
   ```bash
   git tag vX.Y.ZrcN
   git push origin master --tags
   ```
4. GitHub Actions 会构建 Docker 镜像和 Windows EXE, 运行版本一致性检查和冒烟测试。
5. RC 发布只推送 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.ZrcN`, 不更新 `:latest`。
6. GitHub Release 会标记为 Prerelease。

RC 测试通过后, 正式发布仍使用纯数字版本 `vX.Y.Z`。

## Important Notes

- 工作区有未提交更改时直接拒绝, 不自动 stash。
- release metadata 必须先于 bump 创建, 保证 tag 指向的 commit 内能读取 `docs/releases/vX.Y.Z.md`。
- 不在开发环境部署生产；生产更新或回退使用 `version-deploy`。
- 不自动调用真实 LLM、外部 API 或付费服务。
