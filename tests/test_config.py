from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.conftest import FakeOllama, upload


@pytest.mark.parametrize("prefix", ["EVIDENCELENS", "BRAVETRACE"])
def test_environment_settings_reopen_existing_research(tmp_path, monkeypatch, prefix):
    data_dir = tmp_path / "data"
    original = Settings(data_dir=data_dir)
    ai = FakeOllama(original)
    with TestClient(create_app(original, ai)) as client:
        document = upload(client, "Refunds are available within thirty days.").json()["document"]
        assert client.post("/api/index", json={}).status_code == 200
        answer = client.post("/api/ask", json={"query": "refund", "mode": "ai"}).json()
        cached = client.get("/api/health").json()["storage"]["embedded_chunks"]
    assert cached > 0
    assert (data_dir / "evidencelens.sqlite3").is_file()

    for name in ["DATA_DIR", "CHAT_MODEL", "EMBEDDING_MODEL"]:
        for previous in ["EVIDENCELENS", "BRAVETRACE"]:
            monkeypatch.delenv(f"{previous}_{name}", raising=False)
    monkeypatch.setenv(f"{prefix}_DATA_DIR", str(data_dir))
    monkeypatch.setenv(f"{prefix}_CHAT_MODEL", "custom-chat")
    monkeypatch.setenv(f"{prefix}_EMBEDDING_MODEL", original.embedding_model)
    restored = Settings()
    model = FakeOllama(restored)
    assert restored.chat_model == "custom-chat"
    with TestClient(create_app(restored, model)) as client:
        assert client.get("/api/documents").json()["documents"][0]["id"] == document["id"]
        saved = client.get(f"/api/history/{answer['run_id']}").json()
        assert saved["citations"] == answer["citations"]
        assert saved["answer"] == answer["answer"]
        assert client.get("/api/health").json()["storage"]["embedded_chunks"] == cached
        assert (
            client.get(f"/api/documents/{document['id']}/download").text
            == "Refunds are available within thirty days."
        )
    assert model.embed_calls == model.generate_calls == 0
    assert not (data_dir / "bravetrace.sqlite3").exists()


def test_bravetrace_settings_take_precedence_without_writing_to_legacy_directory(
    tmp_path, monkeypatch
):
    legacy_dir = tmp_path / "legacy"
    current_dir = tmp_path / "current"
    for name, legacy, current in [
        ("DATA_DIR", str(legacy_dir), str(current_dir)),
        ("CHAT_MODEL", "legacy-chat", "current-chat"),
        ("EMBEDDING_MODEL", "legacy-embed", "current-embed"),
    ]:
        monkeypatch.setenv(f"EVIDENCELENS_{name}", legacy)
        monkeypatch.setenv(f"BRAVETRACE_{name}", current)
    settings = Settings()
    assert settings.data_dir == Path(current_dir)
    assert settings.chat_model == "current-chat"
    assert settings.embedding_model == "current-embed"
    with TestClient(create_app(settings, FakeOllama(settings))) as client:
        assert upload(client).status_code == 201
        assert len(client.get("/api/documents").json()["documents"]) == 1
    assert (current_dir / "evidencelens.sqlite3").is_file()
    assert not legacy_dir.exists()
