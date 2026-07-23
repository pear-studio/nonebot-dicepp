"""Contracts for DicePP's managed NoneBot plugin preflight."""

from __future__ import annotations

import pytest

from plugins.DicePP.runtime_preflight import (
    DICEPP_PLUGIN_MODULE_NAME,
    DicePPPluginPreflightError,
    load_and_validate_dicepp_plugin,
    validate_registered_dicepp_plugin,
)


pytestmark = pytest.mark.quick


class _Plugin:
    def __init__(
        self,
        *,
        module_name: str = DICEPP_PLUGIN_MODULE_NAME,
        matcher: object | None = None,
        command_matcher: object | None = None,
    ) -> None:
        self.module_name = module_name
        self.command_matcher = _Matcher() if command_matcher is None else command_matcher
        self.matcher = {self.command_matcher} if matcher is None else matcher


class _Matcher:
    def __init__(self, handlers: object = (object(),)) -> None:
        self.handlers = handlers


def _nonempty_registry() -> tuple[str]:
    return ("registered-command",)


def _command_matcher_for(plugin: _Plugin):
    return plugin.command_matcher


def test_managed_loader_returns_the_registered_canonical_plugin() -> None:
    plugin = _Plugin()
    loaded_names: list[str] = []

    loaded = load_and_validate_dicepp_plugin(
        plugin_loader=lambda module_name: loaded_names.append(module_name) or plugin,
        plugin_lookup=lambda module_name: plugin,
        registry_provider=_nonempty_registry,
        command_matcher_provider=lambda: _command_matcher_for(plugin),
    )

    assert loaded is plugin
    assert loaded_names == [DICEPP_PLUGIN_MODULE_NAME]


def test_managed_loader_rejects_none_return() -> None:
    with pytest.raises(DicePPPluginPreflightError, match="loader returned None"):
        load_and_validate_dicepp_plugin(
            plugin_loader=lambda module_name: None,
            plugin_lookup=lambda module_name: None,
            registry_provider=_nonempty_registry,
        )


def test_managed_loader_wraps_loader_exception() -> None:
    def raise_import_failure(module_name: str) -> None:
        raise ImportError(f"missing dependency for {module_name}")

    with pytest.raises(
        DicePPPluginPreflightError,
        match="loader raised ImportError",
    ):
        load_and_validate_dicepp_plugin(
            plugin_loader=raise_import_failure,
            plugin_lookup=lambda module_name: None,
            registry_provider=_nonempty_registry,
        )


def test_preflight_rejects_a_plugin_lookup_for_a_different_instance() -> None:
    plugin = _Plugin()
    different_plugin = _Plugin()

    with pytest.raises(
        DicePPPluginPreflightError,
        match="lookup did not return the managed DicePP plugin",
    ):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: different_plugin,
            registry_provider=_nonempty_registry,
        )


def test_preflight_rejects_empty_matcher_registration() -> None:
    plugin = _Plugin(matcher=set())

    with pytest.raises(DicePPPluginPreflightError, match="registered no NoneBot matchers"):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: plugin,
            registry_provider=_nonempty_registry,
            command_matcher_provider=lambda: _command_matcher_for(plugin),
        )


def test_preflight_rejects_an_unhandled_command_matcher() -> None:
    command_matcher = _Matcher(handlers=())
    plugin = _Plugin(
        matcher={command_matcher},
        command_matcher=command_matcher,
    )

    with pytest.raises(
        DicePPPluginPreflightError,
        match="command matcher registered no NoneBot matcher handlers",
    ):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: plugin,
            registry_provider=_nonempty_registry,
            command_matcher_provider=lambda: _command_matcher_for(plugin),
        )


def test_preflight_accepts_auxiliary_matchers_without_handlers() -> None:
    command_matcher = _Matcher()
    plugin = _Plugin(
        matcher={_Matcher(handlers=()), command_matcher},
        command_matcher=command_matcher,
    )

    assert validate_registered_dicepp_plugin(
        plugin,
        plugin_lookup=lambda module_name: plugin,
        registry_provider=_nonempty_registry,
        command_matcher_provider=lambda: _command_matcher_for(plugin),
    ) is plugin


def test_preflight_rejects_an_unhandled_command_matcher_with_live_auxiliary() -> None:
    command_matcher = _Matcher(handlers=())
    auxiliary_matcher = _Matcher()
    plugin = _Plugin(
        matcher={command_matcher, auxiliary_matcher},
        command_matcher=command_matcher,
    )

    with pytest.raises(
        DicePPPluginPreflightError,
        match="command matcher registered no NoneBot matcher handlers",
    ):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: plugin,
            registry_provider=_nonempty_registry,
            command_matcher_provider=lambda: _command_matcher_for(plugin),
        )


def test_preflight_rejects_a_command_matcher_missing_from_plugin() -> None:
    command_matcher = _Matcher()
    plugin = _Plugin(
        matcher={_Matcher()},
        command_matcher=command_matcher,
    )

    with pytest.raises(
        DicePPPluginPreflightError,
        match="command matcher is not registered",
    ):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: plugin,
            registry_provider=_nonempty_registry,
            command_matcher_provider=lambda: _command_matcher_for(plugin),
        )


def test_preflight_rejects_an_empty_command_registry() -> None:
    plugin = _Plugin()

    with pytest.raises(DicePPPluginPreflightError, match="DEFAULT_REGISTRY is empty"):
        validate_registered_dicepp_plugin(
            plugin,
            plugin_lookup=lambda module_name: plugin,
            registry_provider=lambda: (),
            command_matcher_provider=lambda: _command_matcher_for(plugin),
        )
