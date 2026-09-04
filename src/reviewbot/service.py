from __future__ import annotations

import asyncio
from pathlib import Path

from reviewbot.deepseek_client import DeepSeekApiError
from reviewbot.diff import build_diff_context
from reviewbot.gitee_client import GiteeApiError
from reviewbot.models import ReviewJob
from reviewbot.ports import GiteePort, ReviewPort
from reviewbot.renderer import render_review, review_marker
from reviewbot.reviewer import ReviewFormatError
from reviewbot.storage import QueueStore

_DEFAULT_RULES = """# Review rules

- Review only the current Pull Request diff and the minimum surrounding code needed to prove a finding.
- Require a concrete failure mode, affected path, line, impact, and actionable modification suggestion.
- Flag security issues such as injection, credential leakage, unsafe dynamic evaluation, and missing authorization.
- Flag missing error handling, regressions, data loss, concurrency hazards, and observable contract breaks.
- Do not report formatting preferences or unrelated refactors as blocking findings.
"""


class ReviewService:
    """Application service with no dependency on FastAPI or process globals."""

    def __init__(
        self,
        *,
        store: QueueStore,
        gitee: GiteePort,
        engine: ReviewPort,
        rule_file: Path,
        max_diff_bytes: int,
        max_review_bytes: int,
        allowlist: frozenset[str],
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._gitee = gitee
        self._engine = engine
        self._rule_file = rule_file
        self._max_diff_bytes = max_diff_bytes
        self._max_review_bytes = max_review_bytes
        self._allowlist = allowlist
        self._enabled = enabled

    async def process(self, job: ReviewJob) -> None:
        if not self._enabled:
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "review disabled")
            return
        if job.repository.lower() not in self._allowlist:
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "repository not allowlisted")
            return
        if not self._is_review_action(job.action):
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "event action ignored")
            return

        pull_request = await self._gitee.get_pull_request(job.repository, job.pull_request_number)
        if pull_request.state.lower() != "open":
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "Pull Request is not open")
            return
        if pull_request.draft:
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "draft Pull Request")
            return
        if await asyncio.to_thread(
            self._store.has_review,
            pull_request.repository,
            pull_request.number,
            pull_request.head_sha,
        ):
            await asyncio.to_thread(self._store.mark_skipped, job.delivery_id, "head SHA already reviewed")
            return
        marker = review_marker(pull_request.head_sha)
        existing_comments = await self._gitee.list_pull_request_comments(
            pull_request.repository,
            pull_request.number,
        )
        existing = next((comment for comment in existing_comments if marker in comment.body), None)
        if existing is not None:
            await asyncio.to_thread(
                self._store.record_review,
                pull_request.repository,
                pull_request.number,
                pull_request.head_sha,
                existing.id,
            )
            await asyncio.to_thread(self._store.mark_succeeded, job.delivery_id)
            return

        changed_files = await self._gitee.list_pull_request_files(pull_request.repository, pull_request.number)
        diff = build_diff_context(changed_files, self._max_diff_bytes)
        rules = load_rules(self._rule_file)
        result = await self._engine.review(pull_request, diff, rules)
        comment = render_review(pull_request, result, max_bytes=self._max_review_bytes)
        comment_id = await self._gitee.create_pull_request_comment(
            pull_request.repository,
            pull_request.number,
            comment,
        )
        await asyncio.to_thread(
            self._store.record_review,
            pull_request.repository,
            pull_request.number,
            pull_request.head_sha,
            comment_id,
        )
        await asyncio.to_thread(self._store.mark_succeeded, job.delivery_id)

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        if isinstance(error, ReviewFormatError):
            return False
        if isinstance(error, (GiteeApiError, DeepSeekApiError)):
            return error.status_code == 0 or error.status_code == 429 or error.status_code >= 500
        return False

    @staticmethod
    def _is_review_action(action: str) -> bool:
        return action in {"opened", "reopened", "ready_for_review", "synchronize"}


def load_rules(path: Path) -> str:
    try:
        rules = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _DEFAULT_RULES
    except OSError:
        return _DEFAULT_RULES
    return rules.strip() or _DEFAULT_RULES
