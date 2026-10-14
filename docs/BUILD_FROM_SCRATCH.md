# Development walkthrough

This guide follows the application from a small server to document search and
AI answers. Use it to trace the code or rebuild each part in a separate folder.
The first five stages work without Ollama.

## First run, then inspect

Follow the Windows commands in the README. Load the sample collection, search
for `audit logs`, and open a source. Keep the browser developer tools and VS Code
terminal open. Observe the request in the Network panel. This gives a concrete
behavior to understand before reading all the code.

Run `python -m pytest -q` using your project interpreter. Then open
`tests/test_documents.py` and find the upload/search/source test. It demonstrates
one end-to-end use case without requiring a browser or model.

## Stage 1 — A running server

Create an empty folder and a virtual environment. Install FastAPI and Uvicorn.
Create `backend/__init__.py` and a minimal `backend/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="EvidenceLens")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Run `python -m uvicorn backend.main:app --reload` with your environment's Python.
Open `/api/health`. `backend.main:app` is a module path and an object name; the
server imports that object. The decorator registers the URL and HTTP method.
The return annotation describes the function's output, and FastAPI serializes
the dictionary as JSON.

Checkpoint: you can change the message, save, and observe the response update.

## Stage 2 — A browser page

Create `frontend/index.html` with a title, an input, and a button. Serve it using
`FileResponse`. Mount `StaticFiles` at `/assets` for CSS and JavaScript. Use
JavaScript `fetch('/api/health')` to show the server status in the page.

In the browser, `api()` wraps fetch and converts structured server
errors into readable messages. `element()` creates DOM nodes and assigns their
`textContent`. This is essential when displaying untrusted document text.
`busy()` prevents overlapping user actions; `notice()` reports status and errors.

Checkpoint: inspect the Network panel and identify a browser request, its status,
and its response body. Temporarily stop the server and observe the failure path.

## Stage 3 — Text uploads and passage splitting

Start with `.txt` only. An upload arrives as `UploadFile`; read a bounded number
of bytes, decode UTF-8, and reject empty or binary text. Build this as a pure
function before adding a database.

The extraction module separates this logic:

| Function | What it does | Why it is separate |
|---|---|---|
| `safe_filename()` | Validates type and normalizes a display name | Upload names must not become disk paths |
| `extract_pages()` | Produces text units with optional PDF page numbers | Retrieval should not care which file format supplied the text |
| `split_passages()` | Creates overlapping passages and exact offsets | Source inspection depends on stable locations |

`PageText` and `Passage` are frozen dataclasses: simple records with named fields.
The splitter walks the text with a cursor, prefers a paragraph boundary, falls
back to whitespace, trims edge whitespace, and stores the adjusted offsets.
It then steps back by the
overlap size and adjusts to a word boundary before taking the next passage.
The defaults are 700 characters and 120 characters of overlap. PDF pages are
split independently.

Checkpoint: for each passage, assert
`page.text[passage.start:passage.end] == passage.text`. Verify that every
non-whitespace character is covered and no passage joins two PDF pages.

Exercise: change the passage size and overlap. Explain the effects on source
granularity, repeated context, storage size, and later model context length.

## Stage 4 — Persistence

Add SQLite tables for documents, text units, and passages. Use bound SQL
parameters. Enable foreign keys for every connection. Put SQL behind a `Store`
class so the upload function does not need to know the schema.

The main `Store` methods are:

- `initialize()` creates folders/tables, checks schema version, migrates v1, and
  verifies FTS5 support. Read the version-1 fixture in `tests/fixtures/` to compare
  the old schema and its upgrade test.
- `add()` checks the content hash, writes a generated file path, and inserts
  document/page/chunk records in a transaction.
- `documents()` returns display metadata and indexing counts.
- `get_source()` resolves a chunk ID and includes its original text unit.
- `remove()` cascades database deletion and removes the original file.

`with conn:` commits or rolls back a transaction; it does not close the connection.
`closing(...)` closes connections as well. A lock coordinates
mutations within this one application process.

Checkpoint: upload a document, stop the server, restart it, and find it again.
Upload identical bytes with a different filename and explain why only one record
remains. Delete it and confirm the source and search results disappear.

## Stage 5 — Keyword research and citations

Create an FTS5 table backed by the passage table. Insert/delete triggers keep it
in sync. Rank results with BM25 and return enough candidates to remove highly
overlapping passages. Preserve the source fields in the response.

`query_terms()` removes common English question words and produces safe tokens.
`Retriever.search()` creates the FTS query, applies an optional document filter,
checks concept coverage and numbers, and returns ranked source records. The FTS
Porter tokenizer matches English word variants. The small `ALIASES` dictionary
contains deliberate query expansions you can inspect and change.
`diversify()` keeps repeated overlap from dominating the result list.

In the browser, `runResearch()` reads the question and sends a POST request.
`renderResult()` builds the answer and evidence cards. `inspectSource()` fetches
the original text unit and wraps the exact passage in a `<mark>` element.

Checkpoint: a source reference resolves to persisted text and has the correct
page or line range. A question with no lexical matches gets an explicit no-evidence
response. Evidence mode should remain clearly labeled as retrieved text.

Exercise: add two notes with contradictory policies. Confirm both can be found.
Discuss why retrieving a passage containing a word is not the same as establishing
the correct answer to a question.

Exercise: compare `pilot timeline` and `pilot telescope`. Explain why the latter
should not return evidence just because both contain the word pilot. Then create
a useful partially matching question that the coverage rule rejects. Record the
precision/recall tradeoff before changing the rule.

## Stage 6 — AI answers

Install Ollama and a supported chat model. Keep the model behind an adapter rather
than placing HTTP calls inside each route. `Ollama.generate()` sends
a system prompt, a question, and source records to `/api/chat`. It requests a JSON
schema and disables streaming for a simple first implementation.

`CitedAnswer` is a Pydantic response model. `validate_answer()` checks its shape,
valid source IDs, and agreement between declared citations and inline `[S12]`
markers. `Research.ask()` coordinates retrieval and generation and maps validated
IDs into stored source records.

An invalid format or citation set gets one repair attempt through the adapter's
`repair` argument. This strengthens the formatting prompt; it does not prove
that a cited claim is true. Both attempts use `validate_answer()`.

Checkpoint: ask about an explicit fact, open its citation, then ask a question the
document cannot answer. Test conflicting passages and a document containing
instructions to ignore the system prompt. Human review is still necessary:
citation validation cannot prove every answer claim follows from the source.

The fake client in `tests/conftest.py` makes model-dependent tests deterministic.
The application uses the real HTTP adapter; model answer quality needs its own
evaluation.

## Stage 7 — Semantic and hybrid retrieval

An embedding represents text as a vector. `Ollama.embed()` asks a local embedding
model for vectors, validates them, and normalizes them. `Retriever.index()` caches
passage vectors in batches. The query must use the same embedding model.

`backend/jobs.py` adds `IndexJobs`: a one-thread executor runs the build outside
the HTTP request. A lock protects job records, an event requests cancellation,
and a callback reports progress. Each finished batch is committed before the
next batch starts. Browser `pollIndex()` updates a native progress element.

Checkpoint: start a build, perform a keyword search while it runs, stop the build,
and resume it. Explain why cancellation cannot instantly interrupt an in-flight
model request and why cached batches survive a server restart while job IDs do not.

With normalized vectors, a dot product measures cosine similarity.
`Retriever.search()` scores cached vectors, builds a semantic rank list, and uses
reciprocal-rank fusion to combine it with the keyword list. This combines rankings
without pretending that different scoring scales are interchangeable.

Checkpoint: use a synonym absent from the document. Compare keyword and hybrid
results. Change the model dimensions and verify that the application requests an
index rebuild. Explain why models with different meanings but the same dimensions
also need a rebuild, even though the application cannot detect that automatically.

## Stage 8 — Saved research and exports

`Store.save_run()` writes a snapshot of a successful search or question result
and its document links. `recent_runs()` returns lightweight list metadata;
`get_run()` returns the complete saved result. The history routes use these
methods. `openHistory()` restores the question, scope, mode, and result without
calling the model. `exportNotes()` creates a local Markdown download from that
snapshot.

Checkpoint: save an AI answer, stop Ollama, reload the browser, and reopen it.
Export it and check that its cited IDs, filenames, locations, and full passages
are present. Remove a source document and verify related saved results vanish.
Clear history and verify your documents remain.

Exercise: explain the purpose of the `run_documents` join table. Why would deleting
only the document row leave sensitive text in saved JSON? Read the test where a
source disappears during generation and explain the final transaction check.

## Stage 9 — Evaluation, polish, and GitHub

`scripts/evaluate.py` loads examples into temporary storage and checks 18 known
questions. It prints retrieval case results and mean reciprocal rank. The current
corpus is deliberately tiny. Expand it with many independent documents and record
answer quality separately from retrieval quality.

Tests cover document processing, persistence, query isolation, HTTP failures,
invalid vectors, malformed answers, citation identity, migration, history, and
background cancellation. The browser smoke script starts its own temporary
server and simulated Ollama HTTP service to check actual browser behavior. Its
fixed responses/vectors verify integration, not model intelligence.
Ruff checks Python style and formatting. Optional Prettier scripts format the
frontend and browser test. CI runs the Python checks on Windows and Linux.

For the next stage, choose a substantive improvement to implement yourself and
document your decisions. Examples:

- Add an OCR adapter and tests for scanned documents.
- Add a reviewed answer-quality dataset with conflicting and unanswerable cases.
- Compare several passage sizes against a larger independent retrieval dataset.
- Add search highlighting that marks individual matched concepts in previews.

Work through one component at a time and commit at each checkpoint. Trace its
inputs, outputs, failure paths, and tests before changing it.

Keep a short decision log describing the problem, your change, and its measured
result. Use fictional data for demonstrations.
