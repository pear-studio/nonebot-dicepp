"""Disabled DiceHub command dispatch contracts."""

from __future__ import annotations


class _ForbiddenHubAccess:
    """Fail if disabled message dispatch touches any DiceHub integration path."""

    def __getattribute__(self, name: str):
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        raise AssertionError(f"disabled DiceHub command accessed hub_manager.{name}")


async def test_hub_messages_are_ignored_without_touching_dicehub(bot, h, monkeypatch):
    monkeypatch.setattr(bot, "hub_manager", _ForbiddenHubAccess())

    for message in (".hub", ".hub online"):
        await h.send_private(
            message,
            user_id="test_master",
            target_checker=lambda commands: commands == [],
        )

    await h.send_private(
        ".help",
        user_id="test_master",
        target_checker=lambda commands: (
            len(commands) == 1 and bool(getattr(commands[0], "msg", ""))
        ),
    )
