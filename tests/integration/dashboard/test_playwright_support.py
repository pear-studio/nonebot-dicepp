from tests.support.dashboard import playwright


def test_find_free_port_retries_chromium_restricted_port(monkeypatch) -> None:
    ports = iter((5061, 49152))
    monkeypatch.setattr(
        playwright,
        "_find_os_free_port",
        lambda: next(ports),
    )

    assert playwright.find_free_port() == 49152
