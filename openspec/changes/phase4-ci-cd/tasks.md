# Phase 4: 任务清单

## T4.1 创建 GitHub Actions 工作流目录
- [ ] 创建 `.github/workflows/` 目录

## T4.2 快速测试工作流（PR 触发）
- [ ] 创建 `.github/workflows/test-fast.yml`
  - [ ] 触发条件：`pull_request` 到 main/master/dev
  - [ ] Python 矩阵：`["3.8", "3.10"]`
  - [ ] 安装依赖步骤
  - [ ] 运行 `pytest -m "not slow" --tb=short -q`
  - [ ] 上传 test results artifact

## T4.3 完整测试工作流（push main + 定时）
- [ ] 创建 `.github/workflows/test-full.yml`
  - [ ] 触发条件：push 到 main/master，以及每日定时（UTC 02:00）
  - [ ] Python 矩阵：`["3.8", "3.10"]`
  - [ ] 安装依赖步骤
  - [ ] 运行 `pytest --cov --cov-report=xml --cov-report=html -v`
  - [ ] 上传 coverage HTML artifact（保留 14 天）
  - [ ] 接入 Codecov（仅 Python 3.10 上传，避免重复）

## T4.4 测试数据确认
- [ ] 检查 `test_3_query` 依赖的 `.xlsx` 文件是否存在于仓库
- [ ] 若不存在，创建最小化的 CI 用测试数据集并提交

## T4.5 依赖文件完善
- [ ] 创建或更新 `requirements-dev.txt`，包含：
  - `pytest>=7.4`
  - `pytest-asyncio>=0.23`
  - `pytest-cov>=4.1`
- [ ] 在 CI yml 中引用 `requirements-dev.txt`

## T4.6 验证
- [ ] 在 GitHub 上创建测试 PR，确认 fast workflow 触发并通过
- [ ] 合并 PR 后确认 full workflow 触发并生成覆盖率报告
- [ ] 下载 artifact，确认 HTML 报告可正常打开
- [ ] 确认 PR 页面的 Check 状态显示 ✅ 或 ❌

## T4.7 README 更新
- [ ] 添加 CI 状态徽章（GitHub Actions Badge）
- [ ] 添加覆盖率徽章（Codecov Badge，如果接入的话）
- [ ] 在 README 中说明如何本地运行测试
