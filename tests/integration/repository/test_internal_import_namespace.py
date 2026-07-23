"""DicePP's single-module-identity import contract."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys

import pytest

from tests.support.layout_policy import check_test_layout


INTERNAL_ROOTS = frozenset(
    {"core", "module", "utils", "adapter", "shell", "frozen"}
)
LEGACY_IMPORT_ROOTS = INTERNAL_ROOTS | {"DicePP"}
LEGACY_BARE_IMPORT_FAILURE_PROBE = "core.command"
LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE = "DicePP.core.command"
DYNAMIC_IMPORT_CALLS = frozenset(
    {"importlib.import_module", "__import__", "builtins.__import__"}
)


def _qualified_name(
    node: ast.AST,
    aliases: dict[str, str | None],
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        return node.id
    if isinstance(node, ast.Attribute):
        base = _qualified_name(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def _string_value(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


class _LegacyDicePPImportAudit:
    """Audit direct imports with local, statement-ordered name resolution.

    Nested bodies receive copies of their enclosing scope. This catches simple
    local aliases and constants while keeping branch, class, and function
    assignments from leaking into parent or sibling scopes. It deliberately
    does not attempt whole-program data-flow analysis.
    """

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def scan_scope(
        self,
        statements: list[ast.stmt],
        *,
        aliases: dict[str, str | None] | None = None,
        constants: dict[str, str] | None = None,
        shadowed_names: tuple[str, ...] = (),
    ) -> None:
        scope_aliases = dict(aliases or {})
        scope_constants = dict(constants or {})
        self._mask_names(scope_aliases, scope_constants, shadowed_names)

        for statement in statements:
            self._scan_statement(statement, scope_aliases, scope_constants)

    def _scan_statement(
        self,
        statement: ast.stmt,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        if isinstance(statement, ast.Import):
            self._record_import(statement, aliases)
            return
        if isinstance(statement, ast.ImportFrom):
            self._record_import_from(statement, aliases)
            return
        if isinstance(statement, ast.Assign):
            self._scan_dynamic_calls(statement.value, aliases, constants)
            self._record_assignment(statement.targets, statement.value, aliases, constants)
            return
        if isinstance(statement, ast.AnnAssign):
            self._scan_dynamic_calls(statement.annotation, aliases, constants)
            if statement.value is not None:
                self._scan_dynamic_calls(statement.value, aliases, constants)
                self._record_assignment(
                    (statement.target,), statement.value, aliases, constants
                )
            return
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._scan_function_header(statement, aliases, constants)
            self.scan_scope(
                statement.body,
                aliases=aliases,
                constants=constants,
                shadowed_names=_function_parameter_names(statement),
            )
            self._mask_names(aliases, constants, (statement.name,))
            return
        if isinstance(statement, ast.ClassDef):
            self._scan_class_header(statement, aliases, constants)
            self.scan_scope(statement.body, aliases=aliases, constants=constants)
            self._mask_names(aliases, constants, (statement.name,))
            return
        if isinstance(statement, ast.If):
            self._scan_if_statement(statement, aliases, constants)
            return
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            self._scan_for_statement(statement, aliases, constants)
            return
        if isinstance(statement, ast.While):
            self._scan_while_statement(statement, aliases, constants)
            return
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            self._scan_with_statement(statement, aliases, constants)
            return
        if isinstance(statement, _TRY_STATEMENTS):
            self._scan_try_statement(statement, aliases, constants)
            return
        if isinstance(statement, ast.Match):
            self._scan_match_statement(statement, aliases, constants)
            return
        self._scan_dynamic_calls(statement, aliases, constants)

    def _record_import(
        self,
        node: ast.Import,
        aliases: dict[str, str | None],
    ) -> None:
        for imported in node.names:
            self._record_module(node.lineno, imported.name)
            local_name = imported.asname or imported.name.split(".", 1)[0]
            aliases[local_name] = imported.name if imported.asname else local_name

    def _record_import_from(
        self,
        node: ast.ImportFrom,
        aliases: dict[str, str | None],
    ) -> None:
        module = node.module or ""
        if node.level == 0:
            self._record_module(node.lineno, module)
        if not node.module:
            return
        for imported in node.names:
            aliases[imported.asname or imported.name] = (
                f"{node.module}.{imported.name}"
            )

    def _record_assignment(
        self,
        targets: list[ast.expr] | tuple[ast.expr, ...],
        value: ast.AST,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        qualified = _qualified_name(value, aliases)
        string_value = _string_value(value, constants)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if qualified in DYNAMIC_IMPORT_CALLS:
                aliases[target.id] = qualified
            else:
                aliases[target.id] = None
            if string_value is not None:
                constants[target.id] = string_value
            else:
                constants.pop(target.id, None)

    def _scan_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        for decorator in node.decorator_list:
            self._scan_dynamic_calls(decorator, aliases, constants)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self._scan_dynamic_calls(default, aliases, constants)

    def _scan_class_header(
        self,
        node: ast.ClassDef,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        for decorator in node.decorator_list:
            self._scan_dynamic_calls(decorator, aliases, constants)
        for base in node.bases:
            self._scan_dynamic_calls(base, aliases, constants)
        for keyword in node.keywords:
            self._scan_dynamic_calls(keyword.value, aliases, constants)

    def _scan_if_statement(
        self,
        node: ast.If,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        self._scan_dynamic_calls(node.test, aliases, constants)
        self.scan_scope(node.body, aliases=aliases, constants=constants)
        self.scan_scope(node.orelse, aliases=aliases, constants=constants)

    def _scan_for_statement(
        self,
        node: ast.For | ast.AsyncFor,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        self._scan_dynamic_calls(node.iter, aliases, constants)
        self.scan_scope(
            node.body,
            aliases=aliases,
            constants=constants,
            shadowed_names=_bound_target_names(node.target),
        )
        self.scan_scope(node.orelse, aliases=aliases, constants=constants)

    def _scan_while_statement(
        self,
        node: ast.While,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        self._scan_dynamic_calls(node.test, aliases, constants)
        self.scan_scope(node.body, aliases=aliases, constants=constants)
        self.scan_scope(node.orelse, aliases=aliases, constants=constants)

    def _scan_with_statement(
        self,
        node: ast.With | ast.AsyncWith,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        for item in node.items:
            self._scan_dynamic_calls(item.context_expr, aliases, constants)
        bound_names = tuple(
            name
            for item in node.items
            if item.optional_vars is not None
            for name in _bound_target_names(item.optional_vars)
        )
        self.scan_scope(
            node.body,
            aliases=aliases,
            constants=constants,
            shadowed_names=bound_names,
        )

    def _scan_try_statement(
        self,
        node: ast.Try,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        self.scan_scope(node.body, aliases=aliases, constants=constants)
        for handler in node.handlers:
            if handler.type is not None:
                self._scan_dynamic_calls(handler.type, aliases, constants)
            self.scan_scope(
                handler.body,
                aliases=aliases,
                constants=constants,
                shadowed_names=(handler.name,) if handler.name else (),
            )
        self.scan_scope(node.orelse, aliases=aliases, constants=constants)
        self.scan_scope(node.finalbody, aliases=aliases, constants=constants)

    def _scan_match_statement(
        self,
        node: ast.Match,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        self._scan_dynamic_calls(node.subject, aliases, constants)
        for case in node.cases:
            case_aliases = dict(aliases)
            case_constants = dict(constants)
            self._mask_names(
                case_aliases,
                case_constants,
                _match_pattern_names(case.pattern),
            )
            if case.guard is not None:
                self._scan_dynamic_calls(case.guard, case_aliases, case_constants)
            self.scan_scope(
                case.body,
                aliases=case_aliases,
                constants=case_constants,
            )

    @staticmethod
    def _mask_names(
        aliases: dict[str, str | None],
        constants: dict[str, str],
        names: tuple[str, ...],
    ) -> None:
        for name in names:
            aliases[name] = None
            constants.pop(name, None)

    def _scan_dynamic_calls(
        self,
        node: ast.AST,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.Call):
            self._record_dynamic_import(node, aliases, constants)
        for child in ast.iter_child_nodes(node):
            self._scan_dynamic_calls(child, aliases, constants)

    def _record_dynamic_import(
        self,
        node: ast.Call,
        aliases: dict[str, str | None],
        constants: dict[str, str],
    ) -> None:
        if _qualified_name(node.func, aliases) not in DYNAMIC_IMPORT_CALLS:
            return
        if not node.args:
            return
        module = _string_value(node.args[0], constants)
        if module is not None:
            self._record_module(node.lineno, module)

    def _record_module(self, line: int, module: str) -> None:
        if module.split(".", 1)[0] in LEGACY_IMPORT_ROOTS:
            self.found.append((line, module))


_TRY_STATEMENTS = (ast.Try, getattr(ast, "TryStar", ast.Try))


def _bound_target_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(name for element in node.elts for name in _bound_target_names(element))
    if isinstance(node, ast.Starred):
        return _bound_target_names(node.value)
    return ()


def _match_pattern_names(node: ast.pattern) -> tuple[str, ...]:
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name is not None:
            names.append(child.name)
        elif isinstance(child, ast.MatchMapping) and child.rest is not None:
            names.append(child.rest)
    return tuple(names)


def _function_parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    names = [argument.arg for argument in arguments]
    if node.args.vararg is not None:
        names.append(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.append(node.args.kwarg.arg)
    return tuple(names)


def _legacy_dicepp_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    audit = _LegacyDicePPImportAudit()
    audit.scan_scope(tree.body)
    return audit.found


def test_legacy_import_static_guard_detects_module_and_function_local_aliases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime.py"
    source.write_text(
        "import builtins\n"
        "import importlib as importer\n"
        "from builtins import __import__ as builtin_import\n"
        "from importlib import import_module as import_module_alias\n"
        "assigned_import = importer.import_module\n"
        "core_target = 'core.command'\n"
        "dicepp_target = 'DicePP.core.command'\n"
        "importer.import_module(core_target)\n"
        "import_module_alias(dicepp_target)\n"
        "builtin_import(core_target)\n"
        "builtins.__import__('DicePP.core.command')\n"
        "assigned_import(dicepp_target)\n"
        "name = 'plugins.DicePP.core.command'\n"
        "def import_local_bare():\n"
        "    import importlib as local_importer\n"
        "    name = 'core.command'\n"
        "    local_target_alias = name\n"
        "    local_load = local_importer.import_module\n"
        "    local_load(local_target_alias)\n"
        "importer.import_module(name)\n"
        "def import_local_top_level():\n"
        "    from importlib import import_module as local_load\n"
        "    local_target = 'DicePP.core.command'\n"
        "    local_load(local_target)\n"
        "def does_not_leak_previous_function_target():\n"
        "    import importlib as local_importer\n"
        "    local_importer.import_module(name)\n",
        encoding="utf-8",
    )

    assert _legacy_dicepp_imports(source) == [
        (8, "core.command"),
        (9, "DicePP.core.command"),
        (10, "core.command"),
        (11, "DicePP.core.command"),
        (12, "DicePP.core.command"),
        (19, "core.command"),
        (24, "DicePP.core.command"),
    ]


def test_legacy_import_static_guard_scans_compounds_in_isolated_scopes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "compound_runtime.py"
    source.write_text(
        "import importlib\n"
        "enabled = True\n"
        "name = 'plugins.DicePP.core.command'\n"
        "if enabled:\n"
        "    importlib.import_module('core.command')\n"
        "try:\n"
        "    import DicePP.core.command\n"
        "except ImportError:\n"
        "    pass\n"
        "for unused in ():\n"
        "    importlib.import_module('DicePP.core.command')\n"
        "with context:\n"
        "    import DicePP.core.command\n"
        "def nested_branch():\n"
        "    import importlib as local_importlib\n"
        "    if enabled:\n"
        "        name = 'core.command'\n"
        "        local_importlib.import_module(name)\n"
        "    local_importlib.import_module(name)\n"
        "class LegacyImports:\n"
        "    import importlib as class_importlib\n"
        "    name = 'DicePP.core.command'\n"
        "    class_importlib.import_module(name)\n"
        "if enabled:\n"
        "    name = 'DicePP.core.command'\n"
        "else:\n"
        "    importlib.import_module(name)\n"
        "importlib.import_module(name)\n"
        "def parameter_shadows_importlib(importlib):\n"
        "    importlib.import_module('DicePP.core.command')\n",
        encoding="utf-8",
    )

    assert _legacy_dicepp_imports(source) == [
        (5, "core.command"),
        (7, "DicePP.core.command"),
        (11, "DicePP.core.command"),
        (13, "DicePP.core.command"),
        (18, "core.command"),
        (23, "DicePP.core.command"),
    ]


def test_dicepp_source_has_no_legacy_internal_imports(
    pytestconfig: pytest.Config,
) -> None:
    root = Path(str(pytestconfig.rootpath))
    source_root = root / "src" / "plugins" / "DicePP"
    violations = [
        (path.relative_to(root), line, module)
        for path in sorted(source_root.rglob("*.py"))
        for line, module in _legacy_dicepp_imports(path)
    ]

    assert violations == [], "\n".join(
        f"{path}:{line}: legacy DicePP import {module}"
        for path, line, module in violations
    )


def test_rexp_sampling_benchmark_uses_only_canonical_import_routes(
    pytestconfig: pytest.Config,
) -> None:
    root = Path(str(pytestconfig.rootpath))
    benchmark = root / "tools" / "bench_rexp_sampling.py"
    legacy_imports = _legacy_dicepp_imports(benchmark)
    legacy_path_exposures = [
        violation
        for violation in check_test_layout(benchmark)
        if violation.code == "PTH002"
    ]

    assert legacy_imports == [], "\n".join(
        f"{benchmark.relative_to(root)}:{line}: legacy DicePP import {module}"
        for line, module in legacy_imports
    )
    assert legacy_path_exposures == [], "\n".join(
        violation.render(root) for violation in legacy_path_exposures
    )


def test_legacy_and_canonical_internal_modules_cannot_coexist(
    pytestconfig: pytest.Config,
) -> None:
    canonical_name = "plugins.DicePP.core.command"
    canonical_module = importlib.import_module(canonical_name)
    package_root = (
        Path(str(pytestconfig.rootpath)) / "src" / "plugins" / "DicePP"
    ).resolve()
    plugins_root = package_root.parent
    resolved_sys_path = {
        Path(entry).resolve() for entry in sys.path if isinstance(entry, str)
    }

    assert package_root not in resolved_sys_path
    assert plugins_root not in resolved_sys_path

    # These are the only declared legacy-name probes allowed by the layout
    # policy: they prove that old identities cannot enter ``sys.modules``.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(LEGACY_BARE_IMPORT_FAILURE_PROBE)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE)

    assert importlib.import_module(canonical_name) is canonical_module
    assert "core" not in sys.modules
    assert "DicePP" not in sys.modules
    assert LEGACY_BARE_IMPORT_FAILURE_PROBE not in sys.modules
    assert LEGACY_TOP_LEVEL_PACKAGE_IMPORT_FAILURE_PROBE not in sys.modules
