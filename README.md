# BraveTrace

Search PDFs, Markdown, and text files, then open the exact passages behind each
result. BraveTrace runs locally with a Python backend and SQLite. Ollama adds
optional AI answers and semantic search.

The idea is simple: finding an answer should also make it easy to check where it
came from. Search works without installing a model.

## Features

- PDF, TXT, and Markdown uploads, with duplicate detection.
- Keyword search using SQLite FTS5 and BM25, plus optional hybrid retrieval.
- Page or line references and a source inspector that highlights the full passage.
- Local AI answers with validated source IDs and an explicit no-evidence response.
- Saved research history and Markdown exports containing the source text.
- Background embedding jobs with progress, cancellation, and resume.

## Run locally

Use Python 3.15. Python 3.12 is also covered by CI. The Python installation needs
SQLite FTS5 support; `scripts/doctor.py` checks this.

Clone the repository, or download its ZIP from GitHub. Open the folder containing
`backend/` and `requirements.txt` in VS Code.

```powershell
git clone https://github.com/SamYKarabacak/BraveTrace.git BraveTrace
cd BraveTrace
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-dev.txt -c requirements-lock.txt
.\.venv\Scripts\python.exe scripts\doctor.py
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open [localhost:8000](http://127.0.0.1:8000). Select `.venv\Scripts\python.exe` as
the VS Code interpreter. These commands use the environment directly, so
PowerShell activation is optional. Stop the server with Ctrl+C.

On Linux or macOS:

```bash
python3.15 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements-dev.txt -c requirements-lock.txt
.venv/bin/python scripts/doctor.py
.venv/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Click **Try sample documents** to load three fictional notes. Try asking how long
audit logs are retained, who approves security exceptions, or what the pilot
timeline is. One note contains an unapproved policy proposal, so check the
sources rather than relying on a matching phrase alone.

## Local AI

Install [Ollama](https://ollama.com/download) and pull the default models:

```text
ollama pull qwen3:4b
ollama pull embeddinggemma
```

Keep Ollama running, open the sidebar setup guide, and refresh AI status. Select
**AI answer** for generation. To use **Hybrid search**, build the semantic index
first. Keyword search remains available while indexing runs.

Stopping an index build lets the current model batch finish and saves its vectors.
Starting another build processes the remaining passages. A server restart loses
the job record, but keeps completed embeddings.

Local inference has no model API fee. Downloads use disk space, and inference
speed depends on your hardware. The chat model needs to support structured JSON
output and `think: false`. Live model quality still needs evaluation; the tests
use simulated responses.

## Configuration

Set these environment variables before starting the server. An `.env` file is
not loaded automatically.

| Variable | Default | Purpose |
|---|---|---|
| `BRAVETRACE_DATA_DIR` | `data/` in the project | Database and uploaded files |
| `BRAVETRACE_CHAT_MODEL` | `qwen3:4b` | Ollama answer model |
| `BRAVETRACE_EMBEDDING_MODEL` | `embeddinggemma` | Ollama embedding model |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama HTTP service |

Existing `EVIDENCELENS_*` settings are still accepted. If both prefixes are set,
`BRAVETRACE_*` takes precedence. The database filename remains
`evidencelens.sqlite3`, so the rename preserves existing documents and history.

For example, in PowerShell:

```powershell
$env:BRAVETRACE_CHAT_MODEL = "qwen3:0.6b"
```

If you replace an embedding model under the same name, force an index rebuild.
See the API docs at `/api/docs` and the [indexing notes](docs/ARCHITECTURE.md).

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts\evaluate.py
```

The suite has 94 tests. GitHub Actions runs the Python checks on Windows and Linux
with Python 3.12 and 3.15. The retrieval script checks 18 questions against the
three sample documents. See [test results](docs/VERIFICATION.md) for the scope of
those checks.

Optional browser tests and formatting use Node.js:

```text
npm ci
npx playwright install chromium
npm run test:browser
npm run check:frontend
```

The browser test starts a temporary app server and a simulated Ollama service.
It uses its own database. `npm run format:frontend` formats the browser files.
Node.js is only needed for these development tools.

## Design and limitations

FastAPI serves both the JSON API and the browser assets. SQLite stores the
original extracted text, passage offsets, FTS index, history, and embedding cache.
Ollama is called over HTTP, keeping the Python environment small.

This is a single-user local application. Documents and saved answers are stored
unencrypted in `data/`, which is ignored by Git. Limits are 10 MB per file,
300 PDF pages, one million extracted characters, and 50 documents.

Scanned PDFs need OCR. Tables and complex PDF layouts may extract poorly.
Keyword relevance checks can miss useful partial matches, and semantic search
can return weak matches. Citation validation checks references; it cannot prove
that a model interpreted the passage correctly.

The application runs on your computer. A hosted web demo needs a separate Python
server, access controls, upload limits, and an inference setup.

More detail:

- [Design notes and API routes](docs/ARCHITECTURE.md)
- [Development walkthrough](docs/BUILD_FROM_SCRATCH.md)
- [Test results and evaluation](docs/VERIFICATION.md)
- [Changelog](CHANGELOG.md)

Upgrading from v0.1? Stop the server and back up the entire data directory first.
The [migration notes](docs/ARCHITECTURE.md) explain what the upgrade preserves.

## Troubleshooting

| Problem | Check |
|---|---|
| Python 3.15 is missing | Run `py --list` and check your Python installation. |
| `No module named backend` | Start the terminal in the project root. |
| Missing packages or FTS5 | Use the project interpreter and run `scripts/doctor.py`. |
| Port 8000 is in use | Stop the other server or choose `--port 8001`. |
| Hybrid search needs indexing | Build the index after adding documents. |
| AI is unavailable or slow | Check Ollama and the model download; try a smaller model. |

## License

[MIT](LICENSE). Check the licenses of any models you download separately.
