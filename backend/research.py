"""Coordinate ingestion, retrieval, and grounded answers without mixing in HTTP."""

import re
import threading
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from backend.config import Settings
from backend.errors import AppError
from backend.extraction import extract_pages, safe_filename, split_passages
from backend.ollama import Ollama
from backend.retrieval import Retriever
from backend.storage import Store


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=6, ge=1, le=8)
    retrieval: Literal["keyword", "hybrid"] = "keyword"
    document_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class AskRequest(SearchRequest):
    mode: Literal["evidence", "ai"] = "evidence"


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    force: bool = False


class CitedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=14000)
    citations: list[Annotated[StrictInt, Field(gt=0)]] = Field(max_length=8)
    abstained: StrictBool


def validate_answer(data: dict, sources: list[dict]) -> CitedAnswer:
    """Verify citation identity, not whether every claim is entailed by its source."""
    try:
        answer = CitedAnswer.model_validate(data)
    except ValidationError as exc:
        raise AppError(
            "invalid_model_response",
            "The model returned an invalid answer format. Try again or use Evidence mode.",
            502,
        ) from exc
    known = {s["chunk_id"] for s in sources}
    declared = set(answer.citations)
    inline = {int(cid) for cid in re.findall(r"\[S(\d+)\]", answer.answer)}
    invalid = (
        len(answer.citations) != len(declared)
        or declared != inline
        or not declared.issubset(known)
        or (answer.abstained and bool(declared))
        or (not answer.abstained and not declared)
        or not answer.answer.strip()
    )
    if invalid:
        raise AppError(
            "invalid_citations",
            "The model produced unverifiable source references. The answer was withheld; try again or use Evidence mode.",
            502,
        )
    return answer


class Research:
    def __init__(self, settings: Settings, store: Store, ai: Ollama) -> None:
        self.settings = settings
        self.store = store
        self.ai = ai
        self.retriever = Retriever(store, ai)
        self.index_lock = threading.Lock()
        self.ai_slots = threading.BoundedSemaphore(2)

    def upload(self, filename: str, content: bytes) -> dict:
        name = safe_filename(filename)
        pages = extract_pages(content, name, self.settings)
        passages = split_passages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
        if not passages:
            raise AppError("no_text", "No searchable text was found in this document.")
        document, duplicate = self.store.add(name, content, pages, passages)
        empty_pages = sum(not p.text.strip() for p in pages)
        warning = (
            f"{empty_pages} PDF page(s) had no extractable text and are not searchable."
            if empty_pages
            else None
        )
        return {"document": document, "duplicate": duplicate, "warning": warning}

    def search(self, request: SearchRequest) -> dict:
        started = time.perf_counter()
        results = self.retriever.search(
            request.query, request.limit, request.retrieval, request.document_id
        )
        result = {
            "query": request.query,
            "retrieval": request.retrieval,
            "sources": results,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        return self.store.save_run(result, "search", request.document_id)

    def ask(self, request: AskRequest) -> dict:
        started = time.perf_counter()
        sources = self.retriever.search(
            request.query, min(request.limit, 6), request.retrieval, request.document_id
        )
        result = {
            "query": request.query,
            "mode": request.mode,
            "retrieval": request.retrieval,
            "model": None,
            "sources": sources,
            "citations": [],
            "abstained": False,
            "repaired": False,
        }
        if not sources:
            result.update(
                answer="No matching evidence was found in the selected documents. Try different terms or upload a relevant document.",
                abstained=True,
            )
        elif request.mode == "evidence":
            result.update(
                answer="These passages match your question. Open a source to inspect its context.",
                citations=sources,
            )
        else:
            if not self.ai_slots.acquire(blocking=False):
                raise AppError("ai_busy", "The local model is busy. Try again shortly.", 429)
            try:
                schema = CitedAnswer.model_json_schema()
                try:
                    data = self.ai.generate(request.query, sources, schema)
                    answer = validate_answer(data, sources)
                except AppError as exc:
                    if exc.code not in {"invalid_model_response", "invalid_citations"}:
                        raise
                    data = self.ai.generate(request.query, sources, schema, repair=True)
                    answer = validate_answer(data, sources)
                    result["repaired"] = True
            finally:
                self.ai_slots.release()
            mapped = {s["chunk_id"]: s for s in sources}
            result.update(
                answer=answer.answer,
                abstained=answer.abstained,
                citations=[mapped[cid] for cid in answer.citations],
                model=self.settings.chat_model,
            )
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return self.store.save_run(result, "ask", request.document_id)

    def index(self, request: IndexRequest) -> dict:
        if not self.index_lock.acquire(blocking=False):
            raise AppError("index_busy", "The semantic index is already being built.", 409)
        try:
            return self.retriever.index(request.document_id, request.force)
        finally:
            self.index_lock.release()
