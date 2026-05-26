# 框架识别参考

先从配置文件和依赖识别测试系统，再从测试文件验证。

## Python

常见文件：

- `pyproject.toml`
- `pytest.ini`
- `tox.ini`
- `noxfile.py`
- `requirements*.txt`
- `Makefile`
- `.github/workflows/*.yml`

常见框架和信号：

- `pytest`：`test_*.py`、`*_test.py`、`pytest.mark`、fixtures、`conftest.py`
- `unittest`：`unittest.TestCase`
- `hypothesis`：`@given`
- `pytest-asyncio`：async tests、`pytest.mark.asyncio`
- `pytest-cov`：coverage 入口
- `pytest-xdist`：`-n auto`
- `pytest-recording` / `vcrpy`：录制外部 API

## JavaScript / TypeScript

常见文件：

- `package.json`
- `vitest.config.*`
- `jest.config.*`
- `playwright.config.*`
- `tsconfig.json`

常见框架和信号：

- `vitest`：`describe`、`it`、`test`、`expect`、`vi.mock`
- `jest`：`jest.mock`、`expect`
- `playwright`：`@playwright/test`

## Go

常见文件：

- `go.mod`
- `*_test.go`

常见信号：

- `func TestXxx`
- `func BenchmarkXxx`
- table-driven tests

## Java / Kotlin

常见文件：

- `pom.xml`
- `build.gradle`
- `build.gradle.kts`

常见信号：

- JUnit `@Test`
- Mockito
- Spring test

## 通用 CI 入口

读取：

- `.github/workflows/`
- `.gitlab-ci.yml`
- `Makefile`
- `justfile`
- `Taskfile.yml`
- package scripts

优先复用项目已有 test/fast/unit 入口，避免发明新的测试命令。
