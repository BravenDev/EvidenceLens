# Changes

## 0.2.0

- New uploads use paragraph-aware 700-character passages with 120-character
  overlap and exact source offsets.
- Keyword search adds English stemming, an explicit small alias map, concept
  coverage filtering, numeric constraints, and previews from FTS snippets.
- Successful searches and questions persist locally as up to 100 result
  snapshots. Results reopen without inference and export to Markdown with full
  source passages. Document deletion also removes related saved research.
- Semantic indexing runs in a background worker with progress, cancellation
  after the current model batch, and reuse of saved batches on resume.
- AI format/citation errors receive one bounded repair attempt. Answers that
  still fail validation remain withheld.
- The browser has a compact results layout, a mobile library toggle, saved-history
  controls, and formatted source files for easier learning and maintenance.
- A transactional schema-v1 migration preserves existing document/source IDs,
  files, extracted text, and embeddings while rebuilding only the keyword index.
- Automated checks cover migration, deleted-source races, bounded history,
  background jobs, format repair, exports, and offline saved-answer reopening.

On the expanded 18-case fictional retrieval sample, v0.1 passes 15 cases and
v0.2 passes 18. The three differences are weak matches for unrelated topics that
share one document word. Both versions achieve answerable-query MRR@5 of 1.000
on this tiny sample; this is not a general retrieval or model-quality benchmark.

## 0.1.0

Initial local application: PDF/TXT/MD uploads, persistent SQLite/FTS5 search,
source inspection, optional Ollama answers and hybrid retrieval, tests, a Windows
setup guide, and a prepared GitHub CI workflow.
