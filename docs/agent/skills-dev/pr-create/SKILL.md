---
name: pr-create
description: 从当前 feature 分支创建 GitHub Pull Request，自动推送分支并填写标题/描述。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

从当前 feature 分支创建 GitHub Pull Request，自动推送到远端并生成 PR 标题和描述。

**Input**: 用户请求创建 PR，如 "开 PR"、"create pr"、"/pr-create"。

**Prerequisites**

- GitHub CLI (`gh`) 已安装并认证（`gh auth status` 显示已登录）
- 当前在 `feature/xxx` 或 `hotfix/xxx` 分支（不在 master）
- 工作区干净，无未提交更改

**Steps**

1. **检查当前分支**

   运行：
   ```bash
   git branch --show-current
   ```

   - 如果当前是 `master` 或 `main`：拒绝，提示 `请在 feature 分支上创建 PR`
   - 如果是 `feature/xxx` 或 `hotfix/xxx`：继续

2. **检查工作区状态**

   运行：
   ```bash
   git status --short
   ```

   - 有未提交更改：拒绝，列出文件，提示先 commit
   - 干净：继续

3. **检查当前 HEAD 的完整回归**

   push 前必须确认本次会话已在当前 HEAD 上成功运行：

   ```bash
   uv run pytest
   ```

   如果测试成功后又发生代码、配置或测试改动，或测试对应的不是当前 HEAD，必须重新运行。没有可复用结果时先运行；失败或未完成则停止，不得 push。

4. **推送到远端**

   ```bash
   git push origin $(git branch --show-current)
   ```

   如果 push 失败：汇报错误，停止。

5. **生成 PR 标题和描述**

   从最近的 commit message 提取 PR 标题：
   ```bash
   git log -1 --pretty=format:"%s"
   ```

   生成分支对比的变更摘要作为描述：
   ```bash
   git log origin/master..HEAD --oneline
   ```

   向用户展示拟定的标题和描述，询问是否确认或修改。

6. **创建 PR**

   用户确认后运行：
   ```bash
   gh pr create --title "<标题>" --body "<描述>"
   ```

   如果 PR 已存在：
   - 汇报：`PR 已存在: <url>`
   - 停止

7. **输出结果**

   成功创建后输出：
   ```
   ✅ PR 创建成功

   分支: feature/xxx → master
   标题: <title>
   链接: <pr_url>
   ```

**Important Notes**

- 目标分支固定为 `master`（ DicePP 项目主分支）。
- 如果 commit message 不够清晰，主动询问用户是否要修改 PR 标题。
- PR 创建后不会自动合并，需通过 `pr-review` skill 或手动审核。
