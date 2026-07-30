from __future__ import annotations

from pathlib import Path

import pytest

from dicepp_manager.client import ManagerClient
from dicepp_manager.config import ManagerClientSettings
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_API_VERSION


def _client(tmp_path: Path) -> ManagerClient:
    return ManagerClient(
        ManagerClientSettings(
            base_url="http://127.0.0.1:4091",
            token_path=tmp_path / "token",
        )
    )


def _compatible_status() -> dict:
    return {
        "health": {
            "manager_api_version": MANAGER_API_VERSION,
            "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        },
        "control": {"available": True, "protocol": "dicepp-control-v1"},
    }


@pytest.mark.asyncio
async def test_archive_download_streams_bounded_chunks_from_real_client_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    (tmp_path / "token").write_text("secret", encoding="utf-8")

    async def request(method: str, path: str, **_kwargs):
        assert (method, path) == ("GET", "/v1/status")
        return _compatible_status()

    class Response:
        def __init__(self) -> None:
            self.parts = [b"a" * 17, b"b" * 13, b""]
            self.read_sizes: list[int] = []
            self.closed = False

        def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            return self.parts.pop(0)

        def close(self) -> None:
            self.closed = True

    response = Response()
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(
        "dicepp_manager.client.urllib.request.urlopen",
        lambda *_args, **_kwargs: response,
    )

    download = await client.open_archive_download("跨平台 save.zip")
    chunks = list(download)

    assert chunks == [b"a" * 17, b"b" * 13]
    assert response.read_sizes == [1024 * 1024] * 3
    assert response.closed is True
