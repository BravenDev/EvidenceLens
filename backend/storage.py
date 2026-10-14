"""SQLite owns metadata, source text, the search index, and embedding caches."""

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings
from backend.errors import AppError
from backend.extraction import PageText, Passage

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, page_number INTEGER, content TEXT NOT NULL,
    PRIMARY KEY (document_id, ordinal)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, page INTEGER, start INTEGER NOT NULL, end INTEGER NOT NULL,
    line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, content='chunks', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL, dimension INTEGER NOT NULL, vector TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model)
);
CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, query TEXT NOT NULL,
    action TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_documents (
    run_id TEXT NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, document_id)
);
PRAGMA user_version=2;
"""

SOURCE_SELECT = """SELECT c.*, d.filename FROM chunks c
JOIN documents d ON d.id=c.document_id"""


def source(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    page = data["page"]
    location = (
        f"Page {page}" if page is not None else f"Lines {data['line_start']}-{data['line_end']}"
    )
    result = {
        "chunk_id": data["id"],
        "label": f"S{data['id']}",
        "document_id": data["document_id"],
        "filename": data["filename"],
        "page": page,
        "line_start": data["line_start"],
        "line_end": data["line_end"],
        "start": data["start"],
        "end": data["end"],
        "location": location,
        "text": data["content"],
        "url": f"/api/chunks/{data['id']}",
    }
    if "excerpt" in data:
        result["excerpt"] = data["excerpt"][:360]
    return result


class Store:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.settings.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, 2}:
                raise RuntimeError("This database was created by a newer EvidenceLens version.")
            try:
                # Rebuild only the derived FTS index. Source IDs and vectors stay valid.
                migration = ""
                if version == 1:
                    migration = """
                        DROP TRIGGER IF EXISTS chunks_insert;
                        DROP TRIGGER IF EXISTS chunks_delete;
                        DROP TABLE IF EXISTS chunks_fts;
                    """
                rebuild = (
                    "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');" if version == 1 else ""
                )
                conn.executescript("BEGIN IMMEDIATE;" + migration + SCHEMA + rebuild + "COMMIT;")
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "SQLite FTS5 is required. Use an official Python installation."
                ) from exc

    def documents(self, model: str) -> list[dict]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT d.*, (SELECT COUNT(*) FROM embeddings e JOIN chunks c ON c.id=e.chunk_id
                    WHERE c.document_id=d.id AND e.model=?) AS embedded_chunks
                FROM documents d ORDER BY created_at DESC, id
            """,
                (model,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self, model: str) -> dict:
        docs = self.documents(model)
        with closing(self.connect()) as conn:
            runs = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
        return {
            "documents": len(docs),
            "chunks": sum(d["chunk_count"] for d in docs),
            "embedded_chunks": sum(d["embedded_chunks"] for d in docs),
            "bytes": sum(d["size_bytes"] for d in docs),
            "research_runs": runs,
        }

    def document(self, document_id: str) -> dict:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "This document no longer exists.", 404)
        return dict(row)

    def add(
        self, filename: str, content: bytes, pages: list[PageText], passages: list[Passage]
    ) -> tuple[dict, bool]:
        digest = hashlib.sha256(content).hexdigest()
        with self.lock, closing(self.connect()) as conn:
            existing = conn.execute("SELECT * FROM documents WHERE sha256=?", (digest,)).fetchone()
            if existing:
                return dict(existing), True
            count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if count >= self.settings.max_documents:
                raise AppError(
                    "library_full",
                    f"The local library supports {self.settings.max_documents} documents.",
                    409,
                )
            doc_id = uuid.uuid4().hex
            kind = Path(filename).suffix.lower()[1:]
            record = {
                "id": doc_id,
                "filename": filename,
                "sha256": digest,
                "kind": kind,
                "size_bytes": len(content),
                "page_count": len(pages) if kind == "pdf" else 0,
                "chunk_count": len(passages),
                "created_at": datetime.now(UTC).isoformat(),
            }
            path = self.settings.upload_dir / f"{doc_id}.{kind}"
            path.write_bytes(content)
            try:
                with conn:
                    conn.execute(
                        """INSERT INTO documents
                        (id,filename,sha256,kind,size_bytes,page_count,chunk_count,created_at)
                        VALUES (:id,:filename,:sha256,:kind,:size_bytes,:page_count,:chunk_count,:created_at)
                    """,
                        record,
                    )
                    conn.executemany(
                        "INSERT INTO pages VALUES (?,?,?,?)",
                        [(doc_id, i, p.page, p.text) for i, p in enumerate(pages)],
                    )
                    conn.executemany(
                        """INSERT INTO chunks
                        (document_id,ordinal,page,start,end,line_start,line_end,content)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        [
                            (doc_id, i, p.page, p.start, p.end, p.line_start, p.line_end, p.text)
                            for i, p in enumerate(passages)
                        ],
                    )
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return record, False

    def remove(self, document_id: str) -> None:
        with self.lock:
            record = self.document(document_id)
            with closing(self.connect()) as conn, conn:
                conn.execute(
                    "DELETE FROM research_runs WHERE id IN (SELECT run_id FROM run_documents WHERE document_id=?)",
                    (document_id,),
                )
                conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
            (self.settings.upload_dir / f"{record['id']}.{record['kind']}").unlink(missing_ok=True)

    def get_source(self, chunk_id: int) -> dict:
        with closing(self.connect()) as conn:
            row = conn.execute(SOURCE_SELECT + " WHERE c.id=?", (chunk_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "This source passage no longer exists.", 404)
            page_row = conn.execute(
                """SELECT content FROM pages
                WHERE document_id=? AND page_number IS ? LIMIT 1""",
                (row["document_id"], row["page"]),
            ).fetchone()
        result = source(row)
        result["page_text"] = page_row[0]
        return result

    def keyword_search(self, match: str, limit: int, document_id: str | None = None) -> list[dict]:
        sql = """SELECT c.*, d.filename, bm25(chunks_fts) AS rank,
            snippet(chunks_fts, 0, '', '', ' … ', 44) AS excerpt
            FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
            JOIN documents d ON d.id=c.document_id WHERE chunks_fts MATCH ?"""
        params: list = [match]
        if document_id:
            sql += " AND c.document_id=?"
            params.append(document_id)
        sql += " ORDER BY rank, c.id LIMIT ?"
        params.append(limit)
        with closing(self.connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [source(row) for row in rows]

    def missing_embeddings(self, model: str, document_id: str | None = None) -> list[dict]:
        sql = SOURCE_SELECT + " WHERE c.id NOT IN (SELECT chunk_id FROM embeddings WHERE model=?)"
        params: list = [model]
        if document_id:
            sql += " AND c.document_id=?"
            params.append(document_id)
        with closing(self.connect()) as conn:
            rows = conn.execute(sql + " ORDER BY c.id", params).fetchall()
        return [source(row) for row in rows]

    def matching_terms(self, groups: list[str], chunk_ids: list[int]) -> dict[int, set[int]]:
        """Count matching query concepts using the FTS tokenizer."""
        matches = {cid: set() for cid in chunk_ids}
        if not chunk_ids:
            return matches
        placeholders = ",".join("?" for _ in chunk_ids)
        with closing(self.connect()) as conn:
            for index, group in enumerate(groups):
                rows = conn.execute(
                    f"SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({placeholders})",
                    [group, *chunk_ids],
                ).fetchall()
                for row in rows:
                    matches[row[0]].add(index)
        return matches

    def save_run(self, result: dict, action: str, document_id: str | None) -> dict:
        """History stores a snapshot; document deletion removes related snapshots."""
        run_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        snapshot = {
            **result,
            "run_id": run_id,
            "created_at": created_at,
            "action": action,
            "document_id": document_id,
        }
        doc_ids = {item["document_id"] for item in result["sources"]}
        if document_id:
            doc_ids.add(document_id)
        source_ids = {item["chunk_id"] for item in result["sources"]}
        with self.lock, closing(self.connect()) as conn, conn:
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                found = conn.execute(
                    f"SELECT COUNT(*) FROM chunks WHERE id IN ({placeholders})", list(source_ids)
                ).fetchone()[0]
                if found != len(source_ids):
                    raise AppError(
                        "source_changed",
                        "A source was removed while this result was being prepared. Run the question again.",
                        409,
                    )
            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                found = conn.execute(
                    f"SELECT COUNT(*) FROM documents WHERE id IN ({placeholders})", list(doc_ids)
                ).fetchone()[0]
                if found != len(doc_ids):
                    raise AppError(
                        "source_changed",
                        "The selected document was removed. Run the question again.",
                        409,
                    )
            conn.execute(
                "INSERT INTO research_runs VALUES (?,?,?,?,?)",
                (
                    run_id,
                    created_at,
                    result["query"],
                    action,
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            conn.executemany(
                "INSERT INTO run_documents VALUES (?,?)", [(run_id, did) for did in doc_ids]
            )
            conn.execute(
                "DELETE FROM research_runs WHERE id NOT IN (SELECT id FROM research_runs ORDER BY created_at DESC, id DESC LIMIT ?)",
                (self.settings.max_history_entries,),
            )
        return snapshot

    def recent_runs(self) -> list[dict]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT id, created_at, query, action FROM research_runs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT result_json FROM research_runs WHERE id=?", (run_id,)
            ).fetchone()
        if not row:
            raise AppError("not_found", "This saved research result no longer exists.", 404)
        return json.loads(row[0])

    def delete_run(self, run_id: str) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            if not conn.execute("DELETE FROM research_runs WHERE id=?", (run_id,)).rowcount:
                raise AppError("not_found", "This saved research result no longer exists.", 404)

    def clear_history(self) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM research_runs")

    def save_embeddings(self, model: str, items: list[tuple[int, list[float]]]) -> int:
        saved = 0
        with self.lock, closing(self.connect()) as conn, conn:
            for chunk_id, vector in items:
                # A document can be deleted while the model is computing embeddings.
                if conn.execute("SELECT 1 FROM chunks WHERE id=?", (chunk_id,)).fetchone():
                    conn.execute(
                        "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                        (chunk_id, model, len(vector), json.dumps(vector)),
                    )
                    saved += 1
        return saved

    def clear_embeddings(self, model: str, document_id: str | None = None) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            if document_id:
                conn.execute(
                    "DELETE FROM embeddings WHERE model=? AND chunk_id IN (SELECT id FROM chunks WHERE document_id=?)",
                    (model, document_id),
                )
            else:
                conn.execute("DELETE FROM embeddings WHERE model=?", (model,))

    def vectors(self, model: str, document_id: str | None = None) -> list[tuple[dict, list[float]]]:
        sql = """SELECT c.*, d.filename, e.vector FROM embeddings e
            JOIN chunks c ON c.id=e.chunk_id JOIN documents d ON d.id=c.document_id
            WHERE e.model=?"""
        params: list = [model]
        if document_id:
            sql += " AND c.document_id=?"
            params.append(document_id)
        with closing(self.connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(source(row), json.loads(row["vector"])) for row in rows]
