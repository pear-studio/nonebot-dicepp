---
name: bump-version
description: 协助用户递增 DicePP 项目版本号（patch/minor/major），自动处理分支切换、commit、tag 与推送。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

协助用户递增 DicePP 项目版本号。自动完成分支切换、版本 bump、commit、tag 和推送，并在结束后切回原分支。

**Input**: 用户请求递增版本号，如 "发版"、"bump version"、"/bump-version"。

**Prerequisites**

- Git 仓库初始化完成
- `bump-my-version` 已安装（在 dev 依赖组：`uv sync --group dev`）
- 有远程仓库写入权限（push）

**Steps**

1. **检查工作区状态**

   运行：
   ```bash
   git status --short
   ```

   - 如果有任何输出（未提交更改）：
     - **拒绝执行**
     - 向用户汇报：`存在未提交更改，请先提交或清理后再发版`
     - 列出具体未提交的文件
     - **停止**
   - 如果输出为空：继续下一步

2. **记录当前分支**

   运行：
   ```bash
   git branch --show-current
   ```

   保存分支名（如 `dev`），用于最后切回。

3. **切换到 master 分支**

   如果当前分支不是 `master`：
   ```bash
   git checkout master
   ```

   如果切换失败（如本地 master 不存在）：
   - 汇报：`无法切换到 master 分支，原因: <error>`
   - **停止**

4. **确认 master 与远程同步**

   运行：
   ```bash
   git fetch origin master
   git log HEAD..origin/master --oneline
   ```

   如果本地 master 落后于远程：
   ```bash
   git pull origin master
   ```

5. **读取当前版本**

   运行：
   ```bash
   uv run bump-my-version show-bump
   ```

   或从 `pyproject.toml` 解析当前 `version`。

6. **让用户选择递增级别**

   向用户展示选项：
   - **patch**：`3.0.0` → `3.0.1`（bugfix / 小修补）
   - **minor**：`3.0.0` → `3.1.0`（新功能，向下兼容）
   - **major**：`3.0.0` → `4.0.0`（破坏性变更）

   等待用户明确选择。

7. **执行版本递增**

   用户确认后运行：
   ```bash
   uv run bump-my-version bump <patch|minor|major>
   ```

   解析输出：
   - 成功：`Bump version: X.X.X → Y.Y.Y`
   - 失败：汇报错误原因，**停止**

8. **推送到远程**

   自动执行：
   ```bash
   git push origin master --tags
   ```

   如果 push 失败：
   - 汇报错误（如权限不足、冲突）
   - 提醒用户手动处理
   - **继续步骤 9（仍切回原分支）**

9. **切回原分支**

   运行：
   ```bash
   git checkout <原分支名>
   ```

   向用户汇报：`已切回原分支 <name>`

10. **生成发版摘要**

    向用户输出：
    ```
    ✅ 版本递增完成

    版本: X.X.X → Y.Y.Y
    分支: 原分支 → master → 原分支
    Commit: <commit hash>
    Tag: vY.Y.Y
    推送: 成功 / 失败（原因）
    ```

**Important Notes**

- 工作区有未提交更改时**直接拒绝**，不会尝试 stash 或自动提交。
- 切到 master 前会先 fetch + pull，确保基于最新代码发版。
- 整个流程结束后**一定切回原分支**，即使用户中途取消。
- 如果 push 失败，tag 和 commit 已在本地完成，用户可手动 `git push origin master --tags`。
- 不自动创建 GitHub Release（如需可后续扩展）。
