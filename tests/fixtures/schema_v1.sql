
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
    content, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
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
PRAGMA user_version=1;
