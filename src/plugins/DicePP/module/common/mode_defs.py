"""Built-in game mode definitions and small mode resolvers."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ModeDefinition:
    """The fixed settings that a named built-in mode applies."""

    mode: str
    default_dice: str
    query_database: str


BUILTIN_MODES: tuple[ModeDefinition, ...] = (
    ModeDefinition("DND5E2024", "20", "DND5E2024"),
    ModeDefinition("DND5E2014", "20", "DND5E2014"),
    ModeDefinition("DND5E混用", "20", "DND5E混合"),
    ModeDefinition("DND3R", "20", "DND3R"),
    ModeDefinition("PF1E", "20", "PF1E"),
    ModeDefinition("PF2E", "20", "PF2E"),
    ModeDefinition("PF2R", "20", "PF2R"),
    ModeDefinition("COC7", "100", "COC7"),
    ModeDefinition("NECHRONICA", "10", "NECHRONICA"),
)

_MODE_BY_NAME = {definition.mode.casefold(): definition for definition in BUILTIN_MODES}


def lookup_mode(mode: str) -> ModeDefinition | None:
    """Find a built-in mode by name, ignoring case."""

    return _MODE_BY_NAME.get(mode.casefold())


def default_dice_for_mode(mode: str) -> str:
    """Return a mode's default dice, guessing for a dynamic database name."""

    definition = lookup_mode(mode)
    if definition is not None:
        return definition.default_dice

    upper = mode.upper()
    if re.search(r"COC", upper):
        return "100"
    if "忍神" in mode or re.search(r"SHINOBI", upper):
        return "2D6"
    if re.search(r"(DND|D&D|PF|PATHFINDER|SF|STARFINDER|SW|STARWARS)", upper):
        return "20"
    return "20"


def query_database_for_mode(mode: str) -> str:
    """Return a mode's database name, using the mode itself for dynamic modes."""

    definition = lookup_mode(mode)
    return definition.query_database if definition is not None else mode


__all__ = [
    "BUILTIN_MODES",
    "ModeDefinition",
    "default_dice_for_mode",
    "lookup_mode",
    "query_database_for_mode",
]
