import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.extraction import PageText, split_passages
from backend.main import create_app
from backend.storage import Store
from tests.conftest import upload


def test_paragraph_splitting_keeps_exact_text_and_word_boundaries():
    paragraph = "A complete policy paragraph explains how local source evidence is retained."
    text = paragraph + "\n\n" + "The next section contains more reference material. " * 15
    chunks = split_passages([PageText(text)], size=140, overlap=20)
    assert chunks[0].text == paragraph
    coverage = set()
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        assert chunk.start == 0 or text[chunk.start - 1].isspace()
        coverage.update(range(chunk.start, chunk.end))
    assert all(i in coverage for i, char in enumerate(text) if not char.isspace())


def test_incidental_single_word_matches_are_filtered(client):
    upload(client, "The pilot budget is approved for six weeks.")
    assert not client.post("/api/search", json={"query": "pilot telescope"}).json()["sources"]
    assert not client.post("/api/ask", json={"query": "budget volcano"}).json()["sources"]
    assert client.post("/api/search", json={"query": "pilot budget"}).json()["sources"]


def test_english_word_variants_match_with_exact_source_text(client):
    text = "The security exception needs written approval. Logs record the approving decision."
    upload(client, text)
    sources = client.post("/api/search", json={"query": "approve security exceptions"}).json()[
        "sources"
    ]
    assert sources and sources[0]["text"] == text
    assert "exception" in sources[0]["excerpt"]


def test_single_digit_numbers_distinguish_results(client):
    week_two = upload(client, "Week 2 covers collection.", "week2.txt").json()["document"]
    week_six = upload(client, "Week 6 covers evaluation.", "week6.txt").json()["document"]
    found = client.post("/api/search", json={"query": "week 6"}).json()["sources"]
    assert {item["document_id"] for item in found} == {week_six["id"]}
    assert week_two["id"] not in {item["document_id"] for item in found}


def test_number_constraints_also_apply_to_hybrid(client):
    upload(client, "Refund policy version 2.", "old.txt")
    current = upload(client, "Refund policy version 6.", "new.txt").json()["document"]
    client.post("/api/index", json={})
    found = client.post("/api/search", json={"query": "refund 6", "retrieval": "hybrid"}).json()[
        "sources"
    ]
    assert found and {item["document_id"] for item in found} == {current["id"]}


def test_ask_and_search_history_survive_restart(settings, ai):
    with TestClient(create_app(settings, ai)) as client:
        upload(client)
        answer = client.post("/api/ask", json={"query": "pilot", "mode": "ai"}).json()
        search = client.post("/api/search", json={"query": "pilot"}).json()
    calls = ai.generate_calls
    with TestClient(create_app(settings, ai)) as client:
        history = client.get("/api/history").json()["runs"]
        assert len(history) == 2
        assert history[0]["id"] == search["run_id"]
        restored = client.get(f"/api/history/{answer['run_id']}").json()
        assert restored["answer"] == answer["answer"]
        assert restored["citations"] == answer["citations"]
        assert ai.generate_calls == calls


def test_deleting_document_removes_its_saved_research(client):
    doc = upload(client).json()["document"]
    run = client.post("/api/ask", json={"query": "pilot"}).json()
    client.post("/api/search", json={"query": "spaceship", "document_id": doc["id"]})
    unrelated = client.post("/api/search", json={"query": "xylophonist"}).json()
    client.delete(f"/api/documents/{doc['id']}")
    assert client.get(f"/api/history/{run['run_id']}").status_code == 404
    assert [item["id"] for item in client.get("/api/history").json()["runs"]] == [
        unrelated["run_id"]
    ]


def test_history_is_bounded_and_can_be_deleted(settings, ai):
    with TestClient(create_app(replace(settings, max_history_entries=2), ai)) as client:
        upload(client)
        runs = [
            client.post("/api/search", json={"query": query}).json()
            for query in ["pilot", "weeks", "six"]
        ]
        history = client.get("/api/history").json()["runs"]
        assert len(history) == 2
        assert client.get(f"/api/history/{runs[0]['run_id']}").status_code == 404
        assert client.delete(f"/api/history/{runs[1]['run_id']}").status_code == 200
        assert client.delete("/api/history").status_code == 200
        assert not client.get("/api/history").json()["runs"]
        assert client.get("/api/documents").json()["documents"]


def test_deleted_source_during_generation_withholds_stale_result(client, ai, monkeypatch):
    doc = upload(client).json()["document"]
    original = ai.generate

    def disappearing(question, sources, schema, **kwargs):
        answer = original(question, sources, schema, **kwargs)
        client.app.state.store.remove(doc["id"])
        return answer

    monkeypatch.setattr(ai, "generate", disappearing)
    response = client.post("/api/ask", json={"query": "pilot", "mode": "ai"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_changed"
    assert not client.get("/api/history").json()["runs"]


def test_invalid_citation_format_gets_one_repair_attempt(client, ai, monkeypatch):
    upload(client)
    repairs = []

    def generate(question, sources, schema, *, repair=False):
        repairs.append(repair)
        if not repair:
            return {"answer": "A claim [S99999]", "citations": [99999], "abstained": False}
        cid = sources[0]["chunk_id"]
        return {"answer": f"Six weeks [S{cid}]", "citations": [cid], "abstained": False}

    monkeypatch.setattr(ai, "generate", generate)
    result = client.post("/api/ask", json={"query": "pilot", "mode": "ai"}).json()
    assert result["repaired"] is True
    assert repairs == [False, True]
    assert result["citations"]


def test_persistent_invalid_response_stops_after_two_attempts(client, ai):
    upload(client)
    ai.response = {"answer": "A claim [S99999]", "citations": [99999], "abstained": False}
    assert client.post("/api/ask", json={"query": "pilot", "mode": "ai"}).status_code == 502
    assert ai.generate_calls == 2
    assert not client.get("/api/history").json()["runs"]


def test_schema_v1_migration_preserves_ids_files_vectors_and_search(settings):
    settings.upload_dir.mkdir(parents=True)
    schema = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()
    text = "Logging decisions requires written approval."
    doc_id = "a" * 32
    original = settings.upload_dir / f"{doc_id}.txt"
    original.write_text(text)
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.executescript(schema)
        with conn:
            conn.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    "legacy.txt",
                    "legacy-hash",
                    "txt",
                    len(text),
                    0,
                    1,
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.execute("INSERT INTO pages VALUES (?,?,?,?)", (doc_id, 0, None, text))
            conn.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)",
                (77, doc_id, 0, None, 0, len(text), 1, 1, text),
            )
            conn.execute(
                "INSERT INTO embeddings VALUES (?,?,?,?)",
                (77, settings.embedding_model, 3, json.dumps([1, 0, 0])),
            )
    store = Store(settings)
    store.initialize()
    store.initialize()
    assert store.get_source(77)["text"] == text
    assert store.keyword_search('"approve"', 5)[0]["chunk_id"] == 77
    assert store.vectors(settings.embedding_model)[0][1] == [1, 0, 0]
    assert original.read_text() == text
    with closing(store.connect()) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_future_database_version_is_rejected(settings):
    settings.data_dir.mkdir(parents=True)
    with closing(sqlite3.connect(settings.db_path)) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        Store(settings).initialize()
