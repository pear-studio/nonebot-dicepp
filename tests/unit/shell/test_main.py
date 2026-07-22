"""CLI contract tests for DicePP Shell command registration."""

from __future__ import annotations

import sys

import pytest

from shell.main import _print_dry_run, _print_warp_result, main


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
            "proactive_agent_runs_max": 3,
            "proactive_schedule_windows": 3,
            "proactive_labels": ["morning", "midday_18:00", "evening"],
            "background_max_rounds": 10,
            "sa_max_rounds": 100,
        },
    })

    output = capsys.readouterr().out
    assert "Agent Run upper bounds" in output
    assert "Proactive schedule windows in timeline: 3" in output
    assert "background=10, SA=100" in output


def test_warp_result_calls_proactive_state_a_schedule_marker(capsys):
    _print_warp_result({
        "start_at": "2026-07-16T12:30:00",
        "end_at": "2026-07-17T12:30:00",
        "minutes_advanced": 1440,
        "life_slots_marked": 2,
        "daily_runs": 1,
        "proactive_schedule_count": 3,
        "proactive_schedule_labels": ["morning", "midday_18:00", "evening"],
        "tick_errors": 0,
        "daily_errors": 0,
    })

    output = capsys.readouterr().out
    assert "Proactive schedule points marked: 3" in output
    assert "sent" not in output.lower()
