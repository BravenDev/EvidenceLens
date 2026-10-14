# EvidenceLens

**A local document research assistant with inspectable evidence and optional AI answers.**

Upload PDFs, Markdown, or plain text; retrieve relevant passages; and inspect their
source context. Connect Ollama for local answer generation and semantic retrieval.
The default Evidence mode works without a model, account, or API key.

## What works

- Responsive browser interface with drag-and-drop uploads, document selection,
  library filtering, search, and a highlighted source inspector.
- Persistent SQLite storage and FTS5/BM25 keyword retrieval.
- English word-variant matching, query-coverage checks, and numeric constraints
  that reduce incidental matches. Paragraph-aware passages retain exact locations.
- Text-based PDF extraction with page references; TXT/MD references use line ranges.
- Content hashing prevents duplicate uploads from creating duplicate records.
- Optional local AI answers using retrieved context and clickable passage references.
- Optional semantic retrieval with cached embeddings and reciprocal-rank fusion.
- Background indexing with progress, cooperative cancellation, and cached resume.
- Saved research history, offline reopening, and Markdown exports with complete
  source passages. The latest 100 successful searches/questions are kept locally.
- A compact results layout and a collapsible document library on mobile.
- Structured errors, request size limits, same-origin mutation checks, and deletion
  of a document's stored file, passages, embeddings, and related saved results.
- Automated tests, a reproducible retrieval baseline, and a prepared CI workflow.

**Evidence mode shows retrieved text. It does not generate an AI answer.** AI mode
requires a working local Ollama installation and a downloaded chat model.

Version **0.2.0** improves the first release. See [changes](CHANGELOG.md) and
[verification](docs/VERIFICATION.md) for the measured results and remaining limits.

## Windows + VS Code quick start

1. Clone this repository or download and extract its ZIP. Open the project folder
   in VS Code: the folder containing `README.md`, `requirements.txt`, `backend`,
   and `frontend`. The separately supplied project archive has an inner
   `EvidenceLens` folder.
2. Open a PowerShell terminal in that folder.
3. Run the following commands individually:

If you have Git installed, these commands download the source first:

```powershell
git clone https://github.com/BravenDev/EvidenceLens.git
cd EvidenceLens
code .
```

Then create the environment and start the application:

```powershell
py -3.15 --version
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-dev.txt -c requirements-lock.txt
.\.venv\Scripts\python.exe scripts\doctor.py
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Keep the terminal running. Stop with **Ctrl+C**.

Select `.venv\Scripts\python.exe` through **Ctrl+Shift+P → Python: Select Interpreter**.
The explicit interpreter commands work without activating the environment or
changing PowerShell's execution policy. Project packages go into `.venv`;
pip may also retain downloaded packages in its normal cache.

Click **Try sample documents**, then ask:

- What is the pilot timeline?
- How long are audit logs retained?
- Who approves security exceptions?

Open a passage card to see its context. The three sample documents are fictional
and contain both an approved retention policy and an unapproved change proposal.
After a search, use **Export notes** to download the result and all retrieved
passages. **Recent research → View all** reopens saved results without another
model call. You can delete individual results or clear the history.

## Linux / macOS

```bash
python3.15 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements-dev.txt -c requirements-lock.txt
.venv/bin/python scripts/doctor.py
.venv/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Python 3.15 is the target. The implementation also supports Python 3.12+.
`requirements.txt` pins direct runtime dependencies; `requirements-dev.txt` adds
test/style tools; `requirements-lock.txt` constrains the verified transitive
versions. They contain no machine paths. Use an official Python build with SQLite
FTS5. See [verification](docs/VERIFICATION.md) for what was actually checked.

## Upgrading from v0.1

Stop the old server with Ctrl+C. Back up the entire `data/` folder before running
the new source; it contains your documents and database. Extract this release
into a separate folder, copy your previous `data/` folder into its `EvidenceLens`
folder, and follow the setup commands above to create its environment. If you
configured `EVIDENCELENS_DATA_DIR`, use that directory instead.

The first startup migrates the database to schema version 2 in a transaction.
It rebuilds the derived keyword index and adds history tables. Document IDs,
source IDs, extracted text, original files, and cached vectors are preserved.
The new 700-character passage target and 120-character overlap apply to new
uploads. Existing documents keep their original passages so references remain
valid. To use the new splitting for an old document, remove and re-upload it;
this creates new source IDs and removes its related history.

Keep the backup if you need to return to v0.1. Downgrading an already-migrated
database is not supported; restore the backup with the old application instead.

## Optional local AI

Install [Ollama](https://ollama.com/download), keep its local service running, and
download these models in a separate terminal:

```text
ollama pull qwen3:4b
ollama pull embeddinggemma
```

Refresh AI status using the `?` button in the sidebar. Select **AI answer** for
generated answers. Keyword retrieval can be used with AI immediately; embeddings
are only needed for **Hybrid search**. Click **Build semantic index**, then select
Hybrid search from the retrieval menu.

An index build runs in a background worker, so keyword research remains usable.
**Stop indexing** takes effect after the current model batch finishes and keeps
completed vectors. Start again to process the remaining passages. Reloading the
page reconnects to the running job. Restarting the Python server loses the job
record, but committed vectors remain and a new build resumes from them.

No model API fees or credentials are required for this local path. Model downloads
use disk space and internet bandwidth; inference uses your computer's memory and
CPU/GPU. Speed depends on the hardware. Check each model's license before
redistributing it. Model files and the Ollama application are not included here.

To select a smaller or different chat model, set an environment variable **before**
starting the Python server. The selected model must support Ollama structured
output and the `think: false` option; test it with the included questions.

```powershell
$env:EVIDENCELENS_CHAT_MODEL = "qwen3:0.6b"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Configuration also supports `EVIDENCELENS_DATA_DIR`, `EVIDENCELENS_EMBEDDING_MODEL`,
and `OLLAMA_BASE_URL`. The Ollama URL must be a local HTTP address. Environment
variables are read directly; an `.env` file is not loaded automatically.

If an embedding model is replaced under the same name, rebuild its index:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/index-jobs -Method Post -ContentType 'application/json' -Body '{"force":true}'
```

This returns a job ID; `GET /api/index-jobs/{id}` reports its progress. A force
rebuild clears the selected cache before computing replacement vectors.
Cached vectors are keyed by model name. Dimension changes are detected, but model
changes that preserve the same dimensions cannot be inferred automatically.

## Test and evaluate

In a second terminal, while in the project folder:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts\evaluate.py
```

Tests use isolated temporary storage and simulated AI responses. They do not
download models, touch your document library, or spend API credits. The evaluation
checks keyword retrieval against 18 questions and three fictional documents. It
is a smoke baseline, not a general benchmark of retrieval or answer quality.

The CI workflow runs these checks on Linux and Windows with Python 3.12 and 3.15.
Check the repository's Actions tab for the latest run; the verification report
separately records the checks executed locally before source publication.

Optional browser checks use Node.js and Playwright. These tools are only needed
for browser testing or frontend formatting, not for running the application.
After installing Node.js:

```powershell
npm ci
npx playwright install chromium
npm run test:browser
npm run check:frontend
```

The script starts its own server and temporary document library, exercises the
interface, and cleans up. It does not use your normal `data/` folder. It finds the
project `.venv` interpreter by default; set `EVIDENCELENS_PYTHON` if needed.
The browser test runs a simulated Ollama HTTP service. It verifies indexing,
hybrid integration, citation repair, and saved-answer controls without a real
model. To format edited browser files, run `npm run format:frontend`.

## How it works

1. An upload is validated and extracted into page-level text.
2. Text is split into overlapping passages, preferring paragraph boundaries,
   while retaining exact offsets and keeping PDF pages separate.
3. SQLite stores metadata, original page text, and passages. Triggers maintain FTS5.
4. Keyword retrieval checks query-concept coverage, prioritizes coverage, then
   BM25. Hybrid retrieval adds embedding similarity and fuses both rankings.
5. In AI mode, only the retrieved passages and the question are supplied to the model.
6. The backend validates the response format, passage IDs, and agreement between
   inline references and declared citations. One format-repair attempt is allowed;
   a still-invalid response withholds the answer.
7. The browser resolves source IDs into stored source text and highlights the passage.
8. Successful results are saved with their document links; removing a document
   removes saved results that contain its text or selected it as their scope.

Read [architecture](docs/ARCHITECTURE.md) and the
[build-from-scratch walkthrough](docs/BUILD_FROM_SCRATCH.md) to learn the components.
The initial implementation was produced with AI-assisted coding and includes
explanations and exercises for rebuilding it independently.

## Limits and intended use

This version is a **single-user local portfolio application**. It binds to loopback
and allows local hostnames. A public deployment needs a separate configuration,
authentication or a read-only demo mode, traffic limits, durable storage, and a
decision about where AI inference runs. Free static website hosting alone cannot
run this Python backend or an Ollama model.

- Documents are limited to 10 MB, 300 PDF pages, one million extracted characters,
  and 50 stored documents. These are pilot limits, not a scaling claim.
- Image-only/scanned PDFs need OCR. Mixed PDFs warn about pages with no readable text.
- PDF layouts and tables may extract imperfectly. Limits do not fully isolate
  hostile PDF parsing; process isolation is a later public-hosting improvement.
- Semantic ranking scores every cached vector in Python. This suits small datasets;
  a larger application should use a vector index after measuring its workload.
- Keyword matching uses English stemming, a small explicit alias map, and a
  coverage heuristic. It can miss synonyms or partial matches; its checks are
  not a general measure of relevance. Hybrid retrieval can return weak matches.
- Citation identity validation does **not** prove claim support or eliminate
  hallucinations. Review source passages. The prompt mitigates document instructions
  but is not a complete defense against prompt injection.
- Local AI responses are non-streaming. A model request times out after 90 seconds;
  hybrid retrieval and a format-repair retry can add more waiting.
- Document text and saved questions/answers are stored unencrypted in `data/`.
  Exported notes also contain source text. This folder and `.venv` are ignored
  by Git and excluded from the supplied source archive.

## Troubleshooting

| Symptom | Action |
|---|---|
| `py -3.15` cannot be found | Install/repair Python 3.15 and reopen the terminal; check `py --list`. |
| `No module named backend` | Open the terminal in the folder containing `backend` and `requirements.txt`. |
| Missing dependency | Run the installation command using `.venv\Scripts\python.exe`. |
| Port 8000 is in use | Stop the existing server or use `--port 8001`, then open that port. |
| SQLite FTS5 error | Use an official Python distribution; run `scripts/doctor.py`. |
| `No readable text` | Upload a text-based PDF or run OCR on the document first. |
| AI is unavailable | Start Ollama, install the configured model, and refresh AI status. |
| Model timeout | Use fewer source passages through the API or a smaller chat model. |
| Hybrid says indexing is required | Build the semantic index after adding documents. |
| Indexing appears slow to stop | The current model batch must finish or time out; completed batches are kept. |
| Index job is unavailable after restart | Start a new build; previously saved vectors are reused. |
| Invalid model citations | One repair attempt failed; retry, change model, or inspect Evidence mode results. |

## Repository and development

This repository contains documentation, tests, a license, ignored local data,
and a CI workflow. Start with the build-from-scratch guide, implement a component
in your own words, and add tests for its behavior. Record your implementation
decisions and describe your contribution honestly.

Source publication does not host the running application. The Python backend
and optional Ollama service run on your computer using the commands above. A
public web demo requires a separate Python hosting setup and the safeguards
described under Limits and intended use.

## References

- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [SQLite FTS5 and BM25](https://www.sqlite.org/fts5.html)
- [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [Ollama embeddings](https://docs.ollama.com/api/embed)
- [Ollama chat](https://docs.ollama.com/api/chat)
- [Qwen3 4B model](https://ollama.com/library/qwen3:4b)
- [EmbeddingGemma model](https://ollama.com/library/embeddinggemma)

## License

MIT; see [LICENSE](LICENSE). Dependencies and AI models retain their own licenses.
