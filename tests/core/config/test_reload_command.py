"""
Tests for module/common/reload_config_command.py

Covers:
  9.4  .reload command — permission check, success path, failure/rollback path
"""
import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "src" / "plugins" / "DicePP"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.communication import MessageMetaData, MessageSender
from core.command.bot_cmd import BotSendMsgCommand
from core.config.loader import ConfigValidationError
from module.common.reload_config_command import ReloadConfigCommand


# ── helpers ───────────────────────────────────────────────────────────────────


def _meta(user_id: str, group_id: str = "", permission: int = 4) -> MessageMetaData:
    meta = MessageMetaData(".reload", ".reload", MessageSender(user_id, "user"), group_id, False)
    meta.permission = permission
    return meta


def _make_bot(reload_raises=None):
    """Return a minimal Bot stand-in with mock config, _cfg_loader, _persona_loader, loc_helper."""
    bot = MagicMock()
    bot.account = "test_bot"

    # Loader that succeeds or raises
    cfg_loader = MagicMock()
    new_config = MagicMock()
    new_config.persona = "default"
    if reload_raises:
        cfg_loader.reload.side_effect = reload_raises
    else:
        cfg_loader.reload.return_value = new_config
    bot._cfg_loader = cfg_loader
    bot.config = new_config

    # Persona loader
    persona_loader = MagicMock()
    bot._persona_loader = persona_loader

    # Loc helper
    loc_helper = MagicMock()
    loc_helper.format_loc_text.side_effect = lambda key, **kw: f"[{key}]"
    bot.loc_helper = loc_helper

    return bot, new_config


# ── can_process_msg ───────────────────────────────────────────────────────────


def test_can_process_msg_exact_match():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    proc, _, _ = cmd.can_process_msg(".reload", _meta("u1"))
    assert proc is True


def test_can_process_msg_with_trailing_spaces():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    proc, _, _ = cmd.can_process_msg("  .reload  ", _meta("u1"))
    assert proc is True


def test_can_process_msg_no_match():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    proc, _, _ = cmd.can_process_msg(".r", _meta("u1"))
    assert proc is False


def test_can_process_msg_partial_no_match():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    proc, _, _ = cmd.can_process_msg(".reload extra", _meta("u1"))
    assert proc is False


# ── process_msg: success path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_msg_success_returns_message():
    bot, new_cfg = _make_bot()
    cmd = ReloadConfigCommand(bot)
    results = await cmd.process_msg(".reload", _meta("master1"), None)
    assert len(results) == 1
    assert isinstance(results[0], BotSendMsgCommand)


@pytest.mark.asyncio
async def test_process_msg_success_swaps_config():
    bot, new_cfg = _make_bot()
    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    assert bot.config is new_cfg


@pytest.mark.asyncio
async def test_process_msg_success_reloads_persona():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    bot._persona_loader.reload.assert_called_once()


@pytest.mark.asyncio
async def test_process_msg_success_applies_persona_to_loc():
    bot, new_cfg = _make_bot()
    new_cfg.persona = "kawaii"
    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    bot.loc_helper.reset_to_default.assert_called_once()
    bot.loc_helper.set_persona.assert_called_once_with("kawaii")


@pytest.mark.asyncio
async def test_process_msg_success_uses_ok_loc_key():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    bot.loc_helper.format_loc_text.assert_called()
    called_key = bot.loc_helper.format_loc_text.call_args[0][0]
    assert called_key == "reload_ok"


# ── process_msg: failure / rollback ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_msg_validation_error_keeps_old_config():
    old_cfg = MagicMock()
    old_cfg.persona = "default"
    err = ConfigValidationError("bad config")
    bot, _ = _make_bot(reload_raises=err)
    bot.config = old_cfg

    cmd = ReloadConfigCommand(bot)
    results = await cmd.process_msg(".reload", _meta("master1"), None)

    # config must NOT have been replaced
    assert bot.config is old_cfg
    assert len(results) == 1


@pytest.mark.asyncio
async def test_process_msg_validation_error_uses_fail_loc_key():
    err = ConfigValidationError("bad config")
    bot, _ = _make_bot(reload_raises=err)
    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    called_key = bot.loc_helper.format_loc_text.call_args[0][0]
    assert called_key == "reload_fail"


@pytest.mark.asyncio
async def test_process_msg_generic_exception_handled():
    bot, _ = _make_bot(reload_raises=RuntimeError("unexpected"))
    cmd = ReloadConfigCommand(bot)
    results = await cmd.process_msg(".reload", _meta("master1"), None)
    assert len(results) == 1
    called_key = bot.loc_helper.format_loc_text.call_args[0][0]
    assert called_key == "reload_fail"


@pytest.mark.asyncio
async def test_process_msg_validation_error_persona_not_called():
    """On failure, persona reload must NOT be called (abort early)."""
    err = ConfigValidationError("bad config")
    bot, _ = _make_bot(reload_raises=err)
    old_cfg = MagicMock()
    old_cfg.persona = "default"
    bot.config = old_cfg

    cmd = ReloadConfigCommand(bot)
    await cmd.process_msg(".reload", _meta("master1"), None)
    bot._persona_loader.reload.assert_not_called()


# ── metadata ──────────────────────────────────────────────────────────────────


def test_get_description_not_empty():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    assert ".reload" in cmd.get_description()


def test_get_help_returns_empty_string():
    bot, _ = _make_bot()
    cmd = ReloadConfigCommand(bot)
    assert cmd.get_help("anything", _meta("u1")) == ""
