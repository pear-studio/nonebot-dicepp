"""Runtime smoke checks for an already-initialized DicePP process.

This module intentionally imports only the standard library at module import
time.  ``bot.py`` initializes NoneBot, registers the OneBot adapter, and loads
the canonical DicePP plugin through NoneBot before calling
``run_smoke_check``.  Re-importing the plugin here would bypass that managed
registration path and can poison ``sys.modules`` after a failed import.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from typing import Any


PluginValidator = Callable[[Any], Any]


def run_smoke_check(
    plugin: Any | None,
    *,
    plugin_validator: PluginValidator | None = None,
) -> bool:
    """Run frozen, dependency, managed-plugin, and version checks.

    ``plugin`` must be the object returned by the production managed loader;
    this function never imports ``plugins.DicePP.plugin`` directly.
    """
    errors: list[str] = []

    errors.extend(_check_frozen_env())
    errors.extend(_check_critical_modules())
    errors.extend(
        _check_registered_dicepp_plugin(plugin, plugin_validator=plugin_validator)
    )
    errors.extend(_check_version())

    if errors:
        print(f"\nSMOKE CHECK FAILED: {len(errors)} error(s)")
        for error in errors:
            print(f"  FAIL: {error}")
        return False

    print(
        "\nSMOKE CHECK PASSED: "
        "plugin=plugins.DicePP.plugin matchers=nonempty registry=nonempty"
    )
    return True


def _check_frozen_env() -> list[str]:
    """Validate frozen-environment detection and path helpers."""
    errors = []
    from plugins.DicePP import frozen

    if frozen.is_frozen() != getattr(sys, "frozen", False):
        errors.append("is_frozen() mismatch with sys.frozen")

    app_dir = frozen.get_app_dir()
    if not app_dir or not os.path.isabs(app_dir):
        errors.append(f"get_app_dir() returned invalid: {app_dir}")

    info = frozen.get_runtime_info()
    for key in ("frozen", "app_dir", "project_root", "executable", "cwd"):
        if key not in info:
            errors.append(f"get_runtime_info() missing key: {key}")

    return errors


def _check_critical_modules() -> list[str]:
    """Validate that dependencies needed by the packaged runtime can import."""
    errors = []
    critical_modules = [
        # Reached from DicePP's ordinary plugin graph; keep it explicit here
        # so the frozen smoke check also proves shared project metadata works.
        "dicepp_meta",
        "dicepp_security.private_token",
        "lark",
        "aiosqlite",
        "rsa",
        "zhconv",
        "openpyxl",
        "requests",
        "charset_normalizer",
        "chardet",
        "docx",
        "lxml",
        "loguru",
        "aiohttp",
        "aiofiles",
        "uvicorn",
        "fastapi",
        "pydantic",
        "cryptography.fernet",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
    ]
    for module_name in critical_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(
                f"Module '{module_name}' import failed: "
                f"{type(exc).__name__}: {exc}"
            )
    return errors


def _check_registered_dicepp_plugin(
    plugin: Any | None,
    *,
    plugin_validator: PluginValidator | None = None,
) -> list[str]:
    """Validate the plugin registered by NoneBot without importing it again."""
    if plugin_validator is None:
        from plugins.DicePP.runtime_preflight import validate_registered_dicepp_plugin

        plugin_validator = validate_registered_dicepp_plugin

    try:
        plugin_validator(plugin)
    except Exception as exc:
        return [
            "Registered DicePP plugin validation failed: "
            f"{type(exc).__name__}: {exc}"
        ]
    return []


def _check_version() -> list[str]:
    """Validate that package metadata exposes a conventional DicePP version."""
    import re
    from importlib.metadata import version

    try:
        ver = version("dicepp")
    except Exception as exc:
        return [
            "DicePP version metadata lookup failed: "
            f"{type(exc).__name__}: {exc}"
        ]
    if not re.match(r"^\d+\.\d+\.\d+", ver):
        return [f"Version format invalid: {ver}"]
    return []
