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

1. **Check Python environment**

   Verify Python is available:
   ```powershell
   python --version
   ```

   If Python is not available:
   - Add CRITICAL issue: "Python not found"
   - Recommendation: "Install Python 3.10+ and ensure it's in PATH"

2. **Check dependencies**

   Verify pytest is installed:
   ```powershell
   uv run python -c "import pytest; print(pytest.__version__)"
   ```

   If pytest is not installed:
   - Recommendation: "Run: uv pip install '.[dev]'"

3. **Run All Tests**

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

4. **Run Specific Module Tests**

   To test a specific module:
   ```powershell
   # Roll module tests
   uv run pytest tests/module/roll/ -v

   # Character module tests
   uv run pytest tests/module/character/ -v

   # Core tests
   uv run pytest tests/core/ -v
   ```

5. **Run Coverage Report**

   Execute pytest with coverage:
   ```powershell
   uv run pytest --cov=src/plugins/DicePP --cov-report=term-missing
   ```

   **Coverage Results**:
   - Report coverage percentage
   - List uncovered lines if below threshold

6. **Generate Test Report**

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