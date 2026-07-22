from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from core.data import LogRepository
from core.data.models import LogPublication, LogRecord
from core.data.schema import ensure_bot_log_schema
from module.common.log.publisher import (
    LogPublisher,
    ProviderPublishResult,
    PublicationStatus,
)
from module.common.log.service import LogService
from module.common.log.types import LogExportView


NOW = datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc)


class _FakeProvider:
    name = "fake_web"

    def __init__(self, *, result=None, error=None, repository=None) -> None:
        self.result = result or ProviderPublishResult("https://example.test/log/1")
        self.error = error
        self.repository = repository
        self.calls = []

    async def publish(self, projection, *, request_id: str, requested_by: str):
        if self.repository is not None:
            publications = await self.repository.list_publications(projection.log_id)
            assert len(publications) == 1
            assert publications[0].status == PublicationStatus.PENDING.value
            assert publications[0].url is None
        self.calls.append((projection, request_id, requested_by))
        if self.error is not None:
            raise self.error
        return self.result


@pytest_asyncio.fixture
async def publisher_parts(tmp_path: Path):
    db_path = tmp_path / "log.db"
    ensure_bot_log_schema(db_path)
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    ids = count(1)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: f"log-{next(ids)}",
        request_id_factory=lambda: f"request-{next(ids)}",
    )
    try:
        yield repository, service
    finally:
        await db.close()


async def _publication_request(repository, service, *, group_id="group-1"):
    started = await service.turn_on(group_id, "雾都夜话", requested_by="owner")
    await repository.add_record(
        LogRecord(
            log_id=started.session.id,
            time=NOW,
            user_id="user-1",
            nickname="调查员",
            source="user",
            message_type="ambient",
            plain_content="快照内",
            raw_content="快照内",
            message_id="message-1",
        )
    )
    stopped = await service.turn_off(group_id, requested_by="owner")
    assert stopped.export_request is not None
    return started.session, stopped.export_request


@pytest.mark.asyncio
async def test_pending_exists_before_provider_and_success_uses_fixed_snapshot(
    publisher_parts,
):
    repository, service = publisher_parts
    session, request = await _publication_request(repository, service)
    await repository.add_record(
        LogRecord(
            log_id=session.id,
            time=NOW + timedelta(minutes=1),
            user_id="user-2",
            nickname="后来者",
            source="user",
            message_type="ambient",
            plain_content="快照外",
            raw_content="快照外",
            message_id="message-2",
        )
    )
    provider = _FakeProvider(repository=repository)

    result = await LogPublisher(
        repository,
        provider,
        clock=lambda: NOW + timedelta(minutes=2),
    ).publish(request)

    assert result.status is PublicationStatus.SUCCESS
    assert result.url == "https://example.test/log/1"
    assert result.audit_error is None
    projection = provider.calls[0][0]
    assert [message.readable_text for message in projection.messages] == ["快照内"]
    assert projection.messages[0].message_id == "message-1"
    publication = (await repository.list_publications(session.id))[0]
    assert publication.id == result.publication_id
    assert publication.status == "success"
    assert publication.view == request.view.value
    assert publication.record_upper_id == request.record_upper_id
    assert publication.published_at == NOW + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_projection_view_and_provider_failure_are_audited(publisher_parts):
    repository, service = publisher_parts
    session, request = await _publication_request(
        repository,
        service,
        group_id="group-2",
    )
    provider = _FakeProvider(error=TimeoutError("provider timed out"))
    request = replace(request, view=LogExportView.COMPLETE)

    result = await LogPublisher(repository, provider, clock=lambda: NOW).publish(request)

    assert result.status is PublicationStatus.FAILED
    assert "provider timed out" in (result.error or "")
    publication = (await repository.list_publications(session.id))[0]
    assert publication.status == "failed"
    assert publication.view == "complete"
    assert publication.url is None
    assert publication.published_at is None


class _FailSuccessAuditRepository(LogRepository):
    async def update_publication(self, publication):
        if publication.status == PublicationStatus.SUCCESS.value:
            raise RuntimeError("publication audit unavailable")
        await super().update_publication(publication)


@pytest.mark.asyncio
async def test_remote_success_survives_success_audit_failure(tmp_path: Path):
    db_path = tmp_path / "log.db"
    ensure_bot_log_schema(db_path)
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = _FailSuccessAuditRepository(db)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: "log-audit",
        request_id_factory=lambda: "request-audit",
    )
    try:
        session, request = await _publication_request(repository, service)
        provider = _FakeProvider()

        result = await LogPublisher(repository, provider, clock=lambda: NOW).publish(request)

        assert result.status is PublicationStatus.SUCCESS
        assert result.url == "https://example.test/log/1"
        assert "publication audit unavailable" in (result.audit_error or "")
        assert len(provider.calls) == 1
        publication = (await repository.list_publications(session.id))[0]
        assert publication.status == "pending"
        assert publication.url is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_latest_link_is_read_only_and_defaults_to_current_provider(publisher_parts):
    repository, service = publisher_parts
    session, _ = await _publication_request(repository, service, group_id="group-3")
    first_id = await repository.add_publication(
        LogPublication(
            request_id="published-1",
            log_id=session.id,
            provider="fake_web",
            view="curated",
            created_at=NOW,
            published_at=NOW,
            url="https://example.test/first",
            status="success",
        )
    )
    await repository.add_publication(
        LogPublication(
            request_id="published-2",
            log_id=session.id,
            provider="other_web",
            view="curated",
            created_at=NOW + timedelta(minutes=1),
            published_at=NOW + timedelta(minutes=1),
            url="https://example.test/other",
            status="success",
        )
    )
    provider = _FakeProvider(error=AssertionError("latest_link must not publish"))
    publisher = LogPublisher(repository, provider)

    latest_default = await publisher.latest_link(session.id)
    latest_other = await publisher.latest_link(session.id, provider="other_web")

    assert latest_default is not None and latest_default.id == first_id
    assert latest_other is not None
    assert latest_other.url == "https://example.test/other"
    assert provider.calls == []
