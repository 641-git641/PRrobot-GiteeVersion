from __future__ import annotations

import asyncio
import logging

from reviewbot.config import Settings
from reviewbot.security import redact_sensitive_text
from reviewbot.service import ReviewService
from reviewbot.storage import QueueStore

log = logging.getLogger(__name__)


class ReviewWorker:
    def __init__(self, *, store: QueueStore, service: ReviewService, settings: Settings) -> None:
        self._store = store
        self._service = service
        self._settings = settings

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            claimed = await asyncio.to_thread(self._store.claim_next)
            if claimed is None:
                await _wait_for_work(stop_event)
                continue
            job, attempts = claimed
            try:
                await self._service.process(job)
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    self._store.mark_failed,
                    job.delivery_id,
                    "worker cancelled",
                    retry=True,
                )
                raise
            except Exception as exc:
                retry = attempts <= self._settings.max_retries and self._service.is_retryable(exc)
                error = _safe_error(exc)
                await asyncio.to_thread(self._store.mark_failed, job.delivery_id, error, retry=retry)
                log.warning(
                    "review job failed",
                    extra={
                        "delivery_id": job.delivery_id,
                        "repository": job.repository,
                        "pull_request": job.pull_request_number,
                        "attempts": attempts,
                        "retry": retry,
                        "error": error,
                    },
                )
                if retry:
                    await _wait_for_retry(stop_event, attempts)


async def _wait_for_work(stop_event: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=1.0)
    except TimeoutError:
        return


async def _wait_for_retry(stop_event: asyncio.Event, attempts: int) -> None:
    delay = min(2.0**attempts, 30.0)
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return


def _safe_error(error: Exception) -> str:
    message = redact_sensitive_text(str(error).replace("\r", " ").replace("\n", " ").strip())
    return message[:500] or error.__class__.__name__
