# RC / Prerelease

当用户要求先验证发版链路时，使用 RC 预发布版本。

## Steps

1. 选择目标正式版本作为基底：
   - 如果 `3.0.0` 尚未正式发布，测试版从 `3.0.0rc1` 开始。
   - 已有正式版后再使用下一个版本的 RC（如 `3.1.0rc1`）。
2. 将 `pyproject.toml` 版本更新为目标 RC 版本，并准备对应的 `docs/releases/vX.Y.ZrcN.md`。
   - RC release notes 以同目标版本的前一个 RC 为 comparison base；如无前一个 RC，则以当前发布周期的前一个正式版本为 comparison base。
3. 创建并推送 tag：

   ```bash
   git tag vX.Y.ZrcN
   git push origin master --tags
   ```

4. GitHub Actions 构建产物：
   - Docker 镜像 `ghcr.io/pear-studio/nonebot-dicepp:vX.Y.ZrcN` 和 `ghcr.io/pear-studio/dicepp-dashboard:vX.Y.ZrcN`
   - Windows EXE `DicePP-vX.Y.ZrcN-win64.zip`
   - **不更新 `:latest`**
5. GitHub Release 标记为 **Prerelease**。
6. 验证参考主技能 Step 9。

RC 测试通过后，正式发布仍使用纯数字版本 `vX.Y.Z`。
