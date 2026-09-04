from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from reviewbot.models import ReviewJob

REVIEW_ACTIONS = frozenset(
    {
        "open",
        "opened",
        "create",
        "created",
        "reopen",
        "reopened",
        "ready",
        "ready_for_review",
        "update",
        "updated",
        "synchronize",
    }
)


class WebhookError(ValueError):
    pass


def verify_webhook(body: bytes, *, secret: str, token_header: str | None, signature_header: str | None) -> bool:
    """Verify Gitee's shared token or an HMAC signature without timing leaks."""
    if not secret:
        return False
    if token_header:
        if hmac.compare_digest(token_header, secret):
            return True
    if not signature_header:
        return False
    if signature_header.startswith("sha256="):
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
    return hmac.compare_digest(signature_header, secret)


def parse_review_job(
    *,
    body: bytes,
    event_type: str,
    delivery_id: str | None,
) -> ReviewJob:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookError("invalid webhook JSON") from exc
    if not isinstance(payload, Mapping):
        raise WebhookError("webhook payload must be an object")

    repository = _repository_name(payload)
    pr = _pull_request_object(payload)
    number = _first_int(
        pr.get("number"),
        pr.get("iid"),
        pr.get("id"),
        payload.get("number"),
        payload.get("pull_request_id"),
        _object_attributes(payload).get("number"),
        _object_attributes(payload).get("iid"),
    )
    if not repository or number is None or number <= 0:
        raise WebhookError("webhook payload has no repository and Pull Request number")

    action = _normalize_action(payload, event_type)
    return ReviewJob(
        delivery_id=delivery_id or hashlib.sha256(body).hexdigest(),
        event_type=event_type or "pull_request",
        action=action,
        repository=repository,
        pull_request_number=number,
        webhook_head_sha=_head_sha(pr, payload),
    )


def is_pull_request_event(event_type: str, payload: Mapping[str, Any]) -> bool:
    normalized = event_type.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"pull_request", "pull_request_hook", "merge_request", "merge_request_hook"}:
        return True
    if "pull_request" in payload or "pullRequest" in payload or "merge_request" in payload:
        return True
    return "merge_request" in normalized or "pull_request" in normalized


def is_review_action(action: str) -> bool:
    return action in REVIEW_ACTIONS


def _pull_request_object(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("pull_request", "pullRequest", "merge_request", "mergeRequest"):
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    attrs = _object_attributes(payload)
    return attrs if attrs else {}


def _object_attributes(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = payload.get("object_attributes")
    return candidate if isinstance(candidate, Mapping) else {}


def _repository_name(payload: Mapping[str, Any]) -> str:
    repository = payload.get("repository")
    project = payload.get("project")
    for candidate in (repository, project, _pull_request_object(payload).get("base_repo")):
        if not isinstance(candidate, Mapping):
            continue
        full_name = _first_text(
            candidate.get("full_name"),
            candidate.get("path_with_namespace"),
            candidate.get("pathWithNamespace"),
        )
        if full_name:
            return full_name
        owner = candidate.get("owner")
        owner_name = owner.get("login") if isinstance(owner, Mapping) else owner
        name = _first_text(candidate.get("path"), candidate.get("name"))
        if isinstance(owner_name, str) and owner_name.strip() and name:
            return f"{owner_name.strip()}/{name}"
    return ""


def _normalize_action(payload: Mapping[str, Any], event_type: str) -> str:
    raw = (
        _first_text(
            payload.get("action"),
            payload.get("hook_name"),
            payload.get("hookName"),
            _object_attributes(payload).get("action"),
            _object_attributes(payload).get("state"),
        )
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )
    if raw in {"open", "opened", "create", "created"}:
        return "opened"
    if raw in {"reopen", "reopened"}:
        return "reopened"
    if raw in {"ready", "ready_for_review"}:
        return "ready_for_review"
    if raw in {"update", "updated", "synchronize", "synchronized", "sync"}:
        return "synchronize"
    if raw in {"pull_request", "pull_request_hook", "merge_request", "merge_request_hook"}:
        return "opened"
    if raw:
        return raw
    normalized_event = event_type.lower().replace(" ", "_").replace("-", "_")
    return "opened" if normalized_event in {"pull_request", "merge_request"} else normalized_event or "opened"


def _head_sha(pr: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    last_commit = payload.get("last_commit") if isinstance(payload.get("last_commit"), Mapping) else {}
    attrs = _object_attributes(payload)
    return _first_text(
        head.get("sha"),
        head.get("id"),
        pr.get("head_sha"),
        pr.get("last_commit_id"),
        last_commit.get("id"),
        payload.get("after"),
        attrs.get("last_commit_id"),
    )


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None
