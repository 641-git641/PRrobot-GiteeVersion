import hashlib
import hmac
import json

from reviewbot.diff import build_diff_context, parse_changed_lines
from reviewbot.models import ChangedFile
from reviewbot.webhook import is_review_action, parse_review_job, verify_webhook


def test_webhook_accepts_shared_token_and_normalizes_merge_request_payload() -> None:
    payload = {
        "hook_name": "Merge Request Hook",
        "project": {"path_with_namespace": "liu-huangmin/bjbh"},
        "object_attributes": {
            "iid": 12,
            "last_commit_id": "head-12",
        },
    }
    body = json.dumps(payload).encode()

    assert verify_webhook(body, secret="webhook-secret", token_header="webhook-secret", signature_header=None)
    job = parse_review_job(body=body, event_type="Merge Request Hook", delivery_id="delivery-12")

    assert job.repository == "liu-huangmin/bjbh"
    assert job.pull_request_number == 12
    assert job.action == "opened"
    assert job.webhook_head_sha == "head-12"
    assert is_review_action(job.action)


def test_webhook_accepts_hmac_signature() -> None:
    body = b'{"number":1}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_webhook(
        body,
        secret="secret",
        token_header=None,
        signature_header=f"sha256={digest}",
    )


def test_diff_context_tracks_added_lines_and_omits_large_files() -> None:
    patch = """@@ -1,3 +1,4 @@\n line one\n+line two\n-line three\n+line four\n\\ No newline at end of file\n"""
    assert parse_changed_lines(patch) == {2, 3}

    files = [ChangedFile(filename="src/example.ts", patch=patch, additions=2, deletions=1)]
    context = build_diff_context(files, max_bytes=10_000)
    assert context.has_changed_line("src/example.ts", 2)
    assert not context.has_changed_line("src/example.ts", 4)
    assert "src/example.ts" in context.text

    omitted = build_diff_context(files, max_bytes=1)
    assert omitted.omitted_files == ("src/example.ts",)
    assert "omitted" in omitted.text
