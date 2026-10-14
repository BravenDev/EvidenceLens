import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from backend.errors import AppError
from backend.main import create_app
from tests.conftest import upload


def wait_job(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = client.get(f"/api/index-jobs/{job_id}").json()
        if job["status"] in {"completed", "cancelled", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Index job did not reach a terminal state")


def test_background_index_reports_completion_and_reuses_cache(client):
    upload(client, "Refund evidence.")
    response = client.post("/api/index-jobs", json={})
    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed"
    assert job["indexed"] == 1 and job["percent"] == 100
    second = client.post("/api/index-jobs", json={}).json()
    assert wait_job(client, second["id"])["indexed"] == 0


def test_cancellation_preserves_completed_batch_and_resumes(settings, ai, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original = ai.embed

    def slow(texts):
        started.set()
        assert release.wait(3)
        return original(texts)

    monkeypatch.setattr(ai, "embed", slow)
    try:
        with TestClient(
            create_app(replace(settings, chunk_size=80, chunk_overlap=0), ai)
        ) as client:
            upload(client, "Refund reimbursement policy applies. " * 60)
            total = client.get("/api/health").json()["storage"]["chunks"]
            assert total > 8
            job_id = client.post("/api/index-jobs", json={}).json()["id"]
            assert started.wait(1)
            assert client.post("/api/index-jobs", json={}).status_code == 409
            assert client.post("/api/index", json={}).status_code == 409
            # The application remains responsive during the model request.
            assert client.post("/api/search", json={"query": "refund"}).status_code == 200
            assert (
                client.post(f"/api/index-jobs/{job_id}/cancel", json={}).json()["status"]
                == "cancelling"
            )
            release.set()
            stopped = wait_job(client, job_id)
            assert stopped["status"] == "cancelled"
            assert stopped["indexed"] == 8
            assert stopped["remaining"] == total - 8
            resumed = client.post("/api/index-jobs", json={}).json()
            done = wait_job(client, resumed["id"])
            assert done["indexed"] == total - 8
            assert done["status"] == "completed" and done["remaining"] == 0
    finally:
        release.set()


def test_job_failure_releases_lock_and_keeps_readable_error(client, ai, monkeypatch):
    upload(client)
    original = ai.embed

    def unavailable(texts):
        raise AppError("model_missing", "Install the embedding model.", 503)

    monkeypatch.setattr(ai, "embed", unavailable)
    job = client.post("/api/index-jobs", json={}).json()
    failed = wait_job(client, job["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "model_missing"
    monkeypatch.setattr(ai, "embed", original)
    next_job = client.post("/api/index-jobs", json={}).json()
    assert wait_job(client, next_job["id"])["status"] == "completed"


def test_already_cancelled_rebuild_keeps_previous_cache(client, ai):
    upload(client)
    client.post("/api/index", json={})
    stop = threading.Event()
    stop.set()
    result = client.app.state.research.retriever.index(force=True, cancel=stop)
    assert result["cancelled"] and result["indexed"] == 0
    assert client.get("/api/health").json()["storage"]["embedded_chunks"] == 1


def test_unknown_job_is_404_and_completed_cancel_is_idempotent(client):
    assert client.get("/api/index-jobs/missing").status_code == 404
    upload(client)
    job = client.post("/api/index-jobs", json={}).json()
    done = wait_job(client, job["id"])
    assert (
        client.post(f"/api/index-jobs/{job['id']}/cancel", json={}).json()["status"]
        == done["status"]
    )
