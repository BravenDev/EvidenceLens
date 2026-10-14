"""Rank passages using keywords, or fuse keyword and semantic rankings."""

import math
import re
import threading
from collections.abc import Callable

from backend.errors import AppError
from backend.ollama import Ollama
from backend.storage import Store

STOP_WORDS = set(
    "a an the is are was were be been being to of for in on at from and or with what which who how when where why do does did can could would should tell me about please document documents according all it its this that these those i we you they as by have has had will my our their long many much find show give explain describe summarize".split()
)

# Small, explicit query expansions. Hybrid search handles broader semantic similarity.
ALIASES = {
    "timeline": ["timeline", "schedule", "duration", "week"],
    "stored": ["stored", "storage"],
    "storage": ["storage", "stored"],
}


def query_terms(query: str) -> list[str]:
    words = re.findall(r"[^\W_]+", query.casefold(), flags=re.UNICODE)
    return list(
        dict.fromkeys(w for w in words if w not in STOP_WORDS and (len(w) > 1 or w.isdigit()))
    )[:16]


def diversify(items: list[dict], limit: int) -> list[dict]:
    """Overlapping passages from the same page should not crowd out other evidence."""
    chosen = []
    for item in items:
        redundant = False
        for old in chosen:
            if item["document_id"] == old["document_id"] and item["page"] == old["page"]:
                overlap = max(0, min(item["end"], old["end"]) - max(item["start"], old["start"]))
                smaller = min(item["end"] - item["start"], old["end"] - old["start"])
                if overlap / max(1, smaller) > 0.5:
                    redundant = True
                    break
        if not redundant:
            chosen.append(item)
        if len(chosen) == limit:
            break
    return chosen


class Retriever:
    def __init__(self, store: Store, ai: Ollama) -> None:
        self.store = store
        self.ai = ai

    def search(
        self, query: str, limit: int = 6, mode: str = "keyword", document_id: str | None = None
    ) -> list[dict]:
        if document_id:
            self.store.document(document_id)
        terms = query_terms(query)
        # All FTS syntax is generated from tokens, never passed through from user input.
        groups = [
            "(" + " OR ".join(f'"{word}"' for word in ALIASES.get(term, [term])) + ")"
            for term in terms
        ]
        match = " OR ".join(groups)
        lexical = self.store.keyword_search(match, limit * 6, document_id) if match else []
        matches = self.store.matching_terms(groups, [item["chunk_id"] for item in lexical])
        required = 1 if len(terms) == 1 else max(2, math.ceil(len(terms) / 2))
        number_indexes = {i for i, term in enumerate(terms) if term.isdigit()}
        lexical = [
            item
            for item in lexical
            if len(matches[item["chunk_id"]]) >= required
            and number_indexes.issubset(matches[item["chunk_id"]])
        ]
        # Coverage first; stable BM25 ordering breaks ties. Coverage is not confidence.
        lexical.sort(key=lambda item: -len(matches[item["chunk_id"]]))
        if mode == "keyword":
            return diversify(lexical, limit)
        if self.store.missing_embeddings(self.ai.settings.embedding_model, document_id):
            raise AppError(
                "index_required", "Build the semantic index before using Hybrid search.", 409
            )
        cached = self.store.vectors(self.ai.settings.embedding_model, document_id)
        if not cached:
            return []
        query_vector = self.ai.embed([query])[0]
        scored = []
        numbers = {term for term in terms if term.isdigit()}
        for item, vector in cached:
            if numbers and not numbers.issubset(set(re.findall(r"\d+", item["text"]))):
                continue
            if len(vector) != len(query_vector):
                raise AppError(
                    "embedding_mismatch",
                    "The embedding model changed. Rebuild the semantic index.",
                    409,
                )
            score = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
            if score > 0:
                scored.append((score, item))
        semantic = [
            item
            for _, item in sorted(scored, key=lambda pair: (-pair[0], pair[1]["chunk_id"]))[
                : limit * 4
            ]
        ]
        fused: dict[int, tuple[float, dict]] = {}
        for ranking in (lexical, semantic):
            for rank, item in enumerate(ranking, start=1):
                cid = item["chunk_id"]
                previous, original = fused.get(cid, (0.0, item))
                fused[cid] = (previous + 1 / (60 + rank), original)
        ordered = [
            item
            for _, item in sorted(fused.values(), key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
        ]
        return diversify(ordered, limit)

    def index(
        self,
        document_id: str | None = None,
        force: bool = False,
        progress: Callable[[int, int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        if document_id:
            self.store.document(document_id)
        model = self.ai.settings.embedding_model
        if cancel and cancel.is_set():
            return {
                "indexed": 0,
                "model": model,
                "remaining": len(self.store.missing_embeddings(model, document_id)),
                "cancelled": True,
            }
        if force:
            self.store.clear_embeddings(model, document_id)
        pending = self.store.missing_embeddings(model, document_id)
        saved = 0
        processed = 0
        if progress:
            progress(len(pending), processed, saved)
        for start in range(0, len(pending), 8):
            if cancel and cancel.is_set():
                break
            batch = pending[start : start + 8]
            vectors = self.ai.embed([item["text"] for item in batch])
            saved += self.store.save_embeddings(
                model,
                [(item["chunk_id"], vector) for item, vector in zip(batch, vectors, strict=True)],
            )
            processed += len(batch)
            if progress:
                progress(len(pending), processed, saved)
        return {
            "indexed": saved,
            "model": model,
            "remaining": len(self.store.missing_embeddings(model, document_id)),
            "cancelled": bool(cancel and cancel.is_set()),
        }
