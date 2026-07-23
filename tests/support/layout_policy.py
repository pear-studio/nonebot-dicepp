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
CANONICAL_IMPORT_LAYERS = RUNNABLE_LAYERS | frozenset({"support"})
DICEPP_PACKAGE_PREFIX = "plugins" + ".DicePP"
DICEPP_INTERNAL_ROOTS = frozenset(
    {"core", "module", "utils", "adapter", "shell", "frozen"}
)
DICEPP_LEGACY_IMPORT_ROOT = "DicePP"
DICEPP_LEGACY_IMPORT_ROOTS = DICEPP_INTERNAL_ROOTS | frozenset(
    {DICEPP_LEGACY_IMPORT_ROOT}
)
DICEPP_LEGACY_IMPORT_PATH_PARTS = ("src", "plugins")
DICEPP_LEGACY_PACKAGE_DIR = "dicepp"
LEGACY_IMPORT_FAILURE_PROBES = {
    "LEGACY_BARE_IMPORT_FAILURE_PROBE": "core.command",
    "LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE": "DicePP.core.command",
}
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
        self.path_tails: dict[str, tuple[str, ...]] = {}
        self.imported_modules: set[str] = set()
        self.violations: list[Violation] = []
        self._legacy_import_failure_probes_used: set[str] = set()
        self._legacy_import_failure_probe_assertion_depth = 0
        self._legacy_import_failure_probe_call_ids: set[int] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imported_modules.add(alias.name)
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local_name] = alias.name if alias.asname else local_name
            if _is_conftest_module(alias.name):
                self._add(node, "IMP001", "tests must not import conftest")
            if self._uses_noncanonical_dicepp_import(alias.name):
                self._add_noncanonical_import(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self.imported_modules.add(module)
        if _is_conftest_module(module):
            self._add(node, "IMP001", "tests must not import conftest")
        if self._uses_noncanonical_dicepp_import(module):
            self._add_noncanonical_import(node)
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = qualified
            if alias.name == "conftest" or _is_conftest_module(qualified):
                self._add(node, "IMP001", "tests must not import conftest")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_assigned_value(node.targets, self._assigned_value(node.value))
        self._record_assigned_path_tail(node.targets, node.value)
        if (
            self._assigns_sys_path(node.targets)
            or self._assigns_pythonpath(node.targets)
        ) and self._contains_dicepp_legacy_import_path(node.value):
            self._add_dicepp_legacy_import_path_exposure(node)
        if self._environment_mapping_exposes_dicepp_legacy_import_path(node.value):
            self._add_dicepp_legacy_import_path_exposure(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assigned_value(
            (node.target,),
            self._assigned_value(node.value) if node.value is not None else None,
        )
        if node.value is not None:
            self._record_assigned_path_tail((node.target,), node.value)
            if (
                self._assigns_sys_path((node.target,))
                or self._assigns_pythonpath((node.target,))
            ) and self._contains_dicepp_legacy_import_path(node.value):
                self._add_dicepp_legacy_import_path_exposure(node)
            if self._environment_mapping_exposes_dicepp_legacy_import_path(node.value):
                self._add_dicepp_legacy_import_path_exposure(node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if (
            self._is_sys_path_target(node.target)
            or self._is_pythonpath_target(node.target)
        ) and self._contains_dicepp_legacy_import_path(node.value):
            self._add_dicepp_legacy_import_path_exposure(node)
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

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        is_legacy_probe_assertion = any(
            self._is_module_not_found_error_assertion(item.context_expr)
            for item in node.items
        )
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)

        if is_legacy_probe_assertion:
            self._legacy_import_failure_probe_assertion_depth += 1
        try:
            for statement in node.body:
                direct_call = self._direct_call_statement(statement)
                if is_legacy_probe_assertion and direct_call is not None:
                    self._legacy_import_failure_probe_call_ids.add(id(direct_call))
                try:
                    self.visit(statement)
                finally:
                    if direct_call is not None:
                        self._legacy_import_failure_probe_call_ids.discard(
                            id(direct_call)
                        )
        finally:
            if is_legacy_probe_assertion:
                self._legacy_import_failure_probe_assertion_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self._qualified_name(node.func)
        if self._is_dynamic_import_or_patch_target(qualified) and node.args:
            argument = node.args[0]
            value = self._string_value(argument)
            if (
                value is not None
                and self._uses_noncanonical_dicepp_import(value)
                and not self._is_declared_legacy_import_failure_probe(
                    qualified,
                    node,
                    argument,
                    value,
                )
            ):
                self._add_noncanonical_import(argument)
        if self._adds_dicepp_legacy_import_path_to_sys_path(node, qualified):
            self._add_dicepp_legacy_import_path_exposure(node)
        if self._sets_dicepp_legacy_import_path_in_pythonpath(node, qualified):
            self._add_dicepp_legacy_import_path_exposure(node)
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

    def _assigned_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return self._qualified_value(node)

    def _record_assigned_value(
        self,
        targets: Sequence[ast.expr],
        value: str | None,
    ) -> None:
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if value is None:
                self.values.pop(target.id, None)
            else:
                self.values[target.id] = value

    def _record_assigned_path_tail(
        self,
        targets: Sequence[ast.expr],
        value: ast.AST,
    ) -> None:
        tail = self._path_tail(value)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if tail:
                self.path_tails[target.id] = tail
            else:
                self.path_tails.pop(target.id, None)

    def _string_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        return None

    def _path_tail(self, node: ast.AST) -> tuple[str, ...]:
        """Return the statically visible trailing path components of ``node``."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._normalize_path_tail(
                tuple(
                    part.casefold()
                    for part in node.value.replace("\\", "/").split("/")
                    if part
                )
            )
        if isinstance(node, ast.Name):
            return self.path_tails.get(node.id, ())
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            return self._normalize_path_tail(
                self._path_tail(node.left) + self._path_tail(node.right)
            )
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self._parent_path_tail(self._path_tail(node.value))
        if isinstance(node, ast.JoinedStr):
            return self._join_path_tails(
                tuple(
                    value.value
                    if isinstance(value, ast.FormattedValue)
                    else value
                    for value in node.values
                )
            )
        if not isinstance(node, ast.Call):
            return ()

        qualified = self._qualified_name(node.func)
        if qualified in {"str", "os.fspath"} and node.args:
            return self._path_tail(node.args[0])
        if qualified in {"pathlib.Path", "pathlib.PurePath"}:
            return self._join_path_tails(node.args)
        if qualified in {"pathlib.Path.resolve", "pathlib.Path.absolute"}:
            return self._path_tail(node.func.value)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"resolve", "absolute"}
        ):
            return self._path_tail(node.func.value)
        if qualified in {
            "os.path.abspath",
            "os.path.normpath",
            "os.path.realpath",
        } and node.args:
            return self._path_tail(node.args[0])
        if qualified == "os.path.join" or (
            isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath"
        ):
            base = (
                self._path_tail(node.func.value)
                if isinstance(node.func, ast.Attribute)
                and node.func.attr == "joinpath"
                else ()
            )
            return self._normalize_path_tail(
                base + self._join_path_tails(node.args)
            )
        return ()

    def _join_path_tails(self, nodes: Sequence[ast.AST]) -> tuple[str, ...]:
        return self._normalize_path_tail(
            tuple(part for node in nodes for part in self._path_tail(node))
        )

    @staticmethod
    def _normalize_path_tail(tail: tuple[str, ...]) -> tuple[str, ...]:
        """Lexically normalize the known tail without resolving the filesystem."""
        normalized: list[str] = []
        for part in tail:
            if not part or part == ".":
                continue
            if part == "..":
                if normalized and normalized[-1] != "..":
                    normalized.pop()
                else:
                    normalized.append(part)
                continue
            normalized.append(part)
        return tuple(normalized)

    @classmethod
    def _parent_path_tail(cls, tail: tuple[str, ...]) -> tuple[str, ...]:
        return cls._normalize_path_tail(tail)[:-1]

    def _is_dicepp_legacy_import_path(self, node: ast.AST) -> bool:
        literal = self._string_value(node)
        if literal is not None and self._literal_contains_dicepp_legacy_import_path(
            literal
        ):
            return True
        tail = self._path_tail(node)
        return self._tail_exposes_dicepp_legacy_import_path(tail)

    @staticmethod
    def _tail_exposes_dicepp_legacy_import_path(tail: tuple[str, ...]) -> bool:
        """Return whether a path tail can introduce a second DicePP identity."""
        tail = _PolicyVisitor._normalize_path_tail(tail)
        length = len(DICEPP_LEGACY_IMPORT_PATH_PARTS)
        for index in range(len(tail) - length + 1):
            if tail[index : index + length] != DICEPP_LEGACY_IMPORT_PATH_PARTS:
                continue
            child_index = index + length
            if (
                child_index == len(tail)
                or tail[child_index] == DICEPP_LEGACY_PACKAGE_DIR
            ):
                return True
        return False

    @staticmethod
    def _literal_contains_dicepp_legacy_import_path(value: str) -> bool:
        """Recognize an import path embedded in a literal PYTHONPATH list."""
        normalized = value.replace("\\", "/").casefold()
        entries = normalized.replace(";", ":").split(":")
        return any(
            _PolicyVisitor._tail_exposes_dicepp_legacy_import_path(
                _PolicyVisitor._normalize_path_tail(
                    tuple(part for part in entry.split("/") if part)
                )
            )
            for entry in entries
        )

    def _contains_dicepp_legacy_import_path(self, node: ast.AST) -> bool:
        """Recognize a legacy import-path entry inside a path-list expression."""
        if self._is_dicepp_legacy_import_path(node):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(
                self._contains_dicepp_legacy_import_path(element)
                for element in node.elts
            )
        if isinstance(node, ast.Dict):
            return any(
                value is not None
                and self._contains_dicepp_legacy_import_path(value)
                for value in node.values
            )
        if isinstance(node, ast.JoinedStr):
            return any(
                self._contains_dicepp_legacy_import_path(value.value)
                for value in node.values
                if isinstance(value, ast.FormattedValue)
            )
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                return False
            return self._contains_dicepp_legacy_import_path(
                node.left
            ) or self._contains_dicepp_legacy_import_path(node.right)
        if isinstance(node, ast.IfExp):
            return self._contains_dicepp_legacy_import_path(
                node.body
            ) or self._contains_dicepp_legacy_import_path(node.orelse)
        if isinstance(node, ast.Call) and self._is_path_list_join(node):
            return any(
                self._contains_dicepp_legacy_import_path(argument)
                for argument in node.args
            )
        return False

    def _assigns_sys_path(self, targets: Sequence[ast.expr]) -> bool:
        return any(self._is_sys_path_target(target) for target in targets)

    def _assigns_pythonpath(self, targets: Sequence[ast.expr]) -> bool:
        return any(self._is_pythonpath_target(target) for target in targets)

    def _is_sys_path_target(self, node: ast.AST) -> bool:
        if self._qualified_name(node) == "sys.path":
            return True
        return (
            isinstance(node, ast.Subscript)
            and self._qualified_name(node.value) == "sys.path"
        )

    def _is_pythonpath_target(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and self._string_value(node.slice) == "PYTHONPATH"
        )

    def _adds_dicepp_legacy_import_path_to_sys_path(
        self,
        node: ast.Call,
        qualified: str | None,
    ) -> bool:
        if qualified == "sys.path.insert":
            arguments = node.args[1:]
        elif qualified in {"sys.path.append", "sys.path.extend"}:
            arguments = node.args
        else:
            return False
        return any(
            self._contains_dicepp_legacy_import_path(argument)
            for argument in arguments
        )

    def _sets_dicepp_legacy_import_path_in_pythonpath(
        self,
        node: ast.Call,
        qualified: str | None,
    ) -> bool:
        if qualified in {"os.putenv", "os.environ.__setitem__"}:
            return (
                len(node.args) >= 2
                and self._string_value(node.args[0]) == "PYTHONPATH"
                and self._contains_dicepp_legacy_import_path(node.args[1])
            )
        if qualified and qualified.endswith(".setenv"):
            return (
                len(node.args) >= 2
                and self._string_value(node.args[0]) == "PYTHONPATH"
                and self._contains_dicepp_legacy_import_path(node.args[1])
            )
        if self._process_call_accepts_environment(qualified):
            return any(
                keyword.arg == "env"
                and self._environment_mapping_exposes_dicepp_legacy_import_path(
                    keyword.value
                )
                for keyword in node.keywords
            )
        if not qualified or not qualified.endswith(".update"):
            return False
        for argument in node.args:
            if not isinstance(argument, ast.Dict):
                continue
            for key, value in zip(argument.keys, argument.values):
                if (
                    key is not None
                    and value is not None
                    and self._string_value(key) == "PYTHONPATH"
                    and self._contains_dicepp_legacy_import_path(value)
                ):
                    return True
        return any(
            keyword.arg == "PYTHONPATH"
            and self._contains_dicepp_legacy_import_path(keyword.value)
            for keyword in node.keywords
        )

    def _environment_mapping_exposes_dicepp_legacy_import_path(
        self,
        node: ast.AST,
    ) -> bool:
        if isinstance(node, ast.Dict):
            return any(
                key is not None
                and value is not None
                and self._string_value(key) == "PYTHONPATH"
                and self._contains_dicepp_legacy_import_path(value)
                for key, value in zip(node.keys, node.values)
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._environment_mapping_exposes_dicepp_legacy_import_path(
                node.left
            ) or self._environment_mapping_exposes_dicepp_legacy_import_path(node.right)
        if isinstance(node, ast.Call) and self._qualified_name(node.func) == "dict":
            return any(
                keyword.arg == "PYTHONPATH"
                and self._contains_dicepp_legacy_import_path(keyword.value)
                for keyword in node.keywords
            ) or any(
                self._environment_mapping_exposes_dicepp_legacy_import_path(argument)
                for argument in node.args
            )
        return False

    @staticmethod
    def _process_call_accepts_environment(qualified: str | None) -> bool:
        return qualified in {
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
            "subprocess.run",
            "asyncio.create_subprocess_exec",
            "asyncio.create_subprocess_shell",
            "asyncio.subprocess.create_subprocess_exec",
            "asyncio.subprocess.create_subprocess_shell",
        }

    def _is_path_list_join(self, node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and self._qualified_name(node.func) != "os.path.join"
        )

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

    def _uses_noncanonical_dicepp_import(self, value: str) -> bool:
        return (
            self.layer in CANONICAL_IMPORT_LAYERS
            and value.split(".", 1)[0] in DICEPP_LEGACY_IMPORT_ROOTS
        )

    def _is_declared_legacy_import_failure_probe(
        self,
        qualified: str | None,
        node: ast.Call,
        argument: ast.AST,
        value: str,
    ) -> bool:
        """Permit only the named regression probes that prove legacy names fail."""
        if qualified != "importlib.import_module":
            return False
        if not isinstance(argument, ast.Name):
            return False
        if argument.id in self._legacy_import_failure_probes_used:
            return False
        if self._legacy_import_failure_probe_assertion_depth == 0:
            return False
        if id(node) not in self._legacy_import_failure_probe_call_ids:
            return False
        if LEGACY_IMPORT_FAILURE_PROBES.get(argument.id) != value:
            return False
        if not (
            self.path.name == "test_internal_import_namespace.py"
            and self.path.parent.name == "repository"
            and self.path.parent.parent.name == "integration"
        ):
            return False
        self._legacy_import_failure_probes_used.add(argument.id)
        return True

    def _is_module_not_found_error_assertion(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if self._qualified_name(node.func) != "pytest.raises" or not node.args:
            return False
        return self._qualified_name(node.args[0]) in {
            "ModuleNotFoundError",
            "builtins.ModuleNotFoundError",
        }

    @staticmethod
    def _direct_call_statement(statement: ast.stmt) -> ast.Call | None:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            return statement.value
        return None

    @staticmethod
    def _is_dynamic_import_or_patch_target(qualified: str | None) -> bool:
        return qualified in {
            "importlib.import_module",
            "__import__",
            "builtins.__import__",
            "unittest.mock.patch",
            "unittest.mock.patch.object",
            "monkeypatch.setattr",
            "pytest.MonkeyPatch.setattr",
        }

    def _add_noncanonical_import(self, node: ast.AST) -> None:
        self._add(
            node,
            "IMP002",
            "tests must use canonical plugins.DicePP imports instead of legacy "
            "core/module/utils/adapter/shell/frozen or DicePP modules",
        )

    def _add_dicepp_legacy_import_path_exposure(self, node: ast.AST) -> None:
        self._add(
            node,
            "PTH002",
            "tests must not expose src/plugins itself or src/plugins/DicePP "
            "(and descendants) through sys.path or PYTHONPATH; controlled "
            "process tests must expose src instead",
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
            'plugins.DicePP.core.bot.Bot'
        )
    return False


def render_violations(
    violations: Sequence[Violation],
    *,
    root: Path | None = None,
) -> str:
    return "\n".join(violation.render(root) for violation in violations)
