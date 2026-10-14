# Changelog

## 0.2.0

- Prefer paragraph boundaries when splitting new uploads; use 700-character
  passages with 120 characters of overlap.
- Add English stemming, query-coverage filtering, numeric constraints, and FTS
  snippets to keyword search.
- Save the latest 100 research results and export notes with full source passages.
- Build embeddings in a background job, with progress and cancellation after the
  current batch. Reuse saved vectors when resuming.
- Retry an invalid AI answer format or citation set once before withholding it.
- Add a mobile library toggle and a more compact results view.
- Migrate v0.1 databases without changing existing document IDs, source IDs, or
  embeddings.

The sample retrieval checks now pass 18/18 cases, up from 15/18. The three added
passes reject unrelated queries that share one word with a document. The sample
contains only three fictional documents; evaluation details are in
[docs/VERIFICATION.md](docs/VERIFICATION.md).

## 0.1.0

Initial version with PDF/TXT/Markdown uploads, SQLite search, source inspection,
Ollama integration, hybrid retrieval, tests, and a browser interface.
