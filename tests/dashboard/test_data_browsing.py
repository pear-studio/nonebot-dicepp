"""Tests for the ``/api/data/**`` data-browsing endpoints."""

from fastapi.testclient import TestClient

from tests.dashboard.conftest import setup_auth


class TestListTables:
    def test_list_tables(self, test_client: TestClient):
        """``GET /api/data/{bot_id}/tables`` returns table names with row counts."""
        setup_auth(test_client)
        resp = test_client.get("/api/data/test_bot/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        tables = data["tables"]
        # Should find the characters table
        chars = [t for t in tables if t["name"] == "characters"]
        assert len(chars) == 1
        assert chars[0]["count"] == 3

    def test_list_tables_another_bot(self, test_client: TestClient):
        """A different bot reports its own tables."""
        setup_auth(test_client)
        resp = test_client.get("/api/data/another_bot/tables")
        assert resp.status_code == 200
        data = resp.json()
        tables = data["tables"]
        items = [t for t in tables if t["name"] == "items"]
        assert len(items) == 1
        assert items[0]["count"] == 2

    def test_invalid_bot(self, test_client: TestClient):
        """An unknown bot returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/data/nonexistent_bot/tables")
        assert resp.status_code == 404


class TestPaginatedRecords:
    def test_paginated_records(self, test_client: TestClient):
        """Offset and limit parameters control pagination."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"offset": 0, "limit": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["records"]) == 2
        assert data["offset"] == 0
        assert data["limit"] == 2

    def test_offset_second_page(self, test_client: TestClient):
        """Offset 2 returns the third record."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"offset": 2, "limit": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["records"]) == 1
        assert data["records"][0]["name"] == "Legolas"


class TestSearch:
    def test_search_filters_records(self, test_client: TestClient):
        """A keyword search returns only matching records."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"q": "Gandalf"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"][0]["name"] == "Gandalf"
        assert data["records"][0]["level"] == 20

    def test_search_no_match(self, test_client: TestClient):
        """A search with no matches returns zero records."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"q": "Frodo"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["records"] == []


class TestInvalidTable:
    def test_invalid_table_returns_404(self, test_client: TestClient):
        """Querying a non-existent table returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/data/test_bot/table/nonexistent")
        assert resp.status_code == 404

    def test_invalid_table_search(self, test_client: TestClient):
        """Searching a non-existent table returns 404."""
        setup_auth(test_client)
        resp = test_client.get(
            "/api/data/test_bot/table/nonexistent",
            params={"q": "test"},
        )
        assert resp.status_code == 404
