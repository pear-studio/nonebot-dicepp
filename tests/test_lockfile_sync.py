"""验证 uv.lock 中项目版本与 pyproject.toml 一致。

此测试作为 CI 门禁：当 pyproject.toml 版本变更但 uv.lock 未同步更新时，
后续任意 uv sync/uv lock 调用都会被"污染"到无关提交中。

此测试也是 version-release skill 的冗余守卫 — skill 中的 pre_commit_hooks
应保证发版提交包含同步后的 uv.lock，此测试兜底捕获任何遗漏。
"""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
LOCKFILE = PROJECT_ROOT / "uv.lock"


def _read_pyproject_version() -> str:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def _read_lockfile_dicepp_version() -> str:
    with open(LOCKFILE, "rb") as f:
        data = tomllib.load(f)
    for pkg in data.get("package", []):
        if pkg.get("name") == "dicepp":
            return pkg["version"]
    raise AssertionError("uv.lock 中未找到 dicepp 包条目")


def test_lockfile_version_matches_pyproject():
    """uv.lock 记录的 dicepp 版本必须与 pyproject.toml 一致。"""
    pyproject_ver = _read_pyproject_version()
    lockfile_ver = _read_lockfile_dicepp_version()
    assert lockfile_ver == pyproject_ver, (
        f"版本不同步：pyproject.toml={pyproject_ver}，uv.lock={lockfile_ver}。\n"
        "请运行 `uv lock && git add uv.lock` 并提交。"
    )
