from __future__ import annotations

from pathlib import Path

from dicepp_data import (
    BOT_CORE_SCHEMA,
    BOT_LOG_SCHEMA,
    INSTANCE_SCHEMA,
    PERSONA_SCHEMA,
)


def test_catalog_schema_references_match_runtime_migration_targets() -> None:
    from plugins.DicePP.core.data.schema import (
        BOT_CORE_TARGET,
        BOT_LOG_TARGET,
        INSTANCE_TARGET,
    )
    from plugins.DicePP.module.persona.data.schema import PERSONA_TARGET

    for reference, target in (
        (INSTANCE_SCHEMA, INSTANCE_TARGET),
        (BOT_CORE_SCHEMA, BOT_CORE_TARGET),
        (BOT_LOG_SCHEMA, BOT_LOG_TARGET),
        (PERSONA_SCHEMA, PERSONA_TARGET),
    ):
        reference.validate_target(target)


def test_default_persona_is_a_packaged_template_not_instance_content(
    pytestconfig,
) -> None:
    root = Path(str(pytestconfig.rootpath))

    assert (root / "templates" / "characters" / "default" / "character.yaml").is_file()
    assert (root / "templates" / "characters" / "default" / "skin.yaml").is_file()
    assert not (
        root / "content" / "characters" / "default" / "character.yaml"
    ).exists()
    assert not (root / "content" / "characters" / "default" / "skin.yaml").exists()

    bot_spec = (root / "scripts" / "build" / "dicepp.spec").read_text(encoding="utf-8")
    dashboard_spec = (root / "scripts" / "build" / "dashboard.spec").read_text(
        encoding="utf-8"
    )
    assert "'templates'" in bot_spec
    assert '"templates"' in dashboard_spec
