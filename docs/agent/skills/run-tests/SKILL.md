---
name: run-tests
description: Run tests for the DicePP project. Executes pytest to validate implementation correctness.
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

Run tests for the DicePP project to validate implementation correctness.

**Input**: Optionally specify which tests to run. If omitted, run all tests.

**Prerequisites**

- Python 3.10+ installed
- uv installed (推荐) 或 pip
- Dependencies installed: `uv pip install ".[dev]"`

**Steps**

1. **Check dependencies**

   Verify pytest is installed:
   ```powershell
   uv run python -c "import pytest; print(pytest.__version__)"
   ```

   If pytest is not installed:
   - Recommendation: "Run: uv pip install '.[dev]'"

2. **Run All Tests**

   Execute pytest:
   ```powershell
   cd d:\Workplace\nonebot-dicepp
   uv run pytest -v --tb=short
   ```

   Parse the output:
   - Count passed tests
   - Count failed tests
   - Note any errors or warnings

   **Test Results**:
   - If tests pass: Add to report "✓ Tests: X passed"
   - If tests fail:
     - Add CRITICAL issue: "Tests failed: <test name>"
     - List failing tests
     - Recommendation: "Fix failing tests before continuing"

3. **Run Specific Module Tests**

   To test a specific module:
   ```powershell
   # Roll module tests
   uv run pytest tests/module/roll/ -v

   # Character module tests
   uv run pytest tests/module/character/ -v

   # Core tests
   uv run pytest tests/core/ -v
   ```

4. **Run Coverage Report**

   Execute pytest with coverage:
   ```powershell
   uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing
   ```

   **Coverage Results**:
   - Report coverage percentage
   - List uncovered lines if below threshold

5. **Generate Test Report**

   Create a summary report:

   ```
   ## Test Report

   ### Test Results
   | Status   | Passed | Failed | Duration |
   |----------|--------|--------|----------|
   | Pass/Fail| X      | Y      | ~Zs      |

   ### Coverage (Optional)
   | Module       | Coverage |
   |--------------|----------|
   | core/        | XX%      |
   | module/roll/ | XX%      |

   ### Final Assessment
   - If tests fail: "X test(s) failed. Fix before proceeding."
   - If all pass: "All tests passed! ✓"
   ```

**Options**

- `--all, -a`: Run all tests (default)
- `--module <name>`: Run tests for specific module
- `--coverage, -c`: Include coverage report
- `--verbose, -v`: Verbose output

**Usage Examples**

```bash
# Run all tests
uv run pytest -v --tb=short

# Run only roll module tests
uv run pytest tests/module/roll/ -v

# Run with coverage
uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing

# Run specific test file
uv run pytest tests/module/roll/test_karma.py -v
```

**Exit Criteria**

- All tests must pass (exit code 0)
- Coverage should be maintained or improved

If tests fail, report specific failing tests and suggest fixes.

**Important Note**

测试运行需要一定时间（约1分钟），请耐心等待测试完成后再查看结果。

在 Trae IDE 中运行测试时，输出可能会以"实时滚动"的方式显示，只显示最新的一行内容。这是正常的输出行为，不代表内容被截断。请等待测试完全结束后再读取完整结果。

如果需要保存完整输出用于后续分析，可以使用重定向
