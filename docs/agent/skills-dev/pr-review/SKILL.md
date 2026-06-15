---
name: pr-review
description: 读取 GitHub Pull Request 的 diff，review 代码质量，执行 approve / request changes / merge 操作。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

读取指定的 GitHub Pull Request，分析 diff 给出 review 意见，并根据用户决策执行 approve、request changes 或 merge。

**Input**: 用户指定 PR 号或分支名，如 "review #42"、"/pr-review 42"。

**Prerequisites**

- GitHub CLI (`gh`) 已安装并认证
- 当前仓库有对应的 GitHub remote

**Steps**

1. **获取 PR 信息**

   运行：
   ```bash
   gh pr view <pr_number> --json number,title,author,headRefName,baseRefName,body,url
   ```

   如果 PR 不存在：汇报错误，停止。

2. **获取 diff**

   运行：
   ```bash
   gh pr diff <pr_number>
   ```

3. **分析代码**

   阅读 diff，从以下维度给出 review 意见：
   - 代码逻辑是否正确
   - 是否有明显 bug 或边界情况未处理
   - 命名是否清晰、是否符合项目风格
   - 是否有冗余代码或可简化之处
   - 测试是否覆盖
   - 是否引入安全风险

   输出结构化的 review 报告：
   ```
   ## PR Review: #<number> <title>

   ### 变更摘要
   - 文件数: X
   - 新增行: +Y
   - 删除行: -Z

   ### 审查意见
   - [问题/建议/疑问] ...
   - [问题/建议/疑问] ...

   ### 总体评估
   - 通过 / 需修改 / 需讨论
   ```

4. **等待用户决策**

   向用户展示 review 报告后，询问操作：
   - **approve**: 标记为审核通过
   - **request changes**: 标记为需要修改（附带 review 意见）
   - **merge**: 合并 PR（需先确认是否已通过或用户明确要直接合并）
   - **跳过**: 不做任何操作

5. **执行操作**

   **Approve**:
   ```bash
   gh pr review <pr_number> --approve --body "<review summary>"
   ```

   **Request changes**:
   ```bash
   gh pr review <pr_number> --request-changes --body "<detailed feedback>"
   ```

   **Merge**:
   先确认合并方式：
   - `gh pr merge <pr_number> --squash`（推荐，压缩为一个 commit）
   - `gh pr merge <pr_number> --rebase`（保留每个 commit，线性历史）
   - `gh pr merge <pr_number> --merge`（传统 merge commit）

   执行合并后输出结果。

**Important Notes**

- PR 作者与 reviewer 身份不设限制，允许自审。
- Merge 前默认检查 CI 状态，如果检查失败则警告用户。
- 如果 PR 有冲突，merge 会失败，需提示用户先解决冲突。
- 合并方式默认推荐 `--squash`，保持 master 历史简洁。
