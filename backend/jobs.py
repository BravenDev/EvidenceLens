"""A cancellable local worker builds embeddings without blocking the browser."""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from backend.errors import AppError
from backend.research import IndexRequest, Research

logger = logging.getLogger("evidencelens.jobs")


class IndexJobs:
    def __init__(self, research: Research) -> None:
        self.research = research
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidencelens-index")
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}
        self.stops: dict[str, threading.Event] = {}
        self.closed = False

    def start(self, request: IndexRequest) -> dict:
        if request.document_id:
            self.research.store.document(request.document_id)
        if not self.research.index_lock.acquire(blocking=False):
            raise AppError("index_busy", "A semantic index build is already running.", 409)
        job_id = uuid.uuid4().hex
        stop = threading.Event()
        try:
            with self.lock:
                if self.closed:
                    raise AppError("shutting_down", "The application is shutting down.", 503)
                for old_id in list(self.records):
                    if len(self.records) < 20:
                        break
                    if self.records[old_id]["status"] in {"completed", "cancelled", "failed"}:
                        self.records.pop(old_id)
                        self.stops.pop(old_id)
                self.records[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "created_at": datetime.now(UTC).isoformat(),
                    "total": 0,
                    "processed": 0,
                    "indexed": 0,
                    "remaining": 0,
                    "percent": 0,
                    "error": None,
                    "model": self.research.settings.embedding_model,
                    "document_id": request.document_id,
                }
                self.stops[job_id] = stop
                self.pool.submit(self._work, job_id, request, stop)
            return self.get(job_id)
        except Exception:
            self.research.index_lock.release()
            raise

    def _work(self, job_id: str, request: IndexRequest, stop: threading.Event) -> None:
        try:
            with self.lock:
                self.records[job_id]["status"] = "running"

            def progress(total: int, processed: int, indexed: int) -> None:
                with self.lock:
                    self.records[job_id].update(
                        total=total,
                        processed=processed,
                        indexed=indexed,
                        percent=round(processed / total * 100) if total else 100,
                        remaining=max(0, total - processed),
                    )

            result = self.research.retriever.index(
                request.document_id, request.force, progress, stop
            )
            with self.lock:
                self.records[job_id].update(
                    status="cancelled" if result["cancelled"] else "completed",
                    indexed=result["indexed"],
                    remaining=result["remaining"],
                )
        except AppError as exc:
            with self.lock:
                self.records[job_id].update(
                    status="failed", error={"code": exc.code, "message": exc.message}
                )
        except Exception:
            logger.exception("Index worker failed")
            with self.lock:
                self.records[job_id].update(
                    status="failed",
                    error={
                        "code": "index_error",
                        "message": "The index build failed. Check the local server terminal.",
                    },
                )
        finally:
            self.research.index_lock.release()

    def get(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.records:
                raise AppError(
                    "not_found",
                    "This indexing job is unavailable. Completed vectors are still saved.",
                    404,
                )
            return dict(self.records[job_id])

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            result = self.get(job_id)
            if result["status"] in {"queued", "running", "cancelling"}:
                self.stops[job_id].set()
                self.records[job_id]["status"] = "cancelling"
        return self.get(job_id)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for stop in self.stops.values():
                stop.set()
        # Current model requests can finish; completed batches are already committed.
        self.pool.shutdown(wait=True)
