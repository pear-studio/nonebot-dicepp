"""Tests for the ``/api/content/**`` content-management endpoints."""

import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.bot_process import BotProcessStatus
from tests.support.dashboard.app import setup_auth
from tests.support.fs_utils import symlink_or_skip


class TestListDecks:
    def test_list_decks(self, test_client: TestClient):
        """``GET /api/content/decks`` returns a file list."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/decks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        names = [f["name"] for f in data["files"]]
        assert "test_deck.txt" in names

    def test_list_random(self, test_client: TestClient):
        """``GET /api/content/random`` returns table files."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/random")
        assert resp.status_code == 200
        names = [f["name"] for f in resp.json()["files"]]
        assert "table.txt" in names

    def test_invalid_subdir(self, test_client: TestClient):
        """An invalid content subdirectory returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/invalid_dir")
        assert resp.status_code == 404

    def test_filters_hidden_files(self, test_client: TestClient):
        """Hidden files (``.gitkeep``, ``.DS_Store``, etc.) are excluded."""
        from dashboard.src.config import DashboardPaths

        # Create hidden files that the API must NOT return.
        (DashboardPaths.CONTENT_DIR / "decks" / ".gitkeep").touch()
        (DashboardPaths.CONTENT_DIR / "decks" / ".DS_Store").touch()

        setup_auth(test_client)
        resp = test_client.get("/api/content/decks")
        assert resp.status_code == 200
        names = [f["name"] for f in resp.json()["files"]]
        assert ".gitkeep" not in names
        assert ".DS_Store" not in names
        assert "test_deck.txt" in names

    @pytest.mark.parametrize("subdir", ["characters", "excel"])
    def test_retired_generic_content_subdirs_are_rejected(
        self, test_client: TestClient, subdir: str
    ):
        setup_auth(test_client)
        resp = test_client.get(f"/api/content/{subdir}")
        assert resp.status_code == 404


class TestReadTextFile:
    def test_read_text_file(self, test_client: TestClient):
        """``GET /api/content/decks/test_deck.txt`` returns file content."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/decks/test_deck.txt")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test_deck.txt"
        assert data["content"] == "deck content"

    def test_read_nonexistent_file(self, test_client: TestClient):
        """A file that doesn't exist returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/decks/nonexistent.txt")
        assert resp.status_code == 404


class TestPathTraversal:
    def test_path_traversal_blocked(self, test_client: TestClient):
        """Paths containing ``..`` are rejected."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/decks/..%2Fconfig%2Fuser.json"
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_absolute_path_blocked(self, test_client: TestClient):
        """Absolute paths are rejected."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/decks//etc/passwd"
        )
        assert resp.status_code == 400


class TestQueryDbEntries:
    @pytest.mark.asyncio
    async def test_normalize_serializes_bot_actions_with_one_maintenance_lock(
        self, test_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Bot lifecycle request cannot overlap an in-place normalization."""
        from dashboard.src import app as dashboard_app

        class Controller:
            def status(self) -> BotProcessStatus:
                return BotProcessStatus("stopped", returncode=0)

            def start(self) -> BotProcessStatus:
                started.set()
                return BotProcessStatus("running", pid=123)

            def shutdown(self) -> BotProcessStatus:
                return BotProcessStatus("stopped", returncode=0)

        entered = threading.Event()
        release = threading.Event()
        started = threading.Event()

        def blocking_write(*_args) -> None:
            entered.set()
            assert release.wait(2)

        monkeypatch.setattr(dashboard_app, "normalization_report", lambda _path: object())
        monkeypatch.setattr(dashboard_app, "report_detail", lambda _report: {})
        monkeypatch.setattr(dashboard_app, "write_normalized_database", blocking_write)
        test_client.app.state.bot_process_controller = Controller()
        request = SimpleNamespace(app=test_client.app, client=None)

        normalize_task = asyncio.create_task(
            dashboard_app.content_queries_normalize("test_queries", request)
        )
        await asyncio.to_thread(entered.wait, 2)
        start_task = asyncio.create_task(dashboard_app.bot_process_action("start", request))
        await asyncio.sleep(0)
        assert not started.is_set()
        release.set()
        await normalize_task
        await start_task
        assert started.is_set()

    def test_normalize_rejects_a_running_bot_without_writing(
        self, test_client: TestClient
    ) -> None:
        setup_auth(test_client)
        source = Path(test_client.app.state.dashboard_paths.CONTENT_DIR) / "queries" / "test_queries.db"
        before = source.read_bytes()

        class RunningController:
            def status(self) -> BotProcessStatus:
                return BotProcessStatus("running", pid=123)

            def shutdown(self) -> BotProcessStatus:
                return BotProcessStatus("stopped", returncode=0)

        test_client.app.state.bot_process_controller = RunningController()
        response = test_client.post("/api/content/queries/test_queries/normalize")

        assert response.status_code == 409
        assert "Bot must be stopped" in response.json()["message"]
        assert source.read_bytes() == before

    def test_normalize_writes_the_final_database_after_bot_is_stopped(
        self, test_client: TestClient
    ) -> None:
        setup_auth(test_client)

        class StoppedController:
            def status(self) -> BotProcessStatus:
                return BotProcessStatus("stopped", returncode=0)

            def shutdown(self) -> BotProcessStatus:
                return BotProcessStatus("stopped", returncode=0)

        test_client.app.state.bot_process_controller = StoppedController()
        response = test_client.post("/api/content/queries/test_queries/normalize")

        assert response.status_code == 200
        assert response.json()["normalized"] is True
        with sqlite3.connect(
            Path(test_client.app.state.dashboard_paths.CONTENT_DIR)
            / "queries"
            / "test_queries.db"
        ) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def test_query_db_entries(self, test_client: TestClient):
        """The data endpoint exposes only the four simple-format fields."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 7
        assert data["records"][0] == {
            "rowid": 1,
            "name": "火球术",
            "english": "Fireball",
            "source": "PHB",
            "content": "造成 8d6 火焰伤害。",
            "valid": True,
        }
        assert "分类" not in data["records"][0]
        assert "标签" not in data["records"][0]

    def test_query_db_pagination(self, test_client: TestClient):
        """Pagination remains ordered by the physical row order."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"offset": 1, "limit": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 7
        assert len(data["records"]) == 1
        assert data["records"][0]["rowid"] == 2

    def test_query_db_search_uses_selected_scope(self, test_client: TestClient):
        """Search is performed by the server against the requested logical field."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"q": "Shield", "scope": "english"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"][0]["name"] == "护盾术"

        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"q": "PHB", "scope": "source"},
        )
        assert resp.status_code == 200
        assert [row["rowid"] for row in resp.json()["records"]] == [1, 2, 3, 5]

        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"q": "%", "scope": "all"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_query_db_warning_row_filter_reaches_rows_outside_page(
        self, test_client: TestClient
    ):
        """A warning can request its concrete rows without current-page filtering."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"rowids": "1,4", "limit": 1, "offset": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert [row["rowid"] for row in data["records"]] == [4]

    def test_query_db_not_found(self, test_client: TestClient):
        """A non-existent query database returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/nonexistent/entries")
        assert resp.status_code == 404

    def test_query_db_entries_with_db_suffix_rejected(self, test_client: TestClient):
        """``db_name`` with ``.db`` suffix is rejected (API contract: no extension)."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries.db/entries")
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_query_db_path_traversal_is_rejected(self, test_client: TestClient):
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/..%2Fconfig%2Fglobal/entries"
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_invalid_search_scope_is_explained(self, test_client: TestClient):
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"scope": "category"},
        )
        assert resp.status_code == 422
        assert "搜索范围无效" in resp.json()["message"]

    def test_sqlite_integer_overflow_is_rejected_at_api_boundary(
        self, test_client: TestClient
    ):
        setup_auth(test_client)
        too_large = 2**63
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"offset": too_large},
        )
        assert resp.status_code == 422
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"rowids": str(too_large)},
        )
        assert resp.status_code == 400
        assert "1 到 9223372036854775807" in resp.json()["message"]


class TestQueryDbAudit:
    def test_audit_returns_stats_and_individual_actionable_warnings(
        self, test_client: TestClient
    ):
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"] == {
            "total_rows": 7,
            "valid_rows": 5,
            "redirect_rows": 4,
            "warning_count": 7,
        }

        resp = test_client.get(
            "/api/content/queries/test_queries/warnings",
            params={"offset": 0, "limit": 20},
        )
        assert resp.status_code == 200
        warning_page = resp.json()
        assert warning_page["total"] == 7
        warnings = warning_page["records"]
        assert len(warnings) == 7
        assert {warning["kind"] for warning in warnings} == {
            "invalid_data",
            "duplicate_content",
            "ambiguous_name",
            "invalid_redirect",
            "duplicate_redirect",
        }
        conflict = next(w for w in warnings if w["kind"] == "duplicate_content")
        assert conflict["rowids"] == [1, 2, 3]
        assert "机器人只会使用最前面的第 1 行" in conflict["message"]
        assert "后续内容会被隐藏" in conflict["message"]
        assert "请合并内容" in conflict["message"]

        ambiguous = next(w for w in warnings if w["kind"] == "ambiguous_name")
        assert ambiguous["rowids"] == [1, 2, 3, 4]
        assert "用户精确查询这个名称时会看到选择列表" in ambiguous["message"]

        duplicate_redirect = next(
            w for w in warnings if w["kind"] == "duplicate_redirect"
        )
        assert "当前机器人只会使用最前面的第 1 行" in duplicate_redirect["message"]
        assert "后续目标会被隐藏" in duplicate_redirect["message"]
        assert "规范数据库后也只会保留第 1 行" in duplicate_redirect["message"]

    def test_warnings_are_paginated_without_clustering(self, test_client: TestClient):
        setup_auth(test_client)
        first = test_client.get(
            "/api/content/queries/test_queries/warnings",
            params={"offset": 0, "limit": 2},
        ).json()
        second = test_client.get(
            "/api/content/queries/test_queries/warnings",
            params={"offset": 2, "limit": 2},
        ).json()
        assert first["total"] == second["total"] == 7
        assert len(first["records"]) == len(second["records"]) == 2
        assert {item["id"] for item in first["records"]}.isdisjoint(
            item["id"] for item in second["records"]
        )

    def test_identical_duplicate_rows_are_reported_as_hidden_data(
        self, test_client: TestClient
    ):
        from dashboard.src.config import DashboardPaths

        db_path = DashboardPaths.CONTENT_DIR / "queries" / "duplicate.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE data (名称 TEXT, 来源 TEXT, 内容 TEXT)")
            conn.executemany(
                "INSERT INTO data VALUES (?, ?, ?)",
                [("相同词条", "TEST", "相同内容"), ("相同词条", "TEST", "相同内容")],
            )

        setup_auth(test_client)
        response = test_client.get("/api/content/queries/duplicate/warnings")

        assert response.status_code == 200
        warning = response.json()["records"][0]
        assert warning["title"] == "“相同词条”有重复数据"
        assert warning["rowids"] == [1, 2]
        assert "后续内容会被隐藏" in warning["message"]
        assert "请删除重复行" in warning["message"]

    def test_missing_required_column_reports_effect_and_fix(
        self, test_client: TestClient
    ):
        from dashboard.src.config import DashboardPaths

        db_path = DashboardPaths.CONTENT_DIR / "queries" / "bad_schema.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE data (名称 TEXT, 英文 TEXT)")

        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/bad_schema/audit")
        assert resp.status_code == 422
        message = resp.json()["message"]
        assert "data 表缺少“内容”列" in message
        assert "请补齐列名后重新加载" in message

    def test_corrupt_database_returns_actionable_json_error(
        self, test_client: TestClient
    ):
        from dashboard.src.config import DashboardPaths

        (DashboardPaths.CONTENT_DIR / "queries" / "corrupt.db").write_bytes(
            b"not a sqlite database"
        )
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/corrupt/audit")
        assert resp.status_code == 500
        assert resp.json()["ok"] is False
        assert "请确认文件是有效且未损坏的 SQLite 查询库" in resp.json()["message"]

    def test_database_name_with_uri_metacharacter_is_opened_literally(
        self, test_client: TestClient
    ):
        from dashboard.src.config import DashboardPaths

        db_path = DashboardPaths.CONTENT_DIR / "queries" / "literal#name.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE data (名称 TEXT, 内容 TEXT)")
            conn.execute("INSERT INTO data VALUES ('测试', '内容')")

        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/literal%23name/audit")
        assert resp.status_code == 200
        assert resp.json()["stats"]["valid_rows"] == 1

    def test_database_symlink_cannot_escape_queries_directory(
        self, test_client: TestClient, tmp_dashboard_paths: Path
    ):
        from dashboard.src.config import DashboardPaths

        target = tmp_dashboard_paths / "outside_queries.db"
        with sqlite3.connect(target) as conn:
            conn.execute("CREATE TABLE data (名称 TEXT, 内容 TEXT)")
        link = DashboardPaths.CONTENT_DIR / "queries" / "escaped.db"
        symlink_or_skip(link, target)

        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/escaped/audit")
        assert resp.status_code == 400
        assert resp.json()["message"] == "Path traversal detected"


class TestQueryDbRedirects:
    def test_redirects_are_semantic_and_searchable(self, test_client: TestClient):
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/redirects",
            params={"q": "护盾"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"] == [
            {
                "rowid": 2,
                "name": "火球",
                "target": "护盾术",
                "valid": True,
            }
        ]

    def test_database_without_redirect_table_is_valid(self, test_client: TestClient):
        from dashboard.src.config import DashboardPaths

        db_path = DashboardPaths.CONTENT_DIR / "queries" / "no_redirect.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE data (名称 TEXT, 内容 TEXT)")
            conn.execute("INSERT INTO data VALUES ('护盾术', '获得 AC 加值。')")

        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/no_redirect/audit")
        assert resp.status_code == 200
        assert resp.json()["stats"]["redirect_rows"] == 0
        resp = test_client.get("/api/content/queries/no_redirect/redirects")
        assert resp.status_code == 200
        assert resp.json()["records"] == []
        resp = test_client.get("/api/content/queries/no_redirect/entries")
        assert resp.status_code == 200
        assert resp.json()["records"][0] == {
            "rowid": 1,
            "name": "护盾术",
            "english": "",
            "source": "",
            "content": "获得 AC 加值。",
            "valid": True,
        }


class TestSymlinkTraversal:
    """A symlink inside the content directory pointing outside is blocked."""

    def test_symlink_traversal_blocked(self, test_client: TestClient, tmp_dashboard_paths: Path):
        """Symlink to an outside file is rejected by the path traversal check."""
        from dashboard.src.config import DashboardPaths

        target_file = tmp_dashboard_paths / "outside_file.txt"
        target_file.write_text("outside, should be blocked")

        link_path = DashboardPaths.CONTENT_DIR / "decks" / "evil_link.txt"
        symlink_or_skip(link_path, target_file)

        setup_auth(test_client)
        resp = test_client.get("/api/content/decks/evil_link.txt")
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
