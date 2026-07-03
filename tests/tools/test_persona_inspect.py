import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from plugins.DicePP.core.data.schema import apply_schema_target
from plugins.DicePP.core.data.schema.lifecycle import execute_many
from plugins.DicePP.module.persona.data.schema import (
    BOT_CORE_SCHEMA_SQL,
    PERSONA_TARGET,
)


def test_persona_inspect_user_reads_latest_schema(tmp_path: Path):
    bot_dir = tmp_path / "bot"
    bot_dir.mkdir()
    core_db_path = bot_dir / "bot_data.db"
    persona_db_path = bot_dir / "personas_data_default.db"

    with sqlite3.connect(core_db_path) as core_conn:
        execute_many(core_conn, BOT_CORE_SCHEMA_SQL)

    apply_schema_target(persona_db_path, PERSONA_TARGET)
    with sqlite3.connect(persona_db_path) as persona_conn:
        persona_conn.execute(
            """
            INSERT INTO persona_user_relationships
              (user_id, familiarity, peak_familiarity, intimacy, peak_intimacy,
               reputation, last_interaction_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("u1", 42.0, 45.0, 30.0, 35.0, 88.0, "2026-07-01 08:30:00"),
        )
        persona_conn.execute(
            """
            INSERT INTO message_stream
              (user_id, group_id, role, type, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("u1", "", "user", "chat", "hello from latest schema", "2026-07-01 08:31:00"),
        )
        persona_conn.execute(
            """
            INSERT INTO persona_score_history
              (user_id, group_id, intimacy_delta, reputation_delta,
               familiarity_delta, composite_before, composite_after, reason,
               created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("u1", "", 1.5, -2.0, 3.0, 38.0, 41.6, "latest score", "2026-07-01 08:32:00"),
        )

    script_path = (
        Path(__file__).parents[2]
        / "docs"
        / "agent"
        / "skills-common"
        / "persona-inspect"
        / "scripts"
        / "persona_inspect.py"
    )
    env = {**os.environ, "PYTHONUTF8": "1"}

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "user",
            "u1",
            "--db",
            str(core_db_path),
            "--character",
            "default",
            "--limit",
            "5",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "familiarity" in result.stdout
    assert "reputation" in result.stdout
    assert "hello from latest schema" in result.stdout
    assert "latest score" in result.stdout
