"""Fail-fast validation for DicePP's managed NoneBot plugin registration.

NoneBot's :func:`nonebot.load_plugin` deliberately converts many import
failures into a ``None`` result.  DicePP is a runtime application rather than
an optional plugin host, so its entrypoint must turn that ambiguous state into
an actionable startup error before an ASGI server is exposed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sized
from typing import Any


DICEPP_PLUGIN_MODULE_NAME = "plugins.DicePP.plugin"


class DicePPPluginPreflightError(RuntimeError):
    """Raised when the canonical DicePP NoneBot plugin is not usable."""


PluginLoader = Callable[[str], Any | None]
PluginLookup = Callable[[str], Any | None]
RegistryProvider = Callable[[], Sized]
CommandMatcherProvider = Callable[[], Any]


def load_and_validate_dicepp_plugin(
    *,
    plugin_loader: PluginLoader | None = None,
    plugin_lookup: PluginLookup | None = None,
    registry_provider: RegistryProvider | None = None,
    command_matcher_provider: CommandMatcherProvider | None = None,
) -> Any:
    """Load DicePP through NoneBot and prove its registration is healthy.

    The injectable collaborators keep the failure semantics independently
    testable without resetting NoneBot's process-global plugin manager.
    """
    if plugin_loader is None or plugin_lookup is None:
        import nonebot

        plugin_loader = plugin_loader or nonebot.load_plugin
        plugin_lookup = plugin_lookup or nonebot.get_plugin_by_module_name

    try:
        plugin = plugin_loader(DICEPP_PLUGIN_MODULE_NAME)
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP plugin loader raised "
            f"{type(exc).__name__} while loading "
            f"{DICEPP_PLUGIN_MODULE_NAME}: {exc}"
        ) from exc

    return validate_registered_dicepp_plugin(
        plugin,
        plugin_lookup=plugin_lookup,
        registry_provider=registry_provider,
        command_matcher_provider=command_matcher_provider,
    )


def validate_registered_dicepp_plugin(
    plugin: Any | None,
    *,
    plugin_lookup: PluginLookup | None = None,
    registry_provider: RegistryProvider | None = None,
    command_matcher_provider: CommandMatcherProvider | None = None,
) -> Any:
    """Validate an already-loaded canonical DicePP plugin.

    This does not import the plugin entrypoint.  It is safe to call from smoke
    checks after the production loader has established the manager's
    registration state.
    """
    if plugin is None:
        raise DicePPPluginPreflightError(
            "DicePP plugin loader returned None for "
            f"{DICEPP_PLUGIN_MODULE_NAME}"
        )

    try:
        module_name = plugin.module_name
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP plugin result has no readable module_name for "
            f"{DICEPP_PLUGIN_MODULE_NAME}: {type(exc).__name__}: {exc}"
        ) from exc
    if module_name != DICEPP_PLUGIN_MODULE_NAME:
        raise DicePPPluginPreflightError(
            "DicePP plugin loader returned the wrong module identity: "
            f"expected {DICEPP_PLUGIN_MODULE_NAME}, got {module_name!r}"
        )

    if plugin_lookup is None:
        import nonebot

        plugin_lookup = nonebot.get_plugin_by_module_name
    try:
        registered_plugin = plugin_lookup(DICEPP_PLUGIN_MODULE_NAME)
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "NoneBot plugin lookup raised "
            f"{type(exc).__name__} for {DICEPP_PLUGIN_MODULE_NAME}: {exc}"
        ) from exc
    if registered_plugin is not plugin:
        if registered_plugin is None:
            detail = "None"
        else:
            detail = (
                "a different plugin instance "
                f"({getattr(registered_plugin, 'module_name', None)!r})"
            )
        raise DicePPPluginPreflightError(
            "NoneBot plugin lookup did not return the managed DicePP plugin: "
            f"got {detail}"
        )

    try:
        matchers = tuple(plugin.matcher)
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP plugin has no readable matcher registration: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not matchers:
        raise DicePPPluginPreflightError(
            "DicePP plugin registered no NoneBot matchers"
        )
    _validate_registered_command_matcher(
        matchers,
        command_matcher_provider=command_matcher_provider,
    )

    registry = _get_command_registry(registry_provider)
    try:
        registry_size = len(registry)
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command registry cannot report its registered commands: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if registry_size == 0:
        raise DicePPPluginPreflightError(
            "DicePP command registration failed: DEFAULT_REGISTRY is empty"
        )

    return plugin


def _validate_registered_command_matcher(
    matchers: Any,
    *,
    command_matcher_provider: CommandMatcherProvider | None,
) -> None:
    """Require the message command matcher itself to be registered and live."""
    command_matcher = _get_command_matcher(command_matcher_provider)
    try:
        is_registered = any(matcher is command_matcher for matcher in matchers)
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command matcher registration cannot be inspected: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not is_registered:
        raise DicePPPluginPreflightError(
            "DicePP command matcher is not registered with the managed plugin"
        )

    try:
        has_handlers = bool(getattr(command_matcher, "handlers", ()))
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command matcher handlers cannot be inspected: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not has_handlers:
        raise DicePPPluginPreflightError(
            "DicePP command matcher registered no NoneBot matcher handlers"
        )


def _get_command_matcher(
    command_matcher_provider: CommandMatcherProvider | None,
) -> Any:
    if command_matcher_provider is None:
        command_matcher_provider = _default_command_matcher_provider
    try:
        command_matcher = command_matcher_provider()
    except DicePPPluginPreflightError:
        raise
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command matcher lookup raised "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if command_matcher is None:
        raise DicePPPluginPreflightError(
            "DicePP command matcher lookup returned None"
        )
    return command_matcher


def _get_command_registry(registry_provider: RegistryProvider | None) -> Sized:
    if registry_provider is None:
        registry_provider = _default_registry_provider
    try:
        registry = registry_provider()
    except DicePPPluginPreflightError:
        raise
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command registry lookup raised "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if registry is None:
        raise DicePPPluginPreflightError(
            "DicePP command registry lookup returned None"
        )
    return registry


def _default_registry_provider() -> Sized:
    try:
        from plugins.DicePP.core.command.user_cmd import DEFAULT_REGISTRY
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command registry import failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return DEFAULT_REGISTRY


def _default_command_matcher_provider() -> Any:
    adapter_module = sys.modules.get("plugins.DicePP.adapter.nonebot_adapter")
    if adapter_module is None:
        raise DicePPPluginPreflightError(
            "DicePP command matcher module was not loaded by the managed plugin"
        )
    try:
        return adapter_module.command_matcher
    except Exception as exc:
        raise DicePPPluginPreflightError(
            "DicePP command matcher lookup failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
