from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from reviewbot.models import ChangedFile, PullRequest, PullRequestComment

_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


class GiteeApiError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int, message: str = "Gitee API request failed") -> None:
        super().__init__(f"{method} {path} returned HTTP {status_code}: {message}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.message = message


class GiteeClient:
    """Minimal Gitee adapter used by the review application."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        auth_mode: str = "query",
        timeout_seconds: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._auth_mode = auth_mode
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Accept": "application/json",
                "User-Agent": "bh-gitee-review-bot/0.1",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_pull_request(self, repository: str, number: int) -> PullRequest:
        path = self._pull_path(repository, number)
        payload = await self._request("GET", path)
        return self._parse_pull_request(repository, number, payload)

    async def list_pull_request_files(self, repository: str, number: int) -> list[ChangedFile]:
        path = f"{self._pull_path(repository, number)}/files"
        payload = await self._request("GET", path)
        if not isinstance(payload, list):
            raise GiteeApiError("GET", path, 200, "unexpected files response")
        return [self._parse_changed_file(item) for item in payload if isinstance(item, Mapping)]

    async def list_pull_request_comments(self, repository: str, number: int) -> list[PullRequestComment]:
        path = f"{self._pull_path(repository, number)}/comments"
        payload = await self._request("GET", path)
        if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
            payload = payload["data"]
        if not isinstance(payload, list):
            raise GiteeApiError("GET", path, 200, "unexpected comments response")
        return [self._parse_comment(item) for item in payload if isinstance(item, Mapping)]

    async def create_pull_request_comment(self, repository: str, number: int, body: str) -> int | None:
        path = f"{self._pull_path(repository, number)}/comments"
        payload = await self._request("POST", path, data={"body": body})
        if isinstance(payload, Mapping) and isinstance(payload.get("id"), int):
            return payload["id"]
        return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        params: dict[str, str] = {}
        headers: dict[str, str] = {}
        if self._auth_mode == "query":
            params["access_token"] = self._token
        elif self._auth_mode == "header":
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            raise ValueError(f"unsupported Gitee auth mode: {self._auth_mode}")

        try:
            response = await self._client.request(method, path.lstrip("/"), params=params, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise GiteeApiError(method, path, 0, "network error") from exc

        if response.is_error:
            raise GiteeApiError(method, path, response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise GiteeApiError(method, path, response.status_code, "invalid JSON response") from exc

    @staticmethod
    def _pull_path(repository: str, number: int) -> str:
        owner, name = GiteeClient._split_repository(repository)
        if number <= 0:
            raise ValueError("Pull Request number must be positive")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/pulls/{number}"

    @staticmethod
    def _split_repository(repository: str) -> tuple[str, str]:
        parts = repository.strip().split("/", 1)
        if len(parts) != 2 or not all(_REPOSITORY_PART.fullmatch(part) for part in parts):
            raise ValueError(f"invalid Gitee repository: {repository!r}")
        return parts[0], parts[1]

    @staticmethod
    def _parse_pull_request(repository: str, number: int, payload: Any) -> PullRequest:
        if not isinstance(payload, Mapping):
            raise GiteeApiError("GET", "pull request", 200, "unexpected pull request response")
        head = payload.get("head") if isinstance(payload.get("head"), Mapping) else {}
        base = payload.get("base") if isinstance(payload.get("base"), Mapping) else {}
        last_commit = payload.get("last_commit") if isinstance(payload.get("last_commit"), Mapping) else {}
        head_sha = GiteeClient._first_text(
            head.get("sha"),
            head.get("commit_id"),
            payload.get("head_sha"),
            payload.get("source_commit"),
            payload.get("source_commit_sha"),
            payload.get("source_sha"),
            last_commit.get("id"),
            payload.get("last_commit_id"),
        )
        base_sha = GiteeClient._first_text(
            base.get("sha"),
            base.get("commit_id"),
            payload.get("base_sha"),
            payload.get("target_commit"),
            payload.get("target_commit_sha"),
            payload.get("target_sha"),
        )
        user = payload.get("user") if isinstance(payload.get("user"), Mapping) else {}
        return PullRequest(
            repository=repository,
            number=number,
            title=str(payload.get("title") or "Untitled Pull Request"),
            body=str(payload.get("body") or ""),
            state=str(payload.get("state") or "open"),
            draft=bool(payload.get("draft")),
            author=str(user.get("login") or user.get("name") or "unknown"),
            head_sha=head_sha,
            head_ref=GiteeClient._first_text(head.get("ref"), payload.get("source_branch"), payload.get("head_branch")),
            base_sha=base_sha,
            base_ref=GiteeClient._first_text(
                base.get("ref"), payload.get("target_branch"), payload.get("base_branch"), "master"
            ),
            html_url=GiteeClient._first_text(payload.get("html_url"), payload.get("url")),
        )

    @staticmethod
    def _parse_changed_file(payload: Mapping[str, Any]) -> ChangedFile:
        filename = GiteeClient._first_text(
            payload.get("filename"),
            payload.get("new_path"),
            payload.get("path"),
            payload.get("old_path"),
        )
        if not filename:
            raise GiteeApiError("GET", "pull request files", 200, "file entry has no path")
        patch_payload = payload.get("patch")
        if isinstance(patch_payload, Mapping):
            patch_text = GiteeClient._first_text(patch_payload.get("diff"), patch_payload.get("patch"))
        else:
            patch_text = GiteeClient._first_text(patch_payload)
        return ChangedFile(
            filename=filename,
            status=str(payload.get("status") or "modified"),
            patch=GiteeClient._first_text(patch_text, payload.get("diff")),
            additions=GiteeClient._integer(payload.get("additions")),
            deletions=GiteeClient._integer(payload.get("deletions")),
        )

    @staticmethod
    def _parse_comment(payload: Mapping[str, Any]) -> PullRequestComment:
        comment_id = payload.get("id")
        if not isinstance(comment_id, int):
            raise GiteeApiError("GET", "pull request comments", 200, "comment entry has no id")
        return PullRequestComment(id=comment_id, body=GiteeClient._first_text(payload.get("body")))

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _integer(value: Any) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str):
            try:
                parsed = int(value.strip())
            except ValueError:
                return 0
            return parsed if parsed >= 0 else 0
        return 0
