# Testing and evaluation

## Checks for v0.2.0

| Check | Result |
|---|---|
| Python suite on Linux, Python 3.15.0 | 94 passed |
| Python suite on Linux, Python 3.12.14 | 94 passed |
| GitHub CI: Windows/Linux, Python 3.12/3.15 | The workflow checks all four combinations; see Actions for the latest result |
| Dependency consistency | `pip check` passed |
| Python lint and formatting | Ruff passed |
| JavaScript syntax and formatting | Syntax checks and Prettier passed |
| Browser workflow | Passed in Chromium 153 on Linux |
| Layout review | Desktop at 1440 px and mobile at 390 px |
| Sample keyword retrieval | 18/18 cases; MRR@5 of 1.000 for answerable queries |

The local Python 3.15 run used SQLite 3.53.1 with FTS5. Current CI results are in
the repository's Actions tab. The [first passing publication run](https://github.com/BravenDev/BraveTrace/actions/runs/38531081673)
ran the original 91-test suite on all four combinations.

## What the tests cover

The Python suite checks uploads, PDF extraction, duplicate detection, source
locations, persistence, deletion, database migration, retrieval, and model errors.
It also checks history limits, deletion during generation, invalid citations,
embedding-cache reuse, and index cancellation.

Configuration checks cover both environment-variable prefixes and their
precedence. They reopen an existing database and verify that uploads, saved
answers, source IDs, and cached embeddings remain available after the rename.

PDF fixtures include blank pages, page-number gaps, password protection, and
page/character limits. The schema-v1 fixture checks that upgrades preserve source
IDs, files, text, and vectors. Request-limit tests include streamed bodies without
a Content-Length header.

The browser smoke test exercises the UI against a temporary server:

- Load samples, upload documents, filter the library, and select a search scope.
- Search, inspect highlighted source text, and download the original file.
- Index in the background, search during a build, cancel, reload, and resume.
- Render AI citations, repair an invalid response, and reopen saved answers offline.
- Export notes, keep HTML/backticks as literal text, and clear related history.
- Complete the mobile workflow without horizontal overflow or JavaScript errors.

Model calls use a fake adapter, HTTP mock transport, or a simulated local HTTP
service. The browser fixture returns fixed vectors and answers. These checks
verify integration and error handling; live Ollama answer quality has not been
evaluated yet.

## Retrieval sample

`scripts/evaluate.py` loads the three example documents into temporary storage.
Both versions were run against the same 18 questions using their default keyword
pipelines, including their respective chunking settings.

| Measure | v0.1.0 | v0.2.0 |
|---|---|---|
| All cases passed | 15/18 | 18/18 |
| Answerable cases with the expected document in the top five | 13/13 | 13/13 |
| Unsupported cases returning no passage | 2/5 | 5/5 |
| Answerable-query MRR@5 | 1.000 | 1.000 |

The changed cases are `pilot telescope`, `budget volcano`, and `retention quasar`.
The old query returned passages on a single shared word; the coverage rule rejects
those matches. The cases were written alongside the implementation, so this is a
small regression sample rather than a held-out benchmark. It does not measure
model answers or general retrieval quality.

## Running the checks

After following the README setup, use the project interpreter:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe scripts\evaluate.py
```

On Linux/macOS, use `.venv/bin/python`. With Node.js and Playwright installed,
run `npm run test:browser` and `npm run check:frontend`.

For a live model evaluation, load the examples and review a factual question,
an unsupported question, the conflicting retention proposal, a synonym query,
and a document containing instructions to ignore the system prompt. Record the
model/version, hardware, retrieved passages, answer, citations, and latency.
Review whether each claim follows from its source, not just whether its source
ID exists.

OCR, multilingual retrieval, large-library performance, parser isolation, and
streaming answers still need separate work.
