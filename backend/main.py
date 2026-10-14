"""FastAPI application, middleware, and routes."""

import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.config import ROOT, Settings
from backend.errors import AppError
from backend.jobs import IndexJobs
from backend.ollama import Ollama
from backend.research import AskRequest, IndexRequest, Research, SearchRequest
from backend.storage import Store

logger = logging.getLogger("evidencelens")


class BodyLimitMiddleware:
    """Bound request bytes BEFORE multipart parsing, including chunked requests."""

    def __init__(self, app, upload_limit: int) -> None:
        self.app = app
        self.upload_limit = upload_limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        limit = self.upload_limit + 65536 if scope["path"] == "/api/documents" else 65536
        parts = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            part = message.get("body", b"")
            size += len(part)
            if size > limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "request_too_large",
                            "message": "This upload or request exceeds the size limit.",
                        }
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            parts.append(part)
            if not message.get("more_body", False):
                break
        body = b"".join(parts)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, ai: Ollama | None = None) -> FastAPI:
    config = settings or Settings()
    store = Store(config)
    model = ai or Ollama(config)
    research = Research(config, store, model)
    jobs = IndexJobs(research)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        try:
            yield
        finally:
            await run_in_threadpool(jobs.close)

    application = FastAPI(
        title="EvidenceLens",
        version="0.2.0",
        lifespan=lifespan,
        description="Local document research with inspectable source passages.",
        docs_url="/api/docs",
        redoc_url=None,
    )
    application.state.store = store
    application.state.research = research
    application.state.jobs = jobs
    application.add_middleware(BodyLimitMiddleware, upload_limit=config.max_upload_bytes)
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )

    @application.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            parsed = urlparse(origin)
            expected = urlparse(str(request.base_url))
            if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
                return JSONResponse(
                    {
                        "error": {
                            "code": "origin_blocked",
                            "message": "Use EvidenceLens from its own browser tab.",
                        }
                    },
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            if not request.url.path.startswith("/api/docs")
            else "frame-ancestors 'none'"
        )
        return response

    @application.exception_handler(AppError)
    async def expected_error(request: Request, exc: AppError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_input",
                    "message": "Check your question, document selection, and search settings.",
                }
            },
            status_code=422,
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {
                "error": {
                    "code": "http_error",
                    "message": "The requested endpoint or request format is unavailable.",
                }
            },
            status_code=exc.status_code,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.exception("Unexpected application error")
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred. Check the local server terminal.",
                }
            },
            status_code=500,
        )

    @application.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "frontend" / "index.html")

    @application.get("/api/health", tags=["system"])
    def health():
        return {
            "status": "ok",
            "version": "0.2.0",
            "python": sys.version.split()[0],
            "storage": store.stats(config.embedding_model),
        }

    @application.get("/api/status", tags=["system"])
    def status():
        return {
            "storage": store.stats(config.embedding_model),
            "ai": model.status(),
            "chat_model": config.chat_model,
            "embedding_model": config.embedding_model,
            "limits": {
                "upload_mb": config.max_upload_bytes // (1024 * 1024),
                "documents": config.max_documents,
            },
        }

    @application.get("/api/documents", tags=["documents"])
    def documents():
        return {"documents": store.documents(config.embedding_model)}

    @application.post("/api/documents", tags=["documents"], status_code=201)
    async def upload(file: UploadFile):
        try:
            data = await file.read(config.max_upload_bytes + 1)
            return await run_in_threadpool(research.upload, file.filename or "", data)
        finally:
            await file.close()

    @application.delete("/api/documents/{document_id}", tags=["documents"])
    def delete(document_id: str):
        store.remove(document_id)
        return {"deleted": document_id}

    @application.get("/api/documents/{document_id}/download", tags=["documents"])
    def download(document_id: str):
        doc = store.document(document_id)
        path = config.upload_dir / f"{doc['id']}.{doc['kind']}"
        if not path.is_file():
            raise AppError(
                "file_missing", "The original document file is missing from local storage.", 404
            )
        return FileResponse(path, filename=doc["filename"], media_type="application/octet-stream")

    @application.get("/api/chunks/{chunk_id}", tags=["research"])
    def chunk(chunk_id: int):
        return store.get_source(chunk_id)

    @application.post("/api/search", tags=["research"])
    def search(request: SearchRequest):
        return research.search(request)

    @application.post("/api/ask", tags=["research"])
    def ask(request: AskRequest):
        return research.ask(request)

    @application.post("/api/index", tags=["research"])
    def index(request: IndexRequest):
        return research.index(request)

    @application.post("/api/index-jobs", tags=["research"], status_code=202)
    def start_index(request: IndexRequest):
        return jobs.start(request)

    @application.get("/api/index-jobs/{job_id}", tags=["research"])
    def index_status(job_id: str):
        return jobs.get(job_id)

    @application.post("/api/index-jobs/{job_id}/cancel", tags=["research"])
    def cancel_index(job_id: str):
        return jobs.cancel(job_id)

    @application.get("/api/history", tags=["history"])
    def history():
        return {"runs": store.recent_runs()}

    @application.get("/api/history/{run_id}", tags=["history"])
    def saved_run(run_id: str):
        return store.get_run(run_id)

    @application.delete("/api/history/{run_id}", tags=["history"])
    def delete_run(run_id: str):
        store.delete_run(run_id)
        return {"deleted": run_id}

    @application.delete("/api/history", tags=["history"])
    def clear_history():
        store.clear_history()
        return {"cleared": True}

    @application.post("/api/demo", tags=["documents"])
    def demo():
        loaded = [
            research.upload(path.name, path.read_bytes())
            for path in sorted((ROOT / "examples").glob("*.md"))
        ]
        return {
            "loaded": sum(not item["duplicate"] for item in loaded),
            "duplicates": sum(item["duplicate"] for item in loaded),
        }

    application.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
    return application


app = create_app()
