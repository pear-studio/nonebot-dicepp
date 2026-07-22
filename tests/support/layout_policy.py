"""Static policy checks for the repository's test layout.

The checker deliberately handles only syntactically identifiable boundary
violations. Whether a test is conceptually at the right level remains a review
question; see ``docs/dev/testing.md``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


RUNNABLE_LAYERS = frozenset({"unit", "integration", "system", "external"})
SUPPORT_LAYERS = frozenset({"support", "fixtures"})
ALLOWED_TOP_LEVEL = RUNNABLE_LAYERS | SUPPORT_LAYERS
ALLOWED_ROOT_FILES = frozenset({"__init__.py", "conftest.py"})
QUICK_LAYERS = frozenset({"unit", "integration"})
LEGACY_SELECTION_MARKERS = frozenset(
    {
        "compatibility",
        "e2e",
        "integration",
        "karma",
        "legacy",
        "log",
        "real_llm",
        "slow",
        "unit",
    }
)


@dataclass(frozen=True, order=True)
class Violation:
    """A single actionable layout-policy violation."""

    path: Path
    line: int
    code: str
    message: str

    def render(self, root: Path | None = None) -> str:
        display_path = self.path
        if root is not None:
            try:
                display_path = self.path.relative_to(root)
            except ValueError:
                pass
        return f"{display_path}:{self.line}: {self.code} {self.message}"


@dataclass(frozen=True)
class _Rule:
    code: str
    message: str


UNIT_ONLY_RULES = {
    "sqlite": _Rule("UNT001", "unit tests must not open a real SQLite connection"),
    "bot": _Rule("UNT002", "unit tests must not construct a complete Bot or TestClient"),
    "file": _Rule("UNT003", "unit tests must not perform real filesystem I/O"),
}
SYSTEM_RESOURCE_RULES = {
    "subprocess": _Rule("SYS001", "this test layer must not start subprocesses"),
    "listener": _Rule("SYS002", "this test layer must not bind or listen on real sockets"),
    "server": _Rule("SYS003", "this test layer must not start websocket or ASGI servers"),
    "browser": _Rule("SYS004", "this test layer must not launch a real browser"),
}


def check_test_layout(
    tests_root: Path | str,
) -> list[Violation]:
    """Check one ``tests`` tree without importing or executing test code.

    Project selection markers are discovered from the source itself, so the
    command-line checker and the pytest architecture check enforce identical
    rules.
    """

    root = Path(tests_root).resolve()
    violations = list(_check_top_level(root))

    if root.is_dir():
        python_files = sorted(root.rglob("*.py"))
    elif root.suffix == ".py":
        python_files = [root]
        root = _find_tests_root(root)
    else:
        python_files = []

    for path in python_files:
        layer = _layer_for(path, root)
        violations.extend(_check_source(path, layer))

    return sorted(set(violations))


def _find_tests_root(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == "tests":
            return parent
    return path.parent


def _check_top_level(root: Path) -> Iterator[Violation]:
    if not root.is_dir():
        return

    for child in sorted(root.iterdir()):
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if (
            child.is_dir()
            and child.name not in ALLOWED_TOP_LEVEL
            and _contains_repository_content(child)
        ):
            yield Violation(
                child,
                1,
                "LAY001",
                "top-level test directories must be one of: "
                + ", ".join(sorted(ALLOWED_TOP_LEVEL)),
            )
        elif child.is_file() and child.name not in ALLOWED_ROOT_FILES:
            yield Violation(
                child,
                1,
                "LAY002",
                "files at the tests root are limited to conftest.py and __init__.py",
            )


def _contains_repository_content(directory: Path) -> bool:
    return any(
        "__pycache__" not in path.parts
        for path in directory.rglob("*")
        if path.is_file()
    )


def _layer_for(path: Path, root: Path) -> str | None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else None


def _check_source(path: Path, layer: str | None) -> Iterator[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        line = getattr(exc, "lineno", None) or 1
        yield Violation(path, line, "AST001", f"cannot parse test source: {exc}")
        return

    visitor = _PolicyVisitor(path=path, layer=layer)
    visitor.visit(tree)
    yield from visitor.violations


class _PolicyVisitor(ast.NodeVisitor):
    def __init__(self, *, path: Path, layer: str | None) -> None:
        self.path = path
        self.layer = layer
        self.aliases: dict[str, str] = {}
        self.values: dict[str, str] = {}
        self.imported_modules: set[str] = set()
        self.violations: list[Violation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imported_modules.add(alias.name)
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local_name] = alias.name if alias.asname else local_name
            if _is_conftest_module(alias.name):
                self._add(node, "IMP001", "tests must not import conftest")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.imported_modules.add(module)
        if _is_conftest_module(module):
            self._add(node, "IMP001", "tests must not import conftest")
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = qualified
            if alias.name == "conftest" or _is_conftest_module(qualified):
                self._add(node, "IMP001", "tests must not import conftest")

    def visit_Assign(self, node: ast.Assign) -> None:
        qualified = self._qualified_value(node.value)
        if qualified:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.values[target.id] = qualified
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and isinstance(node.target, ast.Name):
            qualified = self._qualified_value(node.value)
            if qualified:
                self.values[node.target.id] = qualified
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and self._is_file_path_expression(node.value.value)
        ):
            self._add(
                node,
                "PTH001",
                "do not locate the repository with Path(__file__).parents[n]",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        qualified = self._qualified_name(node)
        marker_prefix = "pytest.mark."
        if qualified and qualified.startswith(marker_prefix):
            marker = qualified.removeprefix(marker_prefix).split(".", 1)[0]
            if marker == "quick" and self.layer not in QUICK_LAYERS:
                self._add(
                    node,
                    "QCK001",
                    "quick tests must live under tests/unit or tests/integration",
                )
            elif marker in LEGACY_SELECTION_MARKERS:
                self._add(
                    node,
                    "MRK001",
                    f"legacy project selection marker is not allowed: {marker}",
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self._qualified_name(node.func)
        category = self._category_for_call(node, qualified)
        if category is not None:
            rule = self._rule_for_category(category)
            if rule is not None:
                self._add(node, rule.code, rule.message)
        self.generic_visit(node)

    def _qualified_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.values.get(node.id) or self.aliases.get(node.id) or node.id
        if isinstance(node, ast.Attribute):
            base = self._qualified_name(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Call):
            return self._qualified_name(node.func)
        return None

    def _qualified_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Call):
            return self._qualified_name(node.func)
        return self._qualified_name(node)

    def _category_for_call(self, node: ast.Call, qualified: str | None) -> str | None:
        if qualified is None:
            return None
        if qualified in {"sqlite3.connect", "aiosqlite.connect"}:
            return "sqlite"
        if _is_bot_constructor(qualified):
            return "bot"
        if qualified in {
            "open",
            "pathlib.Path.mkdir",
            "pathlib.Path.open",
            "pathlib.Path.read_bytes",
            "pathlib.Path.read_text",
            "pathlib.Path.touch",
            "pathlib.Path.unlink",
            "pathlib.Path.write_bytes",
            "pathlib.Path.write_text",
            "shutil.copy",
            "shutil.copy2",
            "shutil.copyfile",
            "shutil.copytree",
            "shutil.move",
            "shutil.rmtree",
            "tempfile.NamedTemporaryFile",
            "tempfile.TemporaryDirectory",
            "zipfile.ZipFile",
        }:
            return "file"
        if qualified in {
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
            "subprocess.run",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "asyncio.subprocess.create_subprocess_exec",
            "asyncio.subprocess.create_subprocess_shell",
            "os.popen",
            "os.system",
        }:
            return "subprocess"
        if qualified.endswith(".bind") or qualified.endswith(".listen"):
            receiver = qualified.rsplit(".", 1)[0]
            if (
                receiver == "socket.socket"
                or receiver.startswith("socket.socket.")
                or self._imports("socket")
            ):
                return "listener"
        if qualified.endswith(".serve") and self._imports("websockets"):
            return "server"
        if qualified in {"uvicorn.run", "uvicorn.Server.serve"}:
            return "server"
        if qualified.endswith(".serve") and self._imports("uvicorn"):
            return "server"
        if qualified.endswith(".launch") and self._imports("playwright"):
            return "browser"
        return None

    def _rule_for_category(self, category: str) -> _Rule | None:
        if self.layer == "unit":
            return UNIT_ONLY_RULES.get(category) or SYSTEM_RESOURCE_RULES.get(category)
        if self.layer == "integration":
            return SYSTEM_RESOURCE_RULES.get(category)
        return None

    def _imports(self, prefix: str) -> bool:
        return any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in self.imported_modules
        )

    def _is_file_path_expression(self, node: ast.AST) -> bool:
        while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in {"absolute", "resolve"}:
                break
            node = node.func.value
        if not isinstance(node, ast.Call) or not node.args:
            return False
        return (
            self._qualified_name(node.func) == "pathlib.Path"
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "__file__"
        )

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        violation = Violation(self.path, getattr(node, "lineno", 1), code, message)
        if violation not in self.violations:
            self.violations.append(violation)


def _is_conftest_module(module: str) -> bool:
    return module == "conftest" or module.endswith(".conftest")


def _is_bot_constructor(qualified: str) -> bool:
    if qualified.endswith(".TestClient"):
        return qualified.startswith(("fastapi.", "starlette."))
    if qualified.endswith(".Bot"):
        return qualified.startswith(("nonebot.", "nonebot2.")) or qualified.endswith(
            "core.bot.Bot"
        )
    return False


def render_violations(
    violations: Sequence[Violation],
    *,
    root: Path | None = None,
) -> str:
    return "\n".join(violation.render(root) for violation in violations)
