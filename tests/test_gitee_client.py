import json

import httpx
import pytest

from reviewbot.gitee_client import GiteeClient


@pytest.mark.asyncio
async def test_gitee_adapter_reads_pull_request_and_posts_form_comment() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.startswith("/api/v5/repos/owner/repo/pulls/1")
        if request.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "src/example.ts",
                        "status": "modified",
                        "patch": {"diff": "@@ -1 +1,2 @@\n+const value = 1"},
                        "additions": "1",
                        "deletions": "0",
                    },
                    {
                        "filename": "README.md",
                        "status": "added",
                        "patch": "@@ -0,0 +1 @@\n+readme",
                        "additions": 1,
                        "deletions": 0,
                    },
                ],
            )
        if request.url.path.endswith("/comments"):
            if request.method == "GET":
                return httpx.Response(200, json=[])
            assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
            assert "body=Review" in request.content.decode()
            return httpx.Response(201, json={"id": 123})
        if request.method == "POST":
            return httpx.Response(201, json={"id": 123})
        return httpx.Response(
            200,
            json={
                "title": "Example",
                "body": "description",
                "state": "open",
                "head": {"sha": "head-1", "ref": "feature"},
                "base": {"sha": "base-1", "ref": "master"},
                "user": {"login": "contributor"},
                "html_url": "https://gitee.com/owner/repo/pulls/1",
            },
        )

    client = GiteeClient(
        base_url="https://gitee.test/api/v5",
        token="gitee-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        pull_request = await client.get_pull_request("owner/repo", 1)
        comments = await client.list_pull_request_comments("owner/repo", 1)
        files = await client.list_pull_request_files("owner/repo", 1)
        comment_id = await client.create_pull_request_comment("owner/repo", 1, "Review")
    finally:
        await client.close()

    assert pull_request.head_sha == "head-1"
    assert pull_request.base_sha == "base-1"
    assert files[0].filename == "src/example.ts"
    assert files[0].patch == "@@ -1 +1,2 @@\n+const value = 1"
    assert files[0].additions == 1
    assert files[0].deletions == 0
    assert files[1].patch == "@@ -0,0 +1 @@\n+readme"
    assert comments == []
    assert comment_id == 123
    assert all(request.url.params.get("access_token") == "gitee-token" for request in requests)
    assert all("gitee-token" not in json.dumps(dict(request.headers)) for request in requests)
