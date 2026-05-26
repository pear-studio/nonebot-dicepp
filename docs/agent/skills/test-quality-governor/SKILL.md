---
name: test-quality-governor
description: 自动化治理项目测试质量。用于测试数量膨胀、低价值测试过多、断言薄弱、mock 过度、重复用例、测试层级混乱、flaky 测试、希望用 subagent 批量审计和自动改进测试资产的项目。触发后应自动发现测试框架、建立清单、增量审计、执行低风险改进、验证结果，并只在高风险操作时询问用户。
---

# Test Quality Governor

## 目标

把项目测试从“数量很多”治理成“信号清晰、维护成本低、能保护行为契约”的测试资产。不要为了覆盖率堆测试；优先减少重复测试、弱断言、实现细节测试、过度 mock 和层级错配。

默认自动推进。只有高风险删除、大规模重写、需要修改生产代码、或语义不确定且影响核心行为时，才询问用户。

## 默认状态目录

把所有中间产物写入项目根目录：

```text
.temp/test-quality/
├── state.json
└── runs/
    └── <run-id>/
        ├── summary.md
        ├── inventory.json
        ├── file-summary.jsonl
        ├── action-items.jsonl
        ├── applied-changes.jsonl
        └── verification.md
```

不要把中间产物写进源码目录、测试目录或 docs。除非用户明确要求，不要提交 `.temp/test-quality/`。

## 标准流程

1. **发现项目测试系统**
   读取测试配置、依赖、CI、Makefile/justfile、测试目录结构。优先运行 `scripts/discover_tests.py <repo>`。

2. **建立测试资产清单**
   运行 `scripts/inventory_tests.py <repo> --out <run-dir>/inventory.json`。清单用于确定测试文件、测试函数、marker/tag、fixture、mock、参数化、异步、snapshot 等信号。

3. **增量判断**
   如果 `.temp/test-quality/state.json` 存在，运行 `scripts/state_tests.py compare <repo> --inventory <run-dir>/inventory.json --discover <run-dir>/discover.json`。未变化文件可复用上次审计结果；变化、新增、删除文件要重新处理。运行结束后用 `scripts/state_tests.py update ...` 更新状态。

4. **样本交叉校准**
   项目较大、风格陌生、或准备自动删除/重写前，先抽样。可运行 `scripts/sample_tests.py <inventory.json> --count 40`。尽量让两个 subagent 独立审计同一小批样本，比较分歧并校准标准。

5. **并行审计**
   运行 `scripts/plan_batches.py <inventory.json>` 得到批次。尽量派发 subagent 分目录或分批审计。subagent 不直接修改文件，只输出文件级摘要和 action item。

6. **合并报告**
   用 `scripts/merge_reports.py` 合并 subagent 产物，生成 `file-summary.jsonl` 和 `action-items.jsonl`。不要默认逐个展示所有测试；只详细记录需要动作的测试组。

7. **自动改进**
   默认自动执行 `risk=low` 且 `confidence=high` 的动作。中风险优先小批量执行并验证；高风险必须先说明原因并请求确认。

8. **验证**
   每批改动后运行相关测试入口。失败时自动诊断并修正；无法修正时回退本批改动并记录原因。

9. **总结与串联**
   写入 `summary.md` 和 `verification.md`，向用户简述本轮结果，并主动建议下一阶段。用户只需要说“继续”，不要要求用户记住其他 skill。

## 资源索引

- 评分标准：`references/rubric.zh.md`
- 动作策略：`references/action-policy.zh.md`
- 子代理提示词：`references/subagent-prompts.zh.md`
- 报告格式：`references/report-schema.zh.md`
- 框架识别参考：`references/framework-notes.zh.md`

需要判断测试质量时先读评分标准和动作策略；需要派发 subagent 时读子代理提示词；需要写或合并产物时读报告格式。

## 自动化边界

- `low risk + high confidence`：默认自动执行。
- `medium risk`：优先分批执行并验证；影响面大或语义不确定时询问用户。
- `high risk`：必须先询问用户。

高风险包括：删除核心领域测试、批量快照更新、大规模改写 fixture、需要修改生产代码、无法判断是否为历史回归测试、或可能改变测试表达的业务语义。

## 禁止事项

- 不要仅凭覆盖率判断测试价值。
- 不要因为测试数量多就删除测试。
- 不要默认逐个输出所有 `keep` 测试。
- 不要把 mock 调用次数、非空断言、不抛异常自动视为高质量。
- 不要为了让测试通过而重写生产代码，除非用户明确要求修复生产缺陷。
- 不要在未验证的情况下批量提交测试重构。
