---
name: git-commit-brief
description: Read before every local git commit.
---

# Git Commit Brief

- 注意 `docs/agent/link-to-cursor.bat`: `.cursor/rules/*.mdc` 与 `docs/agent/rules/*.md` 是硬链接, `.cursor/skills` 是到 `docs/agent/skills` 的目录符号链接; 任一侧修改会同步到另一侧.
- 提交前避免把"链接同步产生的镜像改动"重复计入, 按真实源目录(建议 `docs/agent/*`)核对后再暂存.
- 中文 commit log 模板: `<type>: <一句话主题>` + 空行 + `说明为什么改/影响什么`(例: `feat: 优化前端搜索与鉴权恢复`).
- 标点符号使用半角+空格, 如"xxx, xxx."
- 读取实际修改的文件, 确认是否都是一个主题修改, 如果无法确定则询问用户. 根据修改的内容编写 commit log, 避免只看文件名称.
