# Proposed Phase 11 — Direct chapter cleanup editor

Status: **approved and locally implemented**. Live Docker/PostgreSQL deployment remains unverified.

## Why this change

The workspace's **Preview / Source** switch only displays chapter Markdown, and **Copy Markdown** does not save changes. A separate **Review quality → Edit text & tables** editor already exists for some completed conversions, but it is buried under a source-page workflow and requires a usable source PDF/page mapping when saving. That leaves older completed jobs without an obvious way to correct their chapters.

## Decisions confirmed

- Put a direct editor beside the currently selected chapter for **all SUCCEEDED and PARTIAL jobs**, including older conversions without source-page mappings. Jobs still need a readable chapter and a valid downloadable archive; a missing/corrupt archive cannot be revised silently.
- Saving updates the chapter preview, downloadable ZIP and its manifest, and private search index. **Previously imported shelf books remain independent snapshots** and are not automatically rewritten.
- Allow manual edits to content Markdown: headings, prose, lists, tables, links and Mermaid code, subject to asset and rendering safety validation. No automatic AI rewrite is proposed.

## User experience

1. For a completed job, show a prominent **Edit chapter** button in the selected chapter's preview controls; keep Preview, Source and Copy. Failed/queued/running jobs remain read-only. The editor opens with the current Markdown, a live rendered preview, **Save changes**, **Discard changes**, and (when available) **Undo last edit**. Explain that Save updates the ZIP/search, not shelf copies. Display loading, save and conflict/errors inline.
2. Preserve unsaved work when switching Preview/Source within the chapter; warn before abandoning a dirty draft by changing chapters/jobs, navigating away or closing the tab. Do not overwrite a dirty draft when background polling refreshes a job. On successful save, reload current Markdown/detail and refresh the visible preview, search-related metadata and ZIP link.
3. If another revision was saved while the draft was open, show a conflict and offer reload; never silently overwrite the newer version. A failed network request must not be presented as a successful save.
4. Keep **Review quality** for comparing the PDF page and flagging it. Keep **Review diagrams** for changing the decision/code of a generated fallback diagram; the direct editor links to that workflow when the user tries to change its protected review region.

## Backend design

- Add explicit owner-authenticated, CSRF-protected direct chapter edit/read/undo endpoints (or a clearly documented extension of existing quality endpoints). Share the existing `QualityReviewService` copy-on-write revision machinery and `ChapterEdit` history rather than introduce a second persistence path. Remove the *source PDF/page-map requirement only for the direct editor*; source-page viewing and quality-warning review retain their existing guards. Access remains restricted to the job owner or the existing authorized admin policy, and edits require `SUCCEEDED`/`PARTIAL` plus a current chapter and valid ZIP.
- Preserve the existing optimistic SHA-256 `expected_hash` check, per-job database lock, bounded 2 MiB chapter input and atomic publication pattern: generate a new local/MinIO chapter file and ZIP; validate member paths and assets; update chapter pointer, ZIP pointer, word count, manifest and search index in one DB commit. Keep the prior revision for Undo. Ensure a failed ZIP/storage/DB operation leaves the old downloadable revision intact and cleans up an unpublished revision where possible.
- Expand content validation to permit ordinary Mermaid fences **only after Mermaid syntax/safety validation**. Reject new or changed raw HTML (but allow untouched legacy extraction markup to remain, or be removed), unsafe links and references to assets outside this job. Generated frontmatter/source-page identity and review-anchor IDs must remain internally consistent. Edits to a protected diagram-review region go through **Review diagrams**, not untracked direct edits, so a later diagram decision cannot overwrite a manually changed region or desynchronize review metadata. Ordinary prose and Mermaid outside those regions are editable. Make this exception explicit in the UI.
- If editing the chapter's leading heading, keep the sidebar title, manifest title and search title synchronized with it (and update generated title metadata safely); retain the stable folder name and original source-page references. Recompute word/Mermaid counts from the saved content where applicable. If an old job has no source-page map, use its current chapter record/ZIP member rather than fabricate one; a missing or corrupt ZIP/manifest yields a clear actionable error instead of false success. Do not mutate existing shelf snapshots.
- Keep old quality-edit routes operational, but route both editors through the same revision service/validation rules or a deliberately separate stricter policy where required. Log revision failures without logging entire chapter text or secrets.

## Tests and delivery

- Backend: completed/local and MinIO jobs, older job with no source-page mapping, draft SHA conflict, failed/active/non-owner authorization, CSRF, input validation (unsafe links/HTML/asset/invalid Mermaid/altered anchors), heading/manifest/search/count synchronization, ZIP member preservation, failed publication, undo, and independent shelf snapshots.
- Frontend: editor visibility, live preview, Save/Discard/Undo, unsaved-change warning, stale edit conflict, refreshing the saved ZIP/preview, and failure messaging.
- Run Ruff, mypy, pytest, frontend typecheck/Vitest/build/audit. Update README and deliver a full project archive. Docker/PostgreSQL/Celery/MinIO deployment must still be verified on the user's host; no migration should be needed if the existing `ChapterEdit` schema suffices.

## Current workaround until approved/implemented

For a finished job with a valid source-page mapping, try **Review quality → Edit text & tables → Save chapter & ZIP**. This existing editor intentionally protects generated metadata and diagram blocks; it is not the requested direct full-content chapter editor. If the quality panel has no chapter/source mapping, it may not offer editing even though the preview displays the chapter.
