# Proposed Phase 7 — Searchable private book library

Status: **approved and implemented locally**. SQLite/API/UI tests pass; Docker/PostgreSQL/live MinIO smoke testing is pending on a Docker host.

## Goal and scope

Search filenames, chapter titles, and converted chapter text (including extracted table text) across the signed-in user's **finished** books. Administrators retain their existing ability to see all users' books. A result opens the correct book and chapter, and shows its source **page range**; the first page can be opened in the existing quality-review viewer. Do not claim a precise source page for a passage: chapter-wide Markdown edits cannot reliably preserve word-to-page alignment.

## Recommended design

1. **PostgreSQL-backed full-text index, no AI or external search service.** Add a `search_documents` row per `(job_id, chapter_order)` containing normalized searchable text, chapter/book display fields, source-page range when known, and an index revision/hash. Use PostgreSQL's built-in English full-text search with a GIN expression index and relevance ranking. Tests on SQLite use a bounded literal-match fallback; Docker/PostgreSQL must be smoke-tested on the user's host. Bound indexed text size and skip/truncate safely rather than exhausting memory on anomalous chapters.
2. **Private search API.** `POST /api/v1/search` with JSON `{query, limit}` (2–100 characters, default 20 results, hard cap 50). POST keeps private search terms out of URLs/access logs and uses the existing session + CSRF controls. Filter `Job.owner_id` (unless admin) and finished job status **in SQL before limit/ranking**; no cross-user result counts or snippets. Parameterize all query terms. Limit request rate by user/IP; do not log query text. Return plain-text excerpts (React escapes them), job ID/filename, chapter order/title, and truthful source-page range when available.
3. **Index lifecycle.** Create/update a row for each chapter after a new conversion has published its final files (both local and MinIO; only finished jobs become searchable). On manual chapter save/undo and on diagram-driven Markdown changes, update the same row in the same database transaction as the chapter/output pointers; no stale hits after revisions. Job deletion cascades to index rows. Search/index failures must not destroy the PDF conversion or already downloadable ZIP; report an index-pending/error state and offer a retry/reindex path.
4. **Existing books.** Schema migration preserves books and does not scan files or contact MinIO. Provide a resumable, idempotent admin CLI command to index already converted jobs from their **current** chapter Markdown, using each job's recorded local/MinIO backend. A per-job failure is reported without stopping the entire batch; retry is safe. The library explains that older books may need this command, rather than silently presenting them as searchable. No PDF re-upload or AI call is needed for search backfill.
5. **Library UI.** Add a search box with clear/loading/empty/error states and a bounded results list. Selecting a hit opens its book and chapter; a source-pages link opens the existing quality-review view at the first page of the reported range when a source PDF is present. Empty query restores the existing library. Keep it usable on narrow screens and keyboard-accessible; no unsafe HTML highlighting.

## Engineering and validation

- Keep indexing/excerpt normalization in a dedicated service and repository; use SQL-side owner filtering and shared index-upsert logic for conversion, edits, diagram review, and the CLI. Use explicit exception handling and structured **metadata-only** logs.
- Migration: table, unique `(job_id, chapter_order)`, owner-independent FK cascade, PostgreSQL GIN index; safe repeatable upgrade. Back up the database and storage volumes before deployment; never use `down -v`.
- Tests: unit normalization/ranking edge cases; API 401/403, CSRF, owner isolation/admin visibility, query bounds and injection/XSS cases; SQLite index lifecycle tests; local and mocked-MinIO edit/undo/diagram/deletion consistency; CLI idempotency/error continuation; frontend click-through and error states. Run lint, typing, tests and production build. Live Docker/PostgreSQL/MinIO smoke tests must be done on a Docker host (Docker is unavailable in this workspace).

## Explicit limitations / later options

No semantic/vector search, embeddings, OCR rerun, PDF-content search beyond existing extracted Markdown, cross-user sharing, or guaranteed exact hit-to-PDF-page mapping. Searches use English-language tokenization initially; multilingual stemming can be considered with OCR language selection later. Raw search terms and private excerpts must not appear in metrics or logs.

## Approved defaults

- Search **finished** chapter content + book/chapter names; owners see only their books, admins see all.
- Results link to chapter and show source **range**, not a fabricated exact page.
- One explicit admin backfill command for existing jobs; new jobs and revisions index automatically.
- PostgreSQL full-text search; no new search container, AI call or cloud key.

These defaults were approved before implementation.
