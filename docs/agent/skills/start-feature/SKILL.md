---
name: start-feature
description: 从 master 创建新的 feature 分支，确保基于最新代码并检查工作区状态。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

从最新 master 创建功能分支，自动拉取上游更新并检查工作区状态。

**Input**: 用户请求创建功能分支，如 "开新功能"、"/start-feature"、或提供分支名如 "feature/roll-refactor"。

**Prerequisites**

- Git 仓库已初始化
- 有 `origin/master` remote

**Steps**

1. **检查当前分支**

   运行：
   ```bash
   git branch --show-current
   ```

   记录当前分支名，用于最后提示。

2. **检查工作区状态**

   运行：
   ```bash
   git status --short
   ```

   - 有未提交更改：
     - 拒绝执行
     - 汇报未提交文件列表
     - 提示：`存在未提交更改，请先 commit 或 stash 后再开新分支`
     - **停止**
   - 干净：继续

3. **获取功能分支名称**

   如果用户没有提供分支名，询问：
   > 请输入功能分支名称（如 `feature/roll-refactor`）：

   分支名规范检查：
   - 如果不以 `feature/` 或 `hotfix/` 开头，自动补全 `feature/` 前缀
   - 去除空格和特殊字符

4. **拉取最新 master**

   ```bash
   git fetch origin master
   git checkout master
   git pull origin master
   ```

   如果 pull 失败（如本地 master 有未推送提交）：
   - 汇报错误原因
   - **停止**

5. **创建功能分支**

   ```bash
   git checkout -b <branch_name> master
   ```

   如果分支已存在：
   - 汇报：`分支 <name> 已存在`
   - 询问是否切换到该分支，或重新命名

6. **输出结果**

   成功创建后输出：
   ```
   ✅ 功能分支已创建

   分支: <branch_name>
   基于: origin/master (<commit_hash>)
   当前分支已切换到: <branch_name>

   可以开始开发了。开发完成后用 /pr-create 创建 Pull Request。
   ```

**Important Notes**

- 如果当前不在 master 分支，会先 stash（如有需要）切到 master，创建完 feature 分支后不会切回原分支——你就在 feature 分支上开始开发。
- 自动基于最新 `origin/master` 创建，避免后续 rebase 冲突。
- 分支名建议用 `feature/简述` 或 `hotfix/简述` 格式。
