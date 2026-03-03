# Phase 4: 技术设计

## GitHub Actions 工作流设计

### 工作流文件结构

```
.github/
└── workflows/
    ├── test-fast.yml      # PR 触发：快速测试（排除 slow）
    └── test-full.yml      # push main 触发：完整测试含 slow + 覆盖率
```

---

### test-fast.yml：PR 快速测试

```yaml
name: Fast Tests

on:
  pull_request:
    branches: [ main, master, dev ]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.8", "3.10"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-asyncio pytest-cov
          pip install -r requirements.txt

      - name: Run fast tests (exclude slow)
        run: |
          pytest -m "not slow" --tb=short -q
        working-directory: .

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results-${{ matrix.python-version }}
          path: .pytest_cache/
```

---

### test-full.yml：完整测试（含覆盖率）

```yaml
name: Full Tests & Coverage

on:
  push:
    branches: [ main, master ]
  schedule:
    - cron: '0 2 * * *'  # 每日凌晨2点自动跑

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.8", "3.10"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-asyncio pytest-cov
          pip install -r requirements.txt

      - name: Run all tests with coverage
        run: |
          pytest --cov --cov-report=xml --cov-report=html -v
        working-directory: .

      - name: Upload coverage HTML report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-html-${{ matrix.python-version }}
          path: htmlcov/
          retention-days: 14

      - name: Upload coverage XML (for Codecov)
        uses: codecov/codecov-action@v4
        if: matrix.python-version == '3.10'  # 只上传一次
        with:
          file: coverage.xml
          fail_ci_if_error: false
```

---

## 依赖安装策略

由于 `pyproject.toml` 使用 poetry，但 CI 环境更倾向于 pip，有两个选择：

### 选项 A：保持 pip + requirements.txt（推荐，简单）

```yaml
- name: Install dependencies
  run: |
    pip install pytest pytest-asyncio pytest-cov
    pip install openpyxl rsa python-docx lxml
    # 不安装 nonebot2 等运行时依赖（测试不需要完整 bot 运行时）
```

优点：快速，依赖最小化  
缺点：需要手工维护 CI 依赖列表

### 选项 B：poetry install（完整）

```yaml
- name: Install poetry
  run: pip install poetry

- name: Install dependencies
  run: poetry install --with dev
```

优点：与本地开发环境完全一致  
缺点：慢（需要安装 nonebot2、aiohttp 等）

**建议**：选项 A，测试只需核心依赖，减少 CI 时间。

---

## 测试数据依赖

集成测试（`test_3_query`）需要本地 `.xlsx` 测试数据文件。需确认：

1. 测试数据文件是否已在仓库中（`src/plugins/DicePP/` 下的 `test.xlsx` 等）
2. 如未提交，需在 `.gitignore` 中将测试数据排除规则调整，或专门创建 CI 用的最小测试数据集

---

## 本地快速验证命令

```bash
# 模拟 CI fast 测试
pytest -m "not slow" --tb=short -q

# 模拟 CI full 测试
pytest --cov --cov-report=term-missing

# 查看覆盖率报告
open htmlcov/index.html  # macOS/Linux
start htmlcov/index.html # Windows
```

---

## 风险点

| 风险 | 说明 | 缓解 |
|------|------|------|
| 测试数据文件不在仓库 | `.xlsx` 查询数据库文件可能被 gitignore | 检查并提交最小测试数据集 |
| 异步测试在 Python 3.8 的兼容性 | `IsolatedAsyncioTestCase` 在 3.8 支持有限 | 使用 pytest-asyncio 替代，或跳过已知不兼容的测试 |
| CI 超时 | 集成测试含随机骰子循环，可能较慢 | 给 `slow` 测试设置 `--timeout=60` |
| secrets 管理 | 如有 Codecov token 需配置 GitHub secret | 添加 `CODECOV_TOKEN` 到仓库 secrets |
