from tests.dashboard import playwright_support


def test_find_free_port_retries_chromium_restricted_port(monkeypatch) -> None:
    ports = iter((5061, 49152))
    monkeypatch.setattr(
        playwright_support,
        "_find_os_free_port",
        lambda: next(ports),
    )

    assert playwright_support.find_free_port() == 49152
