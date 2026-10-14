import json

import httpx
import pytest

from backend.errors import AppError
from backend.ollama import Ollama, normalized
from backend.research import CitedAnswer


def mock_http(monkeypatch, handler):
    original = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))


def test_embedding_http_contract_and_normalization(monkeypatch, settings):
    def handler(request):
        assert request.url.path == "/api/embed"
        payload = json.loads(request.content)
        assert payload["model"] == settings.embedding_model
        assert payload["input"] == ["first", "second"]
        assert payload["truncate"] is False
        return httpx.Response(200, json={"embeddings": [[3, 4], [0, 5]]})

    mock_http(monkeypatch, handler)
    vectors = Ollama(settings).embed(["first", "second"])
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert vectors[1] == [0, 1]


def test_generation_http_contract_includes_evidence_and_schema(monkeypatch, settings):
    def handler(request):
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["format"]["required"] == ["answer", "citations", "abstained"]
        context = json.loads(payload["messages"][1]["content"])
        assert context["evidence"][0]["chunk_id"] == 7
        assert "untrusted" in payload["messages"][0]["content"]
        output = {"answer": "Thirty days [S7]", "citations": [7], "abstained": False}
        return httpx.Response(200, json={"message": {"content": json.dumps(output)}})

    mock_http(monkeypatch, handler)
    result = Ollama(settings).generate(
        "How long?",
        [
            {
                "chunk_id": 7,
                "filename": "policy.txt",
                "location": "Lines 1-2",
                "text": "Retention is thirty days.",
            }
        ],
        CitedAnswer.model_json_schema(),
    )
    assert result["citations"] == [7]


@pytest.mark.parametrize("status,code", [(404, "model_missing"), (500, "model_error")])
def test_model_http_failures_are_readable(monkeypatch, settings, status, code):
    mock_http(monkeypatch, lambda request: httpx.Response(status, json={"error": "failure"}))
    with pytest.raises(AppError) as exc:
        Ollama(settings).embed(["text"])
    assert exc.value.code == code


@pytest.mark.parametrize(
    "vector", [[], [0, 0], [float("nan"), 1], [float("inf"), 1], [True, 1], ["wrong", 1]]
)
def test_invalid_embedding_vectors_are_rejected(vector):
    with pytest.raises(AppError):
        normalized(vector)


def test_large_finite_embeddings_do_not_overflow():
    assert normalized([1e200, 1e200]) == pytest.approx([2**-0.5, 2**-0.5])


def test_wrong_embedding_count_is_rejected(monkeypatch, settings):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(AppError) as exc:
        Ollama(settings).embed(["text"])
    assert exc.value.code == "invalid_embedding"


def test_model_timeout_is_readable(monkeypatch, settings):
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    mock_http(monkeypatch, timeout)
    with pytest.raises(AppError) as exc:
        Ollama(settings).embed(["text"])
    assert exc.value.code == "ai_timeout"


def test_unavailable_model_status_does_not_break_search(monkeypatch, settings):
    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)

    mock_http(monkeypatch, unavailable)
    assert Ollama(settings).status()["available"] is False


@pytest.mark.parametrize("models", [None, {"name": "wrong-container"}])
def test_malformed_model_list_has_unavailable_status(monkeypatch, settings, models):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"models": models}))
    status = Ollama(settings).status()
    assert status == {
        "available": False,
        "chat_ready": False,
        "embedding_ready": False,
        "models": [],
    }
