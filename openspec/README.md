# OpenSpec 测试改进计划

本目录包含 DicePP 项目的测试基础设施改进规划文档。

## 📁 目录结构

```
openspec/
├── config.yaml          # OpenSpec 配置
├── README.md            # 本文件
└── changes/
    ├── phase1-test-infra/    # Phase 1: 测试基础设施
    │   ├── proposal.md       # 提案
    │   ├── design.md         # 设计
    │   ├── tasks.md          # 任务清单
    │   └── specs/            # 规格说明
    │       ├── pytest-config.spec.md
    │       ├── coverage-config.spec.md
    │       └── path-resolution.spec.md
    │
    ├── phase2-test-arch/     # Phase 2: 测试架构改进
    │   ├── proposal.md
    │   ├── design.md
    │   ├── tasks.md
    │   └── specs/
    │       ├── test-proxy.spec.md
    │       ├── fixtures.spec.md
    │       ├── markers.spec.md
    │       └── target-checker.spec.md
    │
    └── phase3-module-tests/  # Phase 3: 核心模块测试
        ├── proposal.md
        ├── design.md
        ├── tasks.md
        └── specs/
            ├── karma/
            │   ├── karma-config.spec.md
            │   ├── karma-state.spec.md
            │   └── karma-engines.spec.md
            ├── log/
            │   ├── log-db.spec.md
            │   └── log-command.spec.md
            ├── coc/
            │   └── character.spec.md
            └── mode/
                └── mode-command.spec.md
```

## 🔗 依赖关系

```
Phase 1 (测试基础设施)
    │
    ├── pytest 配置
    ├── 覆盖率工具
    └── sys.path 解决方案
         │
         ▼
Phase 2 (测试架构)
    │
    ├── TestProxy 类
    ├── shared_bot / fresh_bot fixture
    ├── pytest markers
    └── 辅助函数
         │
         ▼
Phase 3 (模块测试)
    │
    ├── Karma 系统测试
    ├── Log 系统测试
    ├── COC 角色卡测试
    └── Mode 命令测试
```

**重要**：必须按顺序完成各 Phase。Phase 2 依赖 Phase 1 的基础设施，Phase 3 依赖 Phase 2 的 fixture。

## 📋 当前状态

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1 | ⚠️ 部分完成 | pytest 已配置，但缺少 DicePP/conftest.py 和 .coveragerc |
| Phase 2 | ❌ 未开始 | 等待 Phase 1 完成 |
| Phase 3 | ❌ 未开始 | 等待 Phase 2 完成 |

## 🚀 快速开始

### 运行现有测试

```bash
# 从工程根目录
pytest

# 带覆盖率
pytest --cov

# 只运行特定模块
pytest src/plugins/DicePP/core/data/unit_test.py
```

### 查看文档

1. 从 `proposal.md` 开始，了解目标和背景
2. 阅读 `design.md` 了解技术方案
3. 查看 `specs/` 目录了解详细规格
4. 根据 `tasks.md` 执行实施

## 📝 Spec 编号规则

| 前缀 | 含义 |
|------|------|
| SPEC-P1-XXX | Phase 1 规格 |
| SPEC-P2-XXX | Phase 2 规格 |
| SPEC-P3-K... | Phase 3 Karma 规格 |
| SPEC-P3-L... | Phase 3 Log 规格 |
| SPEC-P3-C... | Phase 3 COC 规格 |
| SPEC-P3-M... | Phase 3 Mode 规格 |

## ❓ FAQ

**Q: 为什么要三个阶段？**
A: 分阶段确保基础设施稳定后再添加新测试，降低风险。

**Q: 可以跳过某个 Phase 吗？**
A: 不建议。每个 Phase 都依赖前一个的产出。

**Q: 如何贡献新测试？**
A: 完成 Phase 2 后，使用 `fresh_bot` fixture 和 `send_and_check` 辅助函数。
