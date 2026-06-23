"""Tests for the ``/api/content/**`` content-management endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.dashboard.conftest import setup_auth


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

    def test_empty_subdir(self, test_client: TestClient):
        """An empty but valid subdirectory returns an empty list."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/characters")
        assert resp.status_code == 200
        assert resp.json()["files"] == []


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
            "/api/content/decks/..%2Fconfig%2Fglobal.json"
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
    def test_query_db_entries(self, test_client: TestClient):
        """``GET /api/content/queries/{db_name}/entries`` returns paginated rows."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["records"]) == 2
        assert data["records"][0]["text"] == "entry1"

    def test_query_db_pagination(self, test_client: TestClient):
        """Offset and limit work for query entries."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/content/queries/test_queries/entries",
            params={"offset": 1, "limit": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["records"]) == 1
        assert data["records"][0]["text"] == "entry2"

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

    def test_entries_includes_columns(self, test_client: TestClient):
        """Response includes ``columns`` derived from the table schema."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert "columns" in data
        assert "rowid" in data["columns"]
        assert "id" in data["columns"]
        assert "text" in data["columns"]


class TestQueryDbTables:
    """Tests for ``GET /api/content/queries/{db_name}/tables``."""

    def test_list_tables(self, test_client: TestClient):
        """Returns a list of table names in the query database."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "tables" in data
        assert "data" in data["tables"]

    def test_db_not_found(self, test_client: TestClient):
        """Non-existent database returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/nonexistent/tables")
        assert resp.status_code == 404

    def test_db_suffix_rejected(self, test_client: TestClient):
        """``db_name`` with ``.db`` suffix is rejected."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/test_queries.db/tables")
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_path_traversal_blocked(self, test_client: TestClient):
        """Path traversal in db_name returns 400."""
        setup_auth(test_client)
        resp = test_client.get("/api/content/queries/..%2Fconfig%2Fglobal/tables")
        assert resp.status_code == 400
        assert resp.json()["ok"] is False


class TestSymlinkTraversal:
    """A symlink inside the content directory pointing outside is blocked."""

    def test_symlink_traversal_blocked(self, test_client: TestClient, tmp_dashboard_paths: Path):
        """Symlink to an outside file is rejected by the path traversal check."""
        from dashboard.src.config import DashboardPaths

        target_file = tmp_dashboard_paths / "outside_file.txt"
        target_file.write_text("outside, should be blocked")

        link_path = DashboardPaths.CONTENT_DIR / "decks" / "evil_link.txt"
        link_path.symlink_to(target_file)

        setup_auth(test_client)
        resp = test_client.get("/api/content/decks/evil_link.txt")
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
