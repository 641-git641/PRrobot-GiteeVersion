from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status

from reviewbot.config import Settings, get_settings
from reviewbot.deepseek_client import DeepSeekClient
from reviewbot.gitee_client import GiteeClient
from reviewbot.reviewer import ReviewEngine
from reviewbot.service import ReviewService
from reviewbot.storage import QueueStore
from reviewbot.webhook import WebhookError, is_pull_request_event, is_review_action, parse_review_job, verify_webhook
from reviewbot.worker import ReviewWorker


def create_app(
    settings: Settings | None = None,
    *,
    gitee_client: GiteeClient | None = None,
    engine: ReviewEngine | None = None,
    store: QueueStore | None = None,
) -> FastAPI:
    runtime = settings or get_settings()
    runtime.validate_runtime()
    queue_store = store or QueueStore(runtime.database_path)
    gitee = gitee_client or GiteeClient(
        base_url=runtime.api_base_url,
        token=runtime.gitee_api_token.get_secret_value(),  # type: ignore[union-attr]
        auth_mode=runtime.gitee_auth_mode,
        timeout_seconds=runtime.request_timeout_seconds,
    )
    deepseek = None
    review_engine = engine
    if review_engine is None:
        deepseek = DeepSeekClient(
            base_url=runtime.deepseek_base_url,
            api_key=runtime.deepseek_api_key.get_secret_value(),  # type: ignore[union-attr]
            model=runtime.deepseek_model,
            thinking_enabled=runtime.deepseek_thinking_enabled,
            reasoning_effort=runtime.deepseek_reasoning_effort,
            timeout_seconds=runtime.request_timeout_seconds,
            max_retries=runtime.max_retries,
        )
        review_engine = ReviewEngine(deepseek)

    service = ReviewService(
        store=queue_store,
        gitee=gitee,
        engine=review_engine,
        rule_file=runtime.review_rule_file,
        max_diff_bytes=runtime.max_diff_bytes,
        max_review_bytes=runtime.max_review_bytes,
        allowlist=runtime.repo_allowlist,
        enabled=runtime.review_enabled,
    )
    worker = ReviewWorker(store=queue_store, service=service, settings=runtime)
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event.clear()
        await asyncio.to_thread(queue_store.initialize)
        task = asyncio.create_task(worker.run(stop_event), name="review-worker")
        app.state.ready = True
        try:
            yield
        finally:
            stop_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close_gitee = getattr(gitee, "close", None)
            if close_gitee is not None:
                result = close_gitee()
                if inspect.isawaitable(result):
                    await result
            if deepseek is not None:
                await deepseek.close()
            app.state.ready = False

    app = FastAPI(title="bh-gitee-review-bot", version="0.1.0", lifespan=lifespan)
    app.state.ready = False
    app.state.settings = runtime
    app.state.store = queue_store

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "reviewEnabled": runtime.review_enabled}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        if not app.state.ready:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "robot is starting")
        return {"status": "ready", "queue": await asyncio.to_thread(queue_store.counts)}

    @app.post("/webhook/gitee", status_code=status.HTTP_202_ACCEPTED)
    async def gitee_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        if len(body) > runtime.max_diff_bytes * 2:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "webhook payload too large")

        token_header = request.headers.get("X-Gitee-Token")
        signature_header = request.headers.get("X-Gitee-Signature") or request.headers.get("X-Hub-Signature-256")
        secret = runtime.gitee_webhook_secret.get_secret_value()  # type: ignore[union-attr]
        if not verify_webhook(body, secret=secret, token_header=token_header, signature_header=signature_header):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")

        event_type = request.headers.get("X-Gitee-Event", "pull_request")
        payload = _json_object(body)
        if not is_pull_request_event(event_type, payload):
            return {"state": "skipped", "reason": "not a Pull Request event"}

        try:
            job = parse_review_job(
                body=body,
                event_type=event_type,
                delivery_id=request.headers.get("X-Gitee-Delivery") or request.headers.get("X-Request-Id"),
            )
        except WebhookError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

        if job.repository.lower() not in runtime.repo_allowlist:
            return {"state": "skipped", "reason": "repository not allowlisted"}
        if not runtime.review_enabled:
            return {"state": "skipped", "reason": "review disabled"}
        if not is_review_action(job.action):
            return {"state": "skipped", "reason": f"action {job.action!r} ignored"}

        inserted = await asyncio.to_thread(queue_store.enqueue, job)
        return {"state": "queued" if inserted else "duplicate", "deliveryId": job.delivery_id}

    return app


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
