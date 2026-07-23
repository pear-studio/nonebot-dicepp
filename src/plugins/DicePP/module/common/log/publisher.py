from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from urllib.parse import urlparse

from plugins.DicePP.core.data.log_repository import LogRepository
from plugins.DicePP.core.data.models import LogPublication

from .projection import LogProjection, LogProjector
from .types import ExportRequest


@dataclass(frozen=True, slots=True)
class ProviderPublishResult:
    url: str
    note: str | None = None


class LogPublicationProvider(Protocol):
    name: str

    async def publish(
        self,
        projection: LogProjection,
        *,
        request_id: str,
        requested_by: str,
    ) -> ProviderPublishResult: ...


class PublicationStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    publication_id: int
    provider: str
    status: PublicationStatus
    url: str | None = None
    error: str | None = None
    audit_error: str | None = None


class LogPublisher:
    """Publish one immutable log snapshot through an injected Web provider."""

    def __init__(
        self,
        repository: LogRepository,
        provider: LogPublicationProvider,
        *,
        projector: LogProjector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        provider_name = provider.name.strip()
        if not provider_name:
            raise ValueError("A Web publication provider must have a name")
        self._repository = repository
        self._provider = provider
        self._provider_name = provider_name
        self._projector = projector or LogProjector()
        self._clock = clock or datetime.now

    async def publish(self, request: ExportRequest) -> PublicationResult:
        session = await self._repository.get_session(request.log_id)
        if session is None:
            raise ValueError(f"Unknown log session: {request.log_id}")
        if session.group_id != request.group_id or session.name != request.log_name:
            raise ValueError("Publication request does not match its log session")

        pending = LogPublication(
            request_id=request.request_id,
            log_id=request.log_id,
            provider=self._provider_name,
            view=request.view.value,
            record_upper_id=request.record_upper_id,
            created_at=self._clock(),
            status=PublicationStatus.PENDING.value,
        )
        publication_id = await self._repository.add_publication(pending)
        pending = pending.model_copy(update={"id": publication_id})

        try:
            records = await self._repository.get_records(
                request.log_id,
                upper_id=request.record_upper_id,
            )
            projection = self._projector.project(
                session,
                records,
                view=request.view,
                record_upper_id=request.record_upper_id,
            )
            provider_result = await self._provider.publish(
                projection,
                request_id=request.request_id,
                requested_by=request.requested_by,
            )
            url = provider_result.url.strip()
            if not _is_http_url(url):
                raise ValueError("Web provider returned an invalid publication URL")
        except Exception as exc:
            error = _error_note(exc)
            failed = pending.model_copy(
                update={
                    "status": PublicationStatus.FAILED.value,
                    "note": error,
                }
            )
            audit_error = await self._try_update_publication(failed)
            return PublicationResult(
                publication_id=publication_id,
                provider=self._provider_name,
                status=PublicationStatus.FAILED,
                error=error,
                audit_error=audit_error,
            )

        succeeded = pending.model_copy(
            update={
                "published_at": self._clock(),
                "url": url,
                "status": PublicationStatus.SUCCESS.value,
                "note": provider_result.note,
            }
        )
        audit_error = await self._try_update_publication(succeeded)
        return PublicationResult(
            publication_id=publication_id,
            provider=self._provider_name,
            status=PublicationStatus.SUCCESS,
            url=url,
            audit_error=audit_error,
        )

    async def latest_link(
        self,
        log_id: str,
        *,
        provider: str | None = None,
    ) -> LogPublication | None:
        return await self._repository.get_latest_successful_publication(
            log_id,
            provider=self._provider_name if provider is None else provider,
        )

    async def _try_update_publication(
        self,
        publication: LogPublication,
    ) -> str | None:
        try:
            await self._repository.update_publication(publication)
        except Exception as exc:
            return _error_note(exc)
        return None


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _error_note(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}"[:500]
