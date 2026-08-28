"""Built-in mode mapping contracts."""

from plugins.DicePP.module.common.mode_defs import (
    default_dice_for_mode,
    lookup_mode,
    query_database_for_mode,
)


def test_builtin_mode_lookup_is_case_insensitive_and_keeps_mixed_dnd_database():
    definition = lookup_mode("dnd5e混用")

    assert definition is not None
    assert definition.mode == "DND5E混用"
    assert definition.default_dice == "20"
    assert definition.query_database == "DND5E混合"


def test_coc7_uses_d100_default():
    assert default_dice_for_mode("cOc7") == "100"


def test_dynamic_mode_uses_its_name_as_database_and_guesses_dice():
    assert query_database_for_mode("my-coc-database") == "my-coc-database"
    assert default_dice_for_mode("my-coc-database") == "100"
