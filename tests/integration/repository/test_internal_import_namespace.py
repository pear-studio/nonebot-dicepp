import ast
from pathlib import Path

import pytest


def _package_imports(path: Path) -> list[tuple[int, str]]:
    package_prefix = "plugins" + ".DicePP"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        for module in modules:
            if module == package_prefix or module.startswith(f"{package_prefix}."):
                found.append((node.lineno, module))
    return found


def test_dicepp_internal_modules_use_the_runtime_canonical_namespace(
    pytestconfig: pytest.Config,
) -> None:
    root = Path(str(pytestconfig.rootpath))
    source_root = root / "src" / "plugins" / "DicePP"
    violations = [
        (path.relative_to(root), line, module)
        for path in sorted(source_root.rglob("*.py"))
        for line, module in _package_imports(path)
    ]

    assert violations == [], (
        "DicePP internal modules must import core/module/utils/... directly; "
        "plugins.DicePP.* is reserved for external package boundaries.\n"
        + "\n".join(
            f"{path}:{line}: {module}" for path, line, module in violations
        )
    )
