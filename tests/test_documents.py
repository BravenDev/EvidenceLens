from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from tests.conftest import pdf_bytes, upload


def test_upload_search_and_exact_source_location(client):
    text = "Introduction.\n\nThe retention period is thirty days.\nContact the owner."
    added = upload(client, text).json()["document"]
    found = client.post("/api/search", json={"query": "retention period"}).json()["sources"]
    assert found[0]["document_id"] == added["id"]
    detail = client.get(found[0]["url"]).json()
    assert detail["page_text"][detail["start"] : detail["end"]] == detail["text"]
    assert detail["line_start"] == 1
    assert detail["line_end"] == 4
    downloaded = client.get(f"/api/documents/{added['id']}/download")
    assert downloaded.content == text.encode()
    assert "attachment" in downloaded.headers["content-disposition"]


def test_duplicate_bytes_are_not_reindexed(client):
    first = upload(client).json()
    second = upload(client, filename="renamed.md").json()
    assert second["duplicate"] is True
    assert first["document"]["id"] == second["document"]["id"]
    assert len(client.get("/api/documents").json()["documents"]) == 1


def test_restart_preserves_documents_and_fts(settings, ai):
    with TestClient(create_app(settings, ai)) as first:
        document = upload(first, "Persistent quasar evidence.").json()["document"]
    with TestClient(create_app(settings, ai)) as second:
        assert second.get("/api/documents").json()["documents"][0]["id"] == document["id"]
        assert second.post("/api/search", json={"query": "quasar"}).json()["sources"]


def test_pdf_page_citation_and_blank_page_warning(client):
    response = upload(
        client,
        pdf_bytes(["Orbital mechanics are on page one.", None, "Refund window is fourteen days."]),
        "report.pdf",
    )
    assert response.status_code == 201
    assert "1 PDF page" in response.json()["warning"]
    source = client.post("/api/search", json={"query": "refund"}).json()["sources"][0]
    assert source["page"] == 3
    assert source["location"] == "Page 3"
    detail = client.get(source["url"]).json()
    assert "fourteen" in detail["page_text"]


@pytest.mark.parametrize(
    "content,name,code,status",
    [
        (b"", "empty.txt", "empty_file", 400),
        (b" \n\t", "empty.md", "no_text", 400),
        (b"hello", "script.exe", "unsupported_type", 415),
        (b"\xff\xfe", "broken.txt", "invalid_encoding", 400),
        (b"binary\x00data", "binary.txt", "binary_file", 400),
        (b"fake pdf", "fake.pdf", "invalid_pdf", 400),
        (b"%PDF-1.7 invalid", "broken.pdf", "invalid_pdf", 400),
    ],
)
def test_invalid_uploads_have_structured_errors(client, content, name, code, status):
    response = upload(client, content, name)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert not client.get("/api/documents").json()["documents"]


def test_scanned_or_blank_pdf_is_not_silently_indexed(client):
    response = upload(client, pdf_bytes([None]), "scan.pdf")
    assert response.json()["error"]["code"] == "no_text"


def test_encrypted_pdf_is_rejected(client):
    response = upload(client, pdf_bytes(["Private text."], password="secret"), "secret.pdf")
    assert response.json()["error"]["code"] == "encrypted_pdf"


def test_windows_and_posix_paths_are_not_used_for_storage(client, settings):
    response = upload(client, "Safe storage.", r"C:\outside\..\notes.txt")
    doc = response.json()["document"]
    assert doc["filename"] == "notes.txt"
    assert list(settings.upload_dir.iterdir())[0].name == f"{doc['id']}.txt"


def test_delete_removes_file_fts_and_embeddings(client, ai, settings):
    doc = upload(client, "Refund reimbursement evidence.").json()["document"]
    client.post("/api/index", json={})
    cid = client.post("/api/search", json={"query": "refund"}).json()["sources"][0]["chunk_id"]
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/chunks/{cid}").status_code == 404
    assert not client.post("/api/search", json={"query": "refund"}).json()["sources"]
    assert client.get("/api/health").json()["storage"]["embedded_chunks"] == 0
    assert not list(settings.upload_dir.iterdir())


def test_utf8_bom_crlf_and_unicode_are_preserved(client):
    text = "\ufeffFirst line.\r\nKuşadası araştırma belgesi.\r\nFinal line."
    assert upload(client, text).status_code == 201
    sources = client.post("/api/search", json={"query": "Kuşadası"}).json()["sources"]
    assert sources and "Kuşadası" in sources[0]["text"]
    detail = client.get(sources[0]["url"]).json()
    assert "\r" not in detail["page_text"]
    assert detail["line_end"] == 3


def test_document_limit_allows_duplicates_but_rejects_new_documents(settings, ai):
    with TestClient(create_app(replace(settings, max_documents=1), ai)) as client:
        assert upload(client).status_code == 201
        assert upload(client).json()["duplicate"]
        assert upload(client, "Another document.").status_code == 409


def test_byte_and_request_limits_are_enforced_before_indexing(settings, ai):
    with TestClient(create_app(replace(settings, max_upload_bytes=32), ai)) as client:
        assert upload(client, b"a" * 33).status_code == 413
        assert upload(client, b"a" * 70000).json()["error"]["code"] == "request_too_large"
        assert not client.get("/api/documents").json()["documents"]


def test_parallel_duplicate_uploads_create_one_document(client):
    research = client.app.state.research
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: research.upload("race.txt", b"Concurrent indexing evidence."), range(4)
            )
        )
    assert sum(not result["duplicate"] for result in results) == 1
    assert len(client.get("/api/documents").json()["documents"]) == 1


def test_demo_is_idempotent(client):
    assert client.post("/api/demo", json={}).json()["loaded"] == 3
    assert client.post("/api/demo", json={}).json()["duplicates"] == 3


def test_pdf_page_limit_is_enforced(settings, ai):
    with TestClient(create_app(replace(settings, max_pages=1), ai)) as client:
        response = upload(client, pdf_bytes(["First page.", "Second page."]), "large.pdf")
        assert response.json()["error"]["code"] == "too_many_pages"
        assert not client.get("/api/documents").json()["documents"]


def test_extracted_character_limit_is_enforced(settings, ai):
    with TestClient(create_app(replace(settings, max_text_chars=10), ai)) as client:
        response = upload(client, "Eleven characters or more.")
        assert response.json()["error"]["code"] == "text_too_large"
        assert not client.get("/api/documents").json()["documents"]
