"""CLI contract tests for DicePP Shell command registration."""

from __future__ import annotations

import sys

import pytest

from plugins.DicePP.shell.main import main


def test_main_help_lists_runtime_warp_commands(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dicepp-shell", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "warp" in output
    assert "job" in output


def test_warp_help_exposes_background_job_options(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dicepp-shell", "warp", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--days" in output
    assert "--dry-run" in output
    assert "--detach" in output
