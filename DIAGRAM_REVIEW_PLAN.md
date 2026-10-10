# Diagram review workflow — proposed implementation

Status: **Implemented after user approval.** Exact inventory applies to new conversions. A Docker-host smoke test and PostgreSQL concurrent-edit check remain outstanding; SQLite tests do not exercise row locking.

## Decisions confirmed

- For each diagram that fell back to an image, the administrator may **accept the original image** or **replace that occurrence with manually entered, validated Mermaid**.
- Exact item-level review is guaranteed for **new conversions only**. Older jobs lack a persisted fallback inventory; re-upload their PDFs after deploying this feature.
- No automatic AI retry is in scope. Ollama/provider configuration remains in Settings.

## Current gap

`ConvertMermaidStage` currently retains failed candidates as image `ContentBlock`s and records only an aggregate count on the job. `AssembleChaptersStage` writes Markdown and the ZIP stage packages it, but no durable record connects the failed occurrence to its chapter, page, image, or Markdown location. The existing `PARTIAL`/“Needs review” label is not an approval workflow.

## Proposed behavior and design

1. **Capture exact occurrences.** Add a per-job review-item table with a unique item ID, source page (shown as 1-based), chapter/order, safe image asset name, occurrence position, fallback reason category, review decision, timestamps, and optional validated Mermaid. Create records for each *failed occurrence* during conversion; map them to chapters during assembly. One asset reused on two pages creates two separately reviewable items. Original images remain preserved.
2. **Anchor generated Markdown.** Mark each fallback occurrence with a unique non-rendering review anchor when assembling Markdown, so a later edit changes only the intended image occurrence, not another image with the same filename. Existing chapter rendering should ignore anchors. No client-supplied paths or arbitrary files are accepted for writes.
3. **Review API.** Add a list/detail endpoint for the new inventory and admin-authenticated, CSRF-protected actions to accept an image or submit Mermaid. Validate supported Mermaid structure/safety on the server, enforce size limits, and surface useful errors. Use the existing administrator session from Settings; the review UI prompts for sign-in if necessary. Disallow edits to running/deleted jobs and serialize concurrent edits to the same job.
4. **Keep the download correct.** For a Mermaid correction, update only the anchored chapter block, preserve a link to the original image, update chapter/manifest counts, and regenerate the ZIP. Publish the new chapter/manifest/ZIP as a consistent revision so a failed write does not leave a partial download. Keep the old ZIP available until the revision is ready.
5. **Honest status.** Preserve the pipeline's `PARTIAL` conversion outcome as historical fact. Track **review state separately**: for example, `3 of 18 reviewed`, then `Review complete · 7 original images accepted · 11 Mermaid edits`. An accepted image is not mislabeled a successful Mermaid conversion. The ZIP remains downloadable throughout; the UI explains whether it contains unreviewed items.
6. **Frontend.** Add a “Review diagrams” action to the workspace, a chapter/page-indexed queue with original-image preview and link to chapter context, Accept image and Edit Mermaid actions, validation/preview, decision feedback, review progress, and clear sign-in/save errors. Improve the existing “Needs review” wording to lead to this workflow.
7. **Tests and documentation.** Cover duplicate asset occurrences, chapter/page mapping, authorization/CSRF, invalid or unsafe Mermaid, file/path isolation, concurrent saves, ZIP/manifest consistency, deleting jobs, frontend actions/status, and new-vs-legacy jobs. Document re-uploading older jobs and the difference between conversion status and review status.

## Out of scope / deployment note

- Automatic per-diagram AI retry and editing arbitrary chapter Markdown.
- Retroactive reconstruction of the exact 18 failures in the previously converted Git Notes job.
- Live Docker or model verification in the authoring sandbox; deploy and run a real review/download smoke test on the user's Docker host.
