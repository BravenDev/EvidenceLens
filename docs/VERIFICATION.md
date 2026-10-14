# Verification of EvidenceLens 0.2.0

This report records local checks completed before GitHub source publication.
The repository's Actions tab reports current CI results separately.

## Executed checks

| Check | Result | Scope |
|---|---|---|
| Python 3.15.0 automated suite | 91 passed | Executed on Linux with stable CPython 3.15.0 and SQLite 3.53.1 FTS5 |
| Python 3.12.14 automated suite | 91 passed | Compatibility check on Linux |
| Dependency consistency | Passed | `pip check` in the Python 3.15 test environment |
| Ruff lint and formatting | Passed | Delivered Python source and tests |
| JavaScript syntax | Passed | Browser application and browser smoke script |
| Prettier formatting | Passed | Browser source and smoke script |
| Retrieval baseline | 18/18 cases; answerable-query MRR@5 1.000 | Three fictional documents, keyword retrieval only |
| Schema-v1 migration | Passed | Legacy source ID, file, vector, source text, and rebuilt search preserved; repeat initialization checked |
| Actual browser smoke workflow | Passed | Playwright with Chromium 153 on Linux |
| Desktop and mobile visual inspection | Passed | Desktop 1440 px; mobile 390 px; source dialog reviewed |

The Python suite uses temporary databases and a simulated model adapter. Tests
also exercise Ollama HTTP contracts using an HTTP mock transport. The browser
test uses the normal application adapter against a simulated local HTTP service
with fixed vectors and responses. No API credits or model downloads are required
for these checks. That fixture is only part of the test script.

## Retrieval comparison

Both releases were evaluated on the same three supplied fictional documents and
the expanded `examples/evaluation.json`. Each used its own default chunking and
keyword pipeline; this compares the complete versions, not an isolated algorithm.

| Measure | v0.1.0 | v0.2.0 |
|---|---|---|
| All regression cases passed | 15/18 | 18/18 |
| Answerable cases with expected document in top 5 | 13/13 | 13/13 |
| Unsupported cases with no retrieved passage | 2/5 | 5/5 |
| Answerable-query MRR@5 | 1.000 | 1.000 |

The three differences are `pilot telescope`, `budget volcano`, and
`retention quasar`. Previously, a shared word was enough to return evidence.
The coverage rule now rejects these cases. English variants, single-digit
numbers, and aliases also have targeted tests. These cases were developed with
the implementation; they are a small regression suite, not a held-out dataset
or evidence of general retrieval performance. No answer-quality score is claimed.

## Browser behavior exercised

- Initial interface and empty-question handling.
- Loading the sample collection and preventing duplicate sample records.
- Keyword search and correct first source for the audit-log query.
- Opening source context and highlighting the actual retrieved text.
- Downloading the original source document as an attachment.
- Filtering the library, selecting a document scope, and keyboard question submission.
- Uploading text files and skipping duplicates with different filenames.
- Rendering HTML-like document content as literal text without executing it.
- Explicit insufficient-evidence responses and the local AI setup dialog.
- Background indexing progress while keyword research remains usable.
- Reconnecting to an active job after a page reload, requesting cancellation,
  preserving the completed eight-passage batch, and resuming the remaining cache.
- Hybrid retrieval and AI citation rendering through the normal HTTP adapter
  against simulated model responses, including one invalid-citation repair.
- Saved AI answers reopening with the model reported unavailable, with no new
  embedding or chat calls, and restored answer/retrieval controls.
- Markdown downloads containing answers, source IDs, locations, and full passages;
  embedded HTML/backtick content remains inside safe text fences.
- Individual saved-result deletion and clearing history without removing documents.
- Deleting a document and keeping other documents available after a browser reload.
- Removing saved results related to that document while preserving unrelated history.
- Mobile research workflow with no horizontal overflow.
- Passage previews centered on matching question terms.
- No JavaScript page errors during the tested workflow.

Persistence across a full application shutdown/restart is separately checked in
the Python suite, not inferred only from a browser reload. The suite also verifies
bounded history, migration, unknown newer schemas, and a source disappearing
during generation. PDF tests use real
in-memory PDFs, including blank pages, page-number gaps, password protection,
and page/character limits. A streaming ASGI test verifies request limits without
relying on a Content-Length header.

## Not executed or not established

- **Live Ollama model generation and real-model semantic quality:** models were not
  downloaded in this environment. The integration is implemented and its protocol
  and failure paths are tested, but live model behavior must be evaluated locally.
- **Local native Windows execution:** local checks ran on Linux. The GitHub CI
  matrix separately checks Windows; consult its completed runs for those results.
- **Public application deployment:** publishing source does not start a hosted
  Python server or an Ollama service. This release runs locally.
- **Large-scale throughput, hostile-parser isolation, multilingual answer quality,
  and automatic claim entailment:** these are outside this version's verification.

## Reproduce

Use the README's environment setup, then run `python -m pytest -q`,
`python -m ruff check .`, `python -m ruff format --check .`, and
`python scripts/evaluate.py` with the project interpreter. The optional
`npm run test:browser` script starts an isolated server and temporary library.
Run `npm run check:frontend` to reproduce the optional Prettier check.

For live AI verification, download the configured chat and embedding models,
load the fictional samples, build the semantic index, and manually review:

1. An explicit factual question: the pilot timeline.
2. A policy-versus-proposal question: approved audit-log retention.
3. An unsupported question: a planetary fact absent from the documents.
4. A synonym query comparing keyword and hybrid retrieval.
5. A document containing instructions to ignore the assistant's system prompt.

Record the model name/version, hardware, settings, retrieved passages, generated
answer, citations, latency, and human review. Evaluate claim support separately
from whether the cited passage ID exists.
