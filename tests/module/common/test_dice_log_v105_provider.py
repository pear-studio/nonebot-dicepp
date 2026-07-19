from __future__ import annotations

import asyncio
import json
import zlib
from datetime import datetime, timezone

import pytest

import module.common.log.providers.dice_log_v105 as provider_module
from module.common.log.projection import (
    LogProjection,
    ProjectedMessage,
    ProjectedPart,
)
from module.common.log.providers.dice_log_v105 import (
    DiceLogV105Provider,
    ProviderPublishError,
    ProviderUnavailableError,
)
from module.common.log.types import LogExportView

pytestmark = [pytest.mark.unit, pytest.mark.log]


class _CapturingFormData:
    latest = None

    def __init__(self):
        self.fields = []
        type(self).latest = self

    def add_field(self, name, value, **kwargs):
        self.fields.append((name, value, kwargs))


class _FakeResponse:
    def __init__(self, status=200, body='{"url":"https://logs.test/1"}', reason="OK", error=None):
        self.status = status
        self.body = body
        self.reason = reason
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self

    async def __aexit__(self, *_):
        return False

    async def text(self):
        return self.body


class _FakeSession:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def put(self, url, **kwargs):
        self.owner.request = (url, kwargs)
        return self.owner.response


class _SessionFactory:
    def __init__(self, response):
        self.response = response
        self.timeout = None
        self.request = None

    def __call__(self, *, timeout):
        self.timeout = timeout
        return _FakeSession(self)


def _projection():
    return LogProjection(
        log_id="log-1",
        group_id="group-1",
        log_name="雾都夜话",
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        view=LogExportView.CURATED,
        record_upper_id=1,
        messages=(
            ProjectedMessage(
                record_id=1,
                time=datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
                user_id="10001",
                nickname="调查员",
                source="user",
                message_type="ambient",
                reply=None,
                parts=(ProjectedPart("text", "你好，世界"),),
                message_id="message-1",
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["secret-token", ""])
async def test_v105_request_preserves_wire_contract(monkeypatch, token):
    monkeypatch.setattr(provider_module.aiohttp, "FormData", _CapturingFormData)
    factory = _SessionFactory(_FakeResponse())
    provider = DiceLogV105Provider(
        "https://provider.test/api/log",
        token=token,
        timeout_seconds=12.5,
        session_factory=factory,
    )

    result = await provider.publish(
        _projection(),
        request_id="request-1",
        requested_by="90001",
    )

    assert result.url == "https://logs.test/1"
    assert factory.timeout.total == 12.5
    url, kwargs = factory.request
    assert url == "https://provider.test/api/log"
    assert kwargs["headers"] == (
        {"Authorization": "Bearer secret-token"} if token else {}
    )
    fields = {name: (value, options) for name, value, options in _CapturingFormData.latest.fields}
    assert fields["name"][0] == "雾都夜话"
    assert fields["uniform_id"][0] == "QQ:90001"
    assert fields["client"][0] == "DicePP"
    assert fields["version"][0] == "105"
    compressed, options = fields["file"]
    assert options == {
        "filename": "log-zlib-compressed",
        "content_type": "application/octet-stream",
    }
    payload = json.loads(zlib.decompress(compressed).decode("utf-8"))
    assert payload == {
        "version": 105,
        "items": [
            {
                "nickname": "调查员",
                "imUserId": "10001",
                "uniformId": "QQ:10001",
                "time": 1784534400,
                "message": "你好，世界",
                "isDice": False,
                "commandId": None,
                "commandInfo": None,
                "rawMsgId": "message-1",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(status=500, body='{"message":"server down"}', reason="Error"),
        _FakeResponse(status=200, body="not json"),
        _FakeResponse(status=200, body='{"message":"missing url"}'),
    ],
)
async def test_v105_rejects_http_and_protocol_errors(response):
    provider = DiceLogV105Provider(
        "https://provider.test/api/log",
        session_factory=_SessionFactory(response),
    )

    with pytest.raises(ProviderPublishError):
        await provider.publish(_projection(), request_id="request-1", requested_by="1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ('{"message":"invalid token secret-token"}', "Bad Request"),
        ("{}", "invalid token secret-token"),
    ],
)
async def test_v105_redacts_token_from_http_errors(body, reason):
    response = _FakeResponse(status=401, body=body, reason=reason)
    provider = DiceLogV105Provider(
        "https://provider.test/api/log",
        token="secret-token",
        session_factory=_SessionFactory(response),
    )

    with pytest.raises(ProviderPublishError) as raised:
        await provider.publish(_projection(), request_id="request-1", requested_by="1")

    assert "secret-token" not in str(raised.value)
    assert "[redacted]" in str(raised.value)


@pytest.mark.asyncio
async def test_v105_timeout_and_empty_endpoint_are_explicit_failures():
    timed_out = DiceLogV105Provider(
        "https://provider.test/api/log",
        session_factory=_SessionFactory(_FakeResponse(error=asyncio.TimeoutError())),
    )
    with pytest.raises(ProviderPublishError, match="timed out"):
        await timed_out.publish(_projection(), request_id="request-1", requested_by="1")

    unavailable = DiceLogV105Provider("")
    with pytest.raises(ProviderUnavailableError, match="not configured"):
        await unavailable.publish(_projection(), request_id="request-2", requested_by="1")
