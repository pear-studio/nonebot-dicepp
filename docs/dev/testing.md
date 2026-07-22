# 测试架构

DicePP 的测试按“允许使用的最重资源”分层。测试层级由目录表达，不再用
`unit`、`integration`、`e2e`、`slow` 等 marker 重复描述；项目唯一的测试选择
marker 是 `quick`。

## 目录约定

`tests/` 顶层只允许以下目录：

| 目录 | 用途 |
| --- | --- |
| `unit/` | 单个模块或纯内存协作的快速测试 |
| `integration/` | 当前 Python 进程内的组件集成测试 |
| `system/` | 进程、浏览器、监听端口和安装包级测试 |
| `external/` | 访问真实外部服务、账号或付费 API 的测试 |
| `support/` | 多个测试共享的 Python helper、fake、builder 和断言工具 |
| `fixtures/` | JSON、图片、语料、快照等静态测试数据 |

根目录只保留 `conftest.py` 和可选的 `__init__.py`。测试代码不要放进
`support/`，可复用 helper 也不要散落在可收集的测试模块中。

`conftest.py` 只负责 pytest fixture 的注册、组装和 hook。可测试的逻辑放到
`tests/support/`，静态数据放到 `tests/fixtures/`。测试模块禁止导入任何
`conftest`；需要复用的对象必须移到显式可导入的 support 模块。

DicePP 运行时内部的 canonical namespace 是 `core/module/utils/adapter/shell`
等裸路径。unit、integration 和 support 不得改用 `plugins.DicePP.*`，否则同一
源码可能形成两份 module、singleton 或 ContextVar。只有明确验证外部包边界的
system 测试，以及 Dashboard integration 中匹配正式跨包调用的测试可以使用完整
包路径。将全部生产代码统一迁移到完整包命名空间属于独立架构任务。

根 `conftest.py` 会在测试模块导入前为每个 pytest worker 创建隔离的应用目录，
并在会话结束后检查真实仓库未被污染。这是全套测试的安全边界；具体 Bot、数据库
和 Dashboard fixture 仍只在相应 integration/system 层注册。

## 资源边界

| 能力 | unit | integration | system | external |
| --- | :---: | :---: | :---: | :---: |
| 纯内存 fake / stub | ✓ | ✓ | ✓ | ✓ |
| 临时文件、真实文件读写 | — | ✓ | ✓ | ✓ |
| SQLite / `aiosqlite` 真实连接 | — | ✓ | ✓ | ✓ |
| 完整 NoneBot `Bot`、FastAPI/Starlette `TestClient` | — | ✓ | ✓ | ✓ |
| 子进程 | — | — | ✓ | ✓ |
| 真实 socket listener、WebSocket/ASGI server | — | — | ✓ | ✓ |
| Playwright 或其他真实浏览器 | — | — | ✓ | ✓ |
| 安装包、分发包和跨进程生命周期 | — | — | ✓ | ✓ |
| 真实外部服务、真实账号、付费 API | — | — | — | ✓ |

这里的“允许”不是“必须”。始终选择能够证明行为契约的最低成本层级。mock
或 fake 如果掩盖了需要验证的组件协作，应将测试上移到 `integration/`，而不是
继续扩大 mock 范围。

## quick、full 与 external

- `quick` 只标记位于 `unit/` 或 `integration/`、稳定且适合高频反馈的代表性
  测试。它不是测试层级，也不等同于全部 unit 测试。
- quick suite 使用 `pytest -m quick`。迁移完成后，它应成为本地和 CI 的短反馈
  入口；项目标准命令为 `uv run pytest -m quick -n0`，始终串行运行并以端到端
  60 秒为预算。
- quick 的选择遵循“先覆盖宽度、预算内再补深度”：至少覆盖 core、adapter、
  utils、roll、Persona、Dashboard，并包含少量高信号 integration 契约。新增
  quick 项时应重新测量串行总时长；不因追求数量降低断言质量。
- full suite 按目录运行 `unit/`、`integration/`、`system/`，包括 Dashboard
  Playwright 浏览器回归，不依赖额外的层级 marker。首次运行前安装受 Playwright
  管理的 Chromium：`uv run playwright install chromium`。缺少 Python 包或
  Chromium 时 full 直接失败，不根据本机是否碰巧安装系统 Chrome 改变测试集合。
  标准命令 `uv run pytest` 使用自适应 xdist：默认保留一个 CPU、最多 4 workers，
  因此 2 核环境自动串行；显式 `-n0` 始终覆盖默认并行。
- `external/` 默认不收集，必须由操作者显式选择，并在运行前确认凭据、成本和
  对外副作用。

项目不新增其他“选择一批测试”的 marker。确有 pytest 技术机制要求的 marker
应先在测试架构评审中说明用途，不能用来绕过目录边界。

pytest 使用 `importlib` 导入模式和显式 Python 路径，确保分层后允许同名测试文件，
同时避免某个 `tools/` 测试包遮蔽仓库维护工具。

## 静态检查与评审

静态检查器以显式路径运行，不会在导入时执行测试代码：

```powershell
uv run python -m tools.check_test_layout --help
uv run python -m tools.check_test_layout tests
uv run python -m tools.check_test_layout path\to\tests
```

检查器解析 Python AST，并处理常见的模块别名和 `from ... import ... as ...`。
它当前检查：

- `tests/` 顶层位置；
- 导入 `conftest`；
- unit/integration/support 中不属于显式 Dashboard 包边界的 `plugins.DicePP.*`
  导入和 patch 目标；
- 用 `Path(__file__).parents[n]` 推算仓库根目录；
- unit 中直接连接 SQLite、构造完整 Bot/TestClient，或使用系统级资源；
- integration 中使用子进程、listener/server 或浏览器；
- `quick` 是否只位于 unit/integration；
- 是否仍使用 `unit`、`integration`、`e2e`、`slow`、`real_llm` 等旧选择 marker。

静态检查只处理语法上能可靠识别的违规，不是运行时沙箱。例如包装后的资源
调用、动态导入、间接返回的 socket，以及“虽然没有违规 API 但测试范围过大”
都需要 Agent 语义审查。审查时还要确认：测试保护的是行为契约、层级是最低可
信层级、fixture/support 放置正确，且没有真实外部副作用。

## CI 与 push 门禁

共享 CI 提供：quick 代表集、一次带覆盖率且包含 Dashboard Playwright 的完整离线
回归、Dashboard 镜像冒烟和 Windows 安装包验收。兼容语料、浏览器、慢测试及普通
integration/system 已包含在完整回归中，不再按旧 marker 重复执行。

Agent 每次 push 前必须在当前 HEAD 上成功运行 `uv run pytest`。只有本次会话已经
在同一 HEAD 上成功运行，且之后没有代码、配置或测试改动时，才可以复用结果。
这条规则不安装 Git hook，避免干扰人工工作流。

## 迁移原则

后续迁移或新增测试时先按真实资源使用情况归类，不要凭历史文件名猜层级。修改后
先运行布局检查器和最小相关 pytest；涉及公共 fixture、目录或 pytest 配置时，再
扩大到 quick 和完整离线回归。
