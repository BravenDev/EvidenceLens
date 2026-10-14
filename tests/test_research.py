import pytest

from backend.errors import AppError
from backend.extraction import PageText, split_passages
from backend.research import validate_answer
from tests.conftest import upload


def test_chunks_cover_text_preserve_offsets_and_do_not_cross_pages():
    texts = [("alpha beta gamma delta " * 50).strip(), ("second page content " * 30).strip()]
    pages = [PageText(texts[0], 1), PageText(texts[1], 2)]
    chunks = split_passages(pages, size=100, overlap=20)
    assert len(chunks) > 2
    for page_number, text in enumerate(texts, 1):
        chunks_here = [c for c in chunks if c.page == page_number]
        coverage = set()
        for chunk in chunks_here:
            assert text[chunk.start : chunk.end] == chunk.text
            assert len(chunk.text) <= 100
            coverage.update(range(chunk.start, chunk.end))
        assert all(i in coverage for i, char in enumerate(text) if not char.isspace())


def test_document_filter_is_respected(client):
    first = upload(client, "Refunds are available for fourteen days.", "refund.txt").json()[
        "document"
    ]
    upload(client, "Refunds are available for seven days.", "other.txt")
    found = client.post(
        "/api/search", json={"query": "refunds", "document_id": first["id"]}
    ).json()["sources"]
    assert found and {item["document_id"] for item in found} == {first["id"]}


def test_fts_and_sql_syntax_from_user_is_safe(client):
    upload(client, "Alpha evidence is searchable.")
    result = client.post(
        "/api/search", json={"query": 'alpha OR NEAR("x") ; DROP TABLE documents; --'}
    )
    assert result.status_code == 200
    assert len(client.get("/api/documents").json()["documents"]) == 1


def test_no_matches_abstains_before_model_call(client, ai):
    upload(client)
    result = client.post(
        "/api/ask", json={"query": "xylophonist interstellar nebula", "mode": "ai"}
    ).json()
    assert result["abstained"] is True
    assert not result["citations"]
    assert ai.generate_calls == 0


def test_evidence_mode_is_explicit_and_does_not_generate(client, ai):
    upload(client)
    result = client.post("/api/ask", json={"query": "pilot timeline"}).json()
    assert result["mode"] == "evidence"
    assert result["model"] is None
    assert result["sources"]
    assert ai.generate_calls == 0


def test_ai_citations_resolve_to_actual_sources(client, ai):
    upload(client)
    result = client.post("/api/ask", json={"query": "pilot", "mode": "ai"}).json()
    citation = result["citations"][0]
    assert f"[{citation['label']}]" in result["answer"]
    assert client.get(citation["url"]).status_code == 200
    assert ai.generate_calls == 1


def test_hallucinated_citations_withhold_model_answer(client, ai):
    upload(client)
    ai.response = {
        "answer": "An unsupported claim [S99999]",
        "citations": [99999],
        "abstained": False,
    }
    response = client.post("/api/ask", json={"query": "pilot", "mode": "ai"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_citations"
    assert "answer" not in response.json()


@pytest.mark.parametrize(
    "response",
    [
        {"answer": "An uncited claim.", "citations": [], "abstained": False},
        {"answer": "A claim [S1]", "citations": [2], "abstained": False},
        {"answer": "A claim [S1]", "citations": [1, 1], "abstained": False},
        {"answer": "No evidence [S1]", "citations": [1], "abstained": True},
        {"answer": "A claim [S1]", "citations": [True], "abstained": False},
    ],
)
def test_malformed_or_inconsistent_citations_are_rejected(response):
    with pytest.raises(AppError):
        validate_answer(response, [{"chunk_id": 1}, {"chunk_id": 2}])


def test_explicit_model_abstention_is_accepted(client, ai):
    upload(client)
    ai.response = {
        "answer": "The supplied evidence does not answer this question.",
        "citations": [],
        "abstained": True,
    }
    result = client.post("/api/ask", json={"query": "pilot", "mode": "ai"}).json()
    assert result["abstained"] and not result["citations"]


def test_ai_failure_is_reported_not_silently_replaced(client, ai, monkeypatch):
    upload(client)

    def fail(*args):
        raise AppError("ollama_unavailable", "Ollama is not running.", 503)

    monkeypatch.setattr(ai, "generate", fail)
    result = client.post("/api/ask", json={"query": "pilot", "mode": "ai"})
    assert result.status_code == 503


def test_hybrid_requires_index_and_retrieves_synonyms(client, ai):
    doc = upload(
        client, "Customers can request reimbursement within fourteen days.", "returns.txt"
    ).json()["document"]
    upload(client, "Vegetable recipe: cook carrots for twenty minutes.", "recipe.txt")
    query = {"query": "How do I get my money back?", "retrieval": "hybrid"}
    assert client.post("/api/search", json=query).json()["error"]["code"] == "index_required"
    assert client.post("/api/index", json={}).json()["indexed"] == 2
    result = client.post("/api/search", json=query).json()
    assert result["sources"][0]["document_id"] == doc["id"]
    assert not client.post("/api/search", json={"query": query["query"]}).json()["sources"]


def test_embedding_cache_is_reused_and_rebuildable(client, ai):
    upload(client, "Refund evidence.")
    assert client.post("/api/index", json={}).json()["indexed"] == 1
    calls = ai.embed_calls
    assert client.post("/api/index", json={}).json()["indexed"] == 0
    assert ai.embed_calls == calls
    assert client.post("/api/index", json={"force": True}).json()["indexed"] == 1
    assert ai.embed_calls == calls + 1


def test_changed_embedding_dimensions_request_rebuild(client, ai):
    upload(client, "Refund evidence.")
    client.post("/api/index", json={})
    source = client.post("/api/search", json={"query": "refund"}).json()["sources"][0]
    client.app.state.store.save_embeddings(
        ai.settings.embedding_model, [(source["chunk_id"], [1.0, 0.0])]
    )
    result = client.post("/api/search", json={"query": "refund", "retrieval": "hybrid"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "embedding_mismatch"


def test_deletion_during_embedding_does_not_resurrect_document(client, ai, monkeypatch):
    doc = upload(client, "Refund evidence.").json()["document"]
    original = ai.embed

    def deleting_embed(texts):
        client.app.state.store.remove(doc["id"])
        return original(texts)

    monkeypatch.setattr(ai, "embed", deleting_embed)
    assert client.post("/api/index", json={}).json()["indexed"] == 0
    assert client.get("/api/health").json()["storage"]["documents"] == 0
