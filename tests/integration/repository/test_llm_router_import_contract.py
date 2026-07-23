"""Static import contract for the Persona LLM router."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def test_router_type_checking_config_import_uses_the_canonical_namespace(
    pytestconfig: pytest.Config,
) -> None:
    repository_root = Path(str(pytestconfig.rootpath))
    source_path = (
        repository_root
        / "src"
        / "plugins"
        / "DicePP"
        / "module"
        / "persona"
        / "llm"
        / "router.py"
    )
    tree = ast.parse(
        source_path.read_text(encoding="utf-8"), filename=str(source_path)
    )

    type_checking_blocks = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    ]
    assert len(type_checking_blocks) == 1
    imports = [
        node
        for node in type_checking_blocks[0].body
        if isinstance(node, ast.ImportFrom)
        and node.module == "plugins.DicePP.core.config.pydantic_models"
    ]

    assert len(imports) == 1
    assert {name.name for name in imports[0].names} == {
        "ModelConfig",
        "PersonaConfig",
        "ProviderConfig",
    }
