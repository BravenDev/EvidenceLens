import asyncio

import pytest

from backend.main import BodyLimitMiddleware
from tests.conftest import upload


def test_interface_assets_and_api_documentation_are_available(client):
    assert client.get("/").status_code == 200
    for path in [
        "/assets/app.js",
        "/assets/styles.css",
        "/assets/favicon.svg",
        "/api/docs",
        "/openapi.json",
    ]:
        assert client.get(path).status_code == 200
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]
    assert client.get("/").headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": None},
        {"query": "pilot", "limit": 0},
        {"query": "pilot", "limit": 9},
        {"query": "pilot", "retrieval": "made-up"},
        {"query": "pilot", "mode": "made-up"},
        {"query": "pilot", "document_id": "../../etc/passwd"},
        {"query": "pilot", "unexpected": "value"},
    ],
)
def test_invalid_research_input_is_rejected(client, payload):
    response = client.post("/api/ask", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_cross_origin_mutations_are_rejected(client):
    response = client.post("/api/demo", json={}, headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_blocked"


def test_same_origin_mutations_are_allowed(client):
    assert (
        client.post("/api/demo", json={}, headers={"Origin": "http://testserver"}).status_code
        == 200
    )


def test_unknown_host_is_rejected(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400


def test_unknown_document_or_chunk_is_a_readable_404(client):
    assert client.get("/api/chunks/9999").status_code == 404
    assert client.delete("/api/documents/missing").status_code == 404
    assert (
        client.post("/api/search", json={"query": "pilot", "document_id": "a" * 32}).status_code
        == 404
    )


def test_html_like_document_content_is_returned_as_data(client):
    attack = "<script>window.xssHit=true</script> Evidence is data."
    upload(client, attack, "injection.txt")
    result = client.post("/api/search", json={"query": "evidence"}).json()["sources"][0]
    assert result["text"] == attack
    # Browser checks separately verify that this data is rendered using text nodes.
    assert "application/json" in client.get(result["url"]).headers["content-type"]


def test_streaming_request_limit_is_enforced_without_content_length():
    async def run():
        entered = False
        sent = []
        messages = iter(
            [
                {"type": "http.request", "body": b"x" * 40000, "more_body": True},
                {"type": "http.request", "body": b"x" * 40000, "more_body": False},
            ]
        )

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        async def inner(scope, receive, send):
            nonlocal entered
            entered = True

        middleware = BodyLimitMiddleware(inner, upload_limit=32)
        await middleware(
            {"type": "http", "method": "POST", "path": "/api/documents", "headers": []},
            receive,
            send,
        )
        assert not entered
        assert sent[0]["status"] == 413

    asyncio.run(run())
