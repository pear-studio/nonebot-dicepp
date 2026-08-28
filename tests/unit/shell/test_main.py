"""CLI contract tests for DicePP Shell command registration."""

from __future__ import annotations

import runpy
import sys

import pytest

from plugins.DicePP.shell.main import _print_dry_run, _print_warp_result, main


def test_canonical_module_entrypoint_shows_shell_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["plugins.DicePP.shell", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("plugins.DicePP.shell", run_name="__main__")

    assert exc_info.value.code == 0
    assert "usage: dicepp-shell" in capsys.readouterr().out


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
    assert "N x 24 hours" in output
    assert "default: runtime ready" in output
    assert "Agent Run upper bounds" in output


def test_dry_run_output_describes_agent_run_upper_bounds(capsys):
    _print_dry_run({
        "start_at": "2026-07-16T12:30:00",
        "end_at": "2026-07-17T12:30:00",
        "minutes": 1440,
        "model": "deepseek-chat",
        "estimate": {
            "calendar_days_touched": 2,
            "dm_agent_runs_max": 8,
            "character_reaction_runs_max": 8,
            "diary_agent_runs_max": 1,
            "sa_agent_runs_max": 1,
            "background_max_rounds": 10,
            "sa_max_rounds": 100,
        },
    })

    output = capsys.readouterr().out
    assert "Agent Run upper bounds" in output
    assert "background=10, SA=100" in output
