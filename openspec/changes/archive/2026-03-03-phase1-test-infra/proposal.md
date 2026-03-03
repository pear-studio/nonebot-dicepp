# Phase 1: 测试基础设施建设

## 背景

当前工程的自动化测试存在以下问题：
- 测试工具链原始，没有 pytest 配置，只能手动 `cd` 到目录后跑 `python -m unittest`
- `pyproject.toml` 中没有任何测试相关依赖（pytest、pytest-asyncio、覆盖率工具等）
- 测试路径依赖 `sys.path` 的隐式配置，无法从工程根目录直接运行
- 没有覆盖率统计，不知道测试盲区在哪里
- 现有三个测试文件（`core/command/unit_test.py`、`core/data/unit_test.py`、`module/roll/unit_test.py`）完全孤立，没有统一入口

## 目标

搭建规范的 Python 测试基础设施，让所有现有测试可以用 `pytest` 从工程根目录一键运行，并产出覆盖率报告。

## 非目标

- 不修改现有测试的测试逻辑
- 不新增测试用例
- 不改变 Bot 核心逻辑代码

## 方案概述

1. 在 `pyproject.toml` 中添加测试依赖和 pytest 配置节
2. 创建 `conftest.py` 解决 `sys.path` 问题
3. 将三个测试文件统一纳入 pytest 发现范围
4. 验证所有现有测试在 pytest 下正常通过

## 成功标准

- `pytest` 命令从工程根目录可直接运行
- 所有现有测试用例通过
- `pytest --cov` 可产出覆盖率报告
- 无需手动设置环境变量或 `PYTHONPATH`
