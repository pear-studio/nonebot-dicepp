# Phase 2: 任务清单

## T2.1 提取测试基础设施到 conftest.py
- [ ] 在 `src/plugins/DicePP/conftest.py` 中实现 `TestProxy` 类（含 `received` 字段和 `clear()` 方法）
- [ ] 实现 `shared_bot` fixture（class scope，对应现有测试行为）
- [ ] 实现 `fresh_bot` fixture（function scope，供新测试使用）
- [ ] 确认现有测试引入 `TestProxy` 后无冲突（原文件内的 `TestProxy` 可保留或删除）

## T2.2 增强 __vg_msg / __vp_msg / __v_notice
- [ ] 在 `core/command/unit_test.py` 的 `__vg_msg` 中增加 `target_checker` 可选参数
- [ ] 在 `__vp_msg` 中增加 `target_checker` 可选参数
- [ ] 在 `__v_notice` 中增加 `target_checker` 可选参数
- [ ] 补充 `.rh` 隐藏骰的 `target_checker` 用例，验证同时发给用户和GM

## T2.3 注册 pytest mark 标签
- [ ] 在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中添加 markers 配置
  - `unit`、`integration`、`slow`、`karma`、`log`
- [ ] 为现有 `roll/unit_test.py` 的 `MyTestCase` 添加 `@pytest.mark.unit`
- [ ] 为现有 `data/unit_test.py` 的 `MyTestCase` 添加 `@pytest.mark.unit`
- [ ] 为现有 `command/unit_test.py` 的 `MyTestCase` 添加 `@pytest.mark.integration`

## T2.4 验证
- [ ] `pytest -m unit` 只运行 roll 和 data 的测试
- [ ] `pytest -m integration` 只运行集成测试
- [ ] 验证 `target_checker` 在 `.rh` 测试中正确检测到两条目标不同的消息
