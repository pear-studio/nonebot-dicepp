# Phase 2: 测试架构改进

## 背景

现有集成测试（`core/command/unit_test.py`）存在以下架构问题：

1. **状态耦合**：所有测试方法共享同一个 `Bot` 实例（类变量），测试间存在隐式依赖，执行顺序由字母序决定，改变顺序可能导致随机失败。

2. **checker 能力有限**：`__vg_msg` 的 `checker` 只能检查"所有 BotCommand 文本拼接"，无法验证：
   - 消息发送给谁（`MessagePort`）
   - 命令的类型（发消息 vs 退群 vs 延迟）
   - 隐藏骰是否同时发给用户和GM

3. **测试分类缺失**：无法区分单元测试和集成测试，无法按标签筛选运行。

4. **辅助工具重复**：`TestProxy`、`Bot` 初始化逻辑散落在测试文件里，新增测试文件时需重复编写。

## 目标

- 将共享测试基础设施提取到 `conftest.py` 的 pytest fixture
- 增强消息验证能力，支持验证发送目标
- 添加 pytest mark 标签体系
- 保持现有测试逻辑不变（向后兼容）

## 非目标

- 不将现有测试改写为完全独立的 fixture 风格（成本过高）
- 不改变 Bot 的对外接口

## 成功标准

- 新测试文件可以通过 `conftest.py` 的 fixture 快速搭建测试环境
- `pytest -m unit` 只运行单元测试，`pytest -m integration` 只运行集成测试
- `__vg_msg` 支持 `target_checker` 参数，可验证消息发送目标
