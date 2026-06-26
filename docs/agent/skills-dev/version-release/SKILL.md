---
name: version-release
description: 在开发环境创建 DicePP 可部署版本。当用户要求发版、创建 release、上线前准备、递增版本、打 tag 或构建生产版本时使用；产出供生产 version-deploy 使用的 vX.Y.Z release。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# Version Release

在开发环境创建可部署的 DicePP 版本。该技能负责把代码状态固化为 `vX.Y.Z` release，并产出生产部署所需的 release metadata。

## 适用场景

- 用户要求发版、创建 release、准备上线、递增版本号、打 tag 或构建生产版本。
- 用户要求把已合并到 `master` 的改动发布为新的 `vX.Y.Z` 版本。
- 不用于生产环境部署或回退（使用 `version-deploy`）；不用于补建基线（使用 `REF-baseline.md`）；不用于 RC 预发布测试（使用 `REF-rc.md`）。

## 核心约定

- `pyproject.toml` 的 `[project].version` 是唯一手工维护的项目版本源。
- Git tag 使用 `vX.Y.Z`，由 `bump-my-version` 根据新版本号创建。
- 发版测试可使用 `vX.Y.ZrcN` 预发布 tag；RC 会创建 GitHub Prerelease 并推送同名镜像 tag，但不会更新 `latest`。
- `.bot` / help / DiceHub 展示的运行版本应从已安装包版本派生，不维护独立硬编码版本号。
- 生产更新风险摘要的唯一源头是 `docs/releases/vX.Y.Z.md`。GitHub Release body 以该文件为准生成。
- 日常发布只处理版本递增。补建当前版本基线属于一次性迁移/修复操作，需用户明确要求后参考 `REF-baseline.md` 手工处理。

## Preconditions

- 只在开发环境中使用。
- Git 仓库初始化完成，且有远程仓库写入权限。
- `bump-my-version` 已安装，可通过 `uv sync --group dev` 安装。
- 工作区必须干净：存在未提交更改时拒绝执行 release。

## Release Metadata

每个 release 必须包含 `docs/releases/vX.Y.Z.md`。格式如下：

```markdown
# vX.Y.Z

- 镜像: ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z
- Dashboard 镜像: ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z
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

- `镜像` / `Dashboard 镜像`: 生产部署使用的 GHCR 镜像 tag。
- `数据变更`: 是否影响 `data/`、数据库 schema、持久化数据结构或需要执行迁移脚本。
- `配置变更`: 是否影响运行环境变量、`config/`、配置 schema 或配置加载行为。
- `Added / Changed / Fixed / Deprecated`: 面向用户的 changelog。
- `Risk Notes`: 面向部署者的详细风险说明。如包含数据迁移，在此写明迁移脚本路径和执行方式。

当任一风险字段无法确认时，`version-deploy` 按最坏情况处理：要求备份 + 用户明确确认。

### 编写规则

`docs/releases/vX.Y.Z.md` 是用户和部署者会看到的公开 release notes，不是开发流水账。编写时先回答四个问题：

1. 用户现在能做什么以前不能做的事？
2. 现有部署/初始化/配置/运行方式有什么变化？
3. 哪些 bug 是用户实际可能遇到的，修复后现象如何变化？
4. 哪些只是测试、CI/CD、agent skill、review/backlog 或内部重构？

#### Comparison base

- RC release notes 以同目标版本的前一个 RC 为 comparison base；如无前一个 RC，以当前发布周期的前一个正式版本为 comparison base。
- **正式版 release notes 必须逐 commit 审核，不可仅合并 RC notes**。流程：
  1. `git log v{prev}..v{new} --oneline` 列出范围内所有 commit
  2. 逐条判断是否用户可见或影响部署，筛选出应写入的条目
  3. 按 Added / Changed / Fixed / Deprecated 归类
  4. 期间的 RC release notes 仅作为交叉参考，确认无遗漏
- 禁止跳过步骤 1 直接复制 RC notes 内容。

#### 必须写入

- 新增或明显改变用户可操作能力（Dashboard、Windows EXE、Linux 部署、配置管理、Bot 行为、命令行为、数据迁移、权限/初始化流程）。
- 影响部署、升级、回滚、备份、安全边界或兼容性的变化。
- 修复用户可能实际遇到的故障；描述故障现象和影响，不描述测试名或实现过程。

#### 默认不写入

- 测试、mock、fixture、覆盖率、Playwright smoke、CI/CD job、构建缓存、workflow 重排等开发门禁细节。
- Agent / AI Skill / review 流程 / backlog / handoff / 内部协作工具变更。
- 仅为让 CI 通过的测试同步、测试夹具修正、内部重构、私有 API 清理。

#### 例外

开发基础设施变化如果会直接改变用户获取或部署产物的方式，写"结果"不写实现细节：

- 好：`Windows 发布包现在包含 Dashboard 可执行文件。`
- 坏：`新增 Windows Playwright smoke 测试。`
- 好：`Dashboard 现在支持独立 Docker 镜像部署。`
- 坏：`新增 Dashboard 镜像控制通道 smoke test。`

测试、CI、技能或内部流程内容若需要记录，应写在发版完成后给维护者的对话汇报中，不写入 release metadata。

## Steps

### 1. 检查工作区状态

```bash
git status --short
```

如果有任何输出，拒绝执行 release，列出未提交文件并要求用户先提交或清理。

### 2. 记录当前分支与 master 状态

```bash
git branch --show-current
git fetch origin master --tags
```

保存当前分支，用于结束后切回。

判断当前是否已在 `master`：
- **已在 master**：执行 `git log HEAD..origin/master --oneline`。如有落后，`git pull origin master`。
- **不在 master**：暂不切换。先进入 Step 3，等用户确认 bump 级别后再切。

### 3. 展示当前版本与 bump 选项

```bash
uv run bump-my-version show-bump
```

向用户展示当前版本和三个 bump 级别，等待明确选择：

- `patch`: bugfix / 小修补，如 `3.0.0` → `3.0.1`
- `minor`: 向下兼容的新功能，如 `3.0.0` → `3.1.0`
- `major`: 破坏性变更，如 `3.0.0` → `4.0.0`

如果用户没有明确指定级别，先展示选项让用户选择，不自行决定。

### 4. 切换到 master 并同步

如果当前不在 `master`：

```bash
git checkout master
git log HEAD..origin/master --oneline
```

如果本地落后于远程：

```bash
git pull origin master
```

### 5. 扫描 commit 并确认风险字段

计算 `new_version` 后，列出本次 release 范围内的 commit：

```bash
git log v{prev}..origin/master --oneline
```

向用户确认 `数据变更` 和 `配置变更`：

- 通过 diff 辅助判断（如 `git diff v{prev}..origin/master -- data/ config/ **/migrations/`）。
- 不确定时不得默认为 no，必须标记 yes 或请用户确认。

### 6. 编写 release notes

创建 `docs/releases/v{new_version}.md`，按上文的模板格式和编写规则填写。

**完成标准**：

- [ ] 逐条审核了范围内所有 commit
- [ ] Added / Changed / Fixed 只包含用户可见或部署相关变化
- [ ] 测试、CI、agent、review、backlog 等内部内容已过滤
- [ ] `数据变更` 和 `配置变更` 已明确标记
- [ ] Risk Notes 写明了升级需要的具体操作

### 7. 展示 release notes 并确认推送

向用户展示：

- `git log v{prev}..HEAD --oneline`（将被推送的 commit 列表）
- `docs/releases/v{new_version}.md` 的完整内容

**等待用户明确确认后**才进入 Step 8。

### 8. 执行版本递增

```bash
uv run bump-my-version bump <patch|minor|major>
```

`pre_commit_hooks` 已配置为自动运行 `uv lock && git add uv.lock`，因此 bump commit 会包含同步后的 lockfile。

确认：

- `pyproject.toml` 已更新到新版本。
- `uv.lock` 中 dicepp 版本与 `pyproject.toml` 一致（确认 pre_commit_hooks 生效）。
- release metadata 文件包含在版本 bump commit 中。
- Git tag 为 `v{new_version}`。

验证 uv.lock 同步：

```bash
uv run pytest tests/test_lockfile_sync.py -v
```

如果失败，说明 pre_commit_hooks 未生效，手动执行：

```bash
uv lock && git add uv.lock && git commit --amend --no-edit
```

### 9. 推送

```bash
git push origin master --tags
```

如果 push 失败，汇报错误和本地 commit/tag 状态，提醒用户处理；仍执行切回原分支。

push 成功后，GitHub Actions (release.yml) 将自动：

- 运行 Quality Gate (test-suite.yml)
- 构建并推送 GHCR 镜像：
  - `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.Z`（+ `:latest`）
  - `ghcr.io/pear-studio/dicepp-dashboard:vX.Y.Z`（+ `:latest`）
- 在 Windows 上构建 EXE，运行冒烟测试，打包 `DicePP-vX.Y.Z-win64.zip`
- 创建 GitHub Release（body 为 `docs/releases/vX.Y.Z.md` 内容）
- 上传 `docker-compose.yml` 和 `DicePP-vX.Y.Z-win64.zip` 作为 release assets

### 10. 等待 CI 并验证产物

```bash
gh run list --workflow=release.yml --limit 3
```

如果仍在运行：

```bash
gh run watch <run-id>
```

CI 完成后逐项确认：

- `gh release view v{new_version}` 返回 release 信息。
- Release assets 包含 `docker-compose.yml` 和 `DicePP-v{new_version}-win64.zip`。
- `git show v{new_version}:docs/linux.md` 不报错。
- `docker pull ghcr.io/pear-studio/nonebot-dicepp:v{new_version}` 成功。
- `docker pull ghcr.io/pear-studio/dicepp-dashboard:v{new_version}` 成功。

如任一产物缺失，查看对应 GHA run 日志：`gh run view <run-id> --log`。

### 11. 切回原分支

如果发布前不在 `master`，切回 Step 2 记录的原始分支。

### 12. 发版摘要

```text
版本: X.Y.Z → A.B.C
Tag: vA.B.C
Release metadata: docs/releases/vA.B.C.md
镜像: ghcr.io/pear-studio/nonebot-dicepp:vA.B.C
Dashboard 镜像: ghcr.io/pear-studio/dicepp-dashboard:vA.B.C
Windows EXE: DicePP-vA.B.C-win64.zip
数据变更: yes/no
配置变更: yes/no
推送: 成功/失败
GitHub Actions: <run URL>
```

## 分支场景

以下场景不常触发，已拆分到独立文件。当用户明确要求对应操作时，读取对应文件执行：

- **补建基线**：用户要求把当前版本固化为发布基线而不递增版本 → 读取 `REF-baseline.md`
- **RC 预发布**：用户要求先验证发版链路再正式发布 → 读取 `REF-rc.md`
