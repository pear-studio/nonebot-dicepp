"""Unit tests for shell/client.py error normalisation (mock urllib)."""

from __future__ import annotations

import json
from unittest import mock
from urllib import error as urllib_error

import pytest

from shell.client import (
    cancel_job,
    fetch_job,
    ShellRuntimeRequestError,
    fetch_status,
    request_stop,
    send_message,
    start_warp,
)
from shell.session import RuntimeInfo


@pytest.fixture
def runtime():
    return RuntimeInfo(
        pid=1, process_created_at=1.0, host="127.0.0.1",
        port=9999, bot_id="test", started_at=1.0,
    )


class TestSendMessage:
    def test_success(self, runtime):
        payload = {"text": "hello", "user_id": "u1"}
        resp_data = json.dumps({"text": "world"}).encode()
        with mock.patch("urllib.request.urlopen") as m_open:
            m_open.return_value.__enter__.return_value.read.return_value = resp_data
            m_open.return_value.__enter__.return_value.status = 200
            result = send_message(runtime, payload)
        assert result == {"text": "world"}


class TestWarpJobs:
    @pytest.mark.parametrize(
        ("call", "expected_path", "expected_method"),
        [
            (lambda runtime: start_warp(runtime, {"days": 2}), "/v1/warps", "POST"),
            (lambda runtime: fetch_job(runtime, "warp_123"), "/v1/jobs/warp_123", "GET"),
            (
                lambda runtime: cancel_job(runtime, "warp_123"),
                "/v1/jobs/warp_123/cancel",
                "POST",
            ),
        ],
    )
    def test_job_requests_use_short_control_calls(
        self, runtime, call, expected_path, expected_method
    ):
        response = json.dumps({"id": "warp_123", "status": "running"}).encode()
        with mock.patch("urllib.request.urlopen") as m_open:
            m_open.return_value.__enter__.return_value.read.return_value = response
            result = call(runtime)

        request = m_open.call_args.args[0]
        assert request.full_url.endswith(expected_path)
        assert request.method == expected_method
        assert result["id"] == "warp_123"


class TestHTTPErrors:
    def test_http_error_normalised(self, runtime, monkeypatch):
        """HTTPError -> ShellRuntimeRequestError with status code in message."""
        def _raise(*a, **kw):
            raise urllib_error.HTTPError(
                "http://127.0.0.1:9999/v1/status", 503, "Service Unavailable",
                {}, mock.Mock(read=lambda: b'{"detail":"down"}'),
            )
        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(ShellRuntimeRequestError, match="503"):
            fetch_status(runtime)

    def test_network_error_normalised(self, runtime, monkeypatch):
        """OSError / URLError -> ShellRuntimeRequestError."""
        def _raise(*a, **kw):
            raise OSError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(ShellRuntimeRequestError, match="unavailable"):
            request_stop(runtime)

    def test_invalid_json_normalised(self, runtime, monkeypatch):
        """Non-JSON response -> ShellRuntimeRequestError."""
        def _raise(*a, **kw):
            raise urllib_error.HTTPError(
                "http://...", 500, "Error", {},
                mock.Mock(read=lambda: b"<html>crash</html>"),
            )
        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(ShellRuntimeRequestError):
            fetch_status(runtime)

    def test_non_object_normalised(self, runtime):
        """A JSON array/list response raises ShellRuntimeRequestError."""
        resp_data = json.dumps([1, 2, 3]).encode()
        with mock.patch("urllib.request.urlopen") as m_open:
            m_open.return_value.__enter__.return_value.read.return_value = resp_data
            m_open.return_value.__enter__.return_value.status = 200
            with pytest.raises(ShellRuntimeRequestError, match="non-object"):
                send_message(runtime, {"text": "x", "user_id": "u1"})
