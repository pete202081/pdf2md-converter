# Phase 6 — Conversion quality review (approved)

Status: **approved and implemented locally**. Docker/PostgreSQL/live MinIO smoke tests remain outstanding.

## Goal

Let an owner (or administrator) compare a converted chapter with the corresponding pages of the original PDF, see honest rule-based quality warnings, correct text/table Markdown, and download a ZIP containing those corrections. Keep diagram review and the existing private-account and storage behavior intact.

## Recommended MVP

1. **Private source-page viewer.** In a finished job, choose a chapter and a source page from its `chapters.json` `source_pages` range. Show a bounded server-rendered PNG of that PDF page alongside the chapter Markdown preview. A same-origin endpoint requires an active owner/admin session; validate job ownership and 1-based page bounds before accessing the local upload or privately staging the MinIO upload. Limit image size, concurrency/rate and memory use; send `Cache-Control: private, no-store`; never expose a public storage URL, filesystem path, or full PDF download. Missing/older source PDFs or mapping should show a helpful unavailable state, not another user's data.
2. **Rule-based page warnings for new conversions only.** Persist per-page signals during the existing pipeline: OCR fallback was used, unusually little/empty extracted text, and obviously sparse/irregular extracted tables; show chapter-boundary fallback if detectable. Show the concrete reason, page and severity. These are *review hints*, not a quality score or a claim of measured OCR confidence—the current OCR adapter returns text but not confidence. No extra AI call or automatic retry. Existing converted jobs are not reprocessed; they may still be viewed/edited if their original PDF and manifest are present, but warning inventory requires re-upload.
3. **Review state.** The owner/admin can mark a flagged page reviewed without editing it. Show remaining flagged-page counts by chapter and job; review completion is independent from `SUCCEEDED`/`PARTIAL` conversion status and existing diagram review status. A saved change to a chapter resets its flagged-page review marks so the revised output can be checked again. No auto-approval.
4. **Safe manual correction.** Edit the *whole chapter* Markdown in the split view, mainly for text and tables (not page-aligned fragments). Preview before saving. Preserve the generated YAML metadata and all diagram-review anchors and their contents; continue editing diagrams through the existing diagram review flow. Validate size (proposed 2 MiB), image/link paths, HTML and any Mermaid code server-side; never render unsafe HTML or allow cross-job assets. Require owner/admin + CSRF and an expected-content hash/version: concurrent edits or diagram decisions return 409 instead of silently overwriting work.
5. **Consistent publication and undo.** Under a per-job database lock, publish a copy-on-write chapter + manifest + ZIP revision for either local or MinIO jobs, then switch database pointers only after files are present. Update word counts and changed review state. Refactor the existing diagram revision logic to share the publication utility so diagram decisions and chapter edits cannot overwrite each other. Keep prior revisions for in-flight downloads and a simple undo-last-Markdown-edit action, permitted only while the current chapter hash still matches that edit's saved hash (an intervening edit or diagram decision instead returns 409). Retained revisions consume storage until job deletion. Record who edited and when without putting source text in logs.

## Implementation sequence

- 6A: migration/models for page warnings, review decisions and edit history; collect signals with unit tests, new read APIs with ownership tests; implement bounded private page rendering for local and MinIO.
- 6B: common local/MinIO revision publisher, Markdown validation, optimistic concurrency, undo and API contract; regression tests for concurrent edits, failed uploads, archive/manifest consistency and interference with diagram review.
- 6C: responsive source-page/Markdown split view, warning/review controls and editor; frontend tests, API docs, README/upgrade and backup notes.
- 6D: run lint, typing, backend/frontend suites and production build; exercise Docker/PostgreSQL/local and optional MinIO flows on a Docker host (Docker is unavailable in this workspace).

## Acceptance checks

- A user cannot inspect or edit another user's PDF, page, chapter, warnings, or ZIP (404 for inaccessible jobs); an administrator can.
- A newly converted scanned page is explicitly marked `OCR used`; sparse/empty output and suspect tables are visible as hints without fabricating confidence scores. Older jobs do not falsely report an empty warning inventory as a verified clean conversion.
- Selecting a page displays only that source page next to its chapter. Out-of-range requests and malformed PDFs fail safely.
- Save/reload/download all show the same new Markdown; ZIP `chapters.json` reflects counts. Failed publication leaves old output downloadable, and stale edits return 409.
- Diagram-review anchors remain valid; an edit and a diagram decision cannot silently overwrite each other. Undo restores the previous chapter revision without losing the original PDF.
- No password, PDF text, private page or object-store URL appears in logs/metrics or public routes.

## Not in this phase

No email notifications, automatic AI retries, numeric OCR-confidence claims, PDF annotation/editor, chapter restructuring, full-document PDF download, or reprocessing/backfilling old jobs. Search, batch upload, OCR language choice and EPUB export remain later options.

## Decisions to approve

Recommended defaults: **flagged-page review** (chapter/job counts derive from page flags); **whole-chapter Markdown editing** with protected metadata/diagram regions; **view/edit eligible old jobs, but signals only on re-upload**; **keep revision history until job deletion**. Please approve these defaults or request changes before implementation.
