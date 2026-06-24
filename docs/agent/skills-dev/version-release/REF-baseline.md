# Baseline Repair

当用户明确要求把当前 `pyproject.toml` 版本补建为发布基线时使用（不执行版本递增）。

## Steps

1. 确认当前版本号与目标 tag 一致。
2. 确认 `docs/releases/vX.Y.Z.md` 已存在且内容完整。
3. 确认 `.bot` 运行版本与包版本一致。
4. 确认 GHCR workflow (release.yml) 与 `.dockerignore` 已准备好。
5. 确认工作区干净，所有改动已提交到 master，当前 commit 是想要固化的基线 commit。
6. 手工创建并推送 tag：

   ```bash
   git tag vX.Y.Z
   git push origin master --tags
   ```

7. 等待 GitHub Actions 完成，验证镜像和 GitHub Release。参考主技能 Step 9。
