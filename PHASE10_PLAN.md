# Proposed Phase 10 — Fast diagram fallback and safe Stop conversion

Status: **awaiting owner approval; do not implement yet**. Scope: the existing one-command Docker application, without changing Phase 8 shelves or finished conversion ZIPs.

## What the supplied logs show

- `diagram_conversion_failed` comes from a per-candidate `except` in `ConvertMermaidStage`. Each failure **already retains the original image**, marks the eventual completed job `PARTIAL`, and adds a review item. Several failures on pages 50 and 78 are separate diagram occurrences, not evidence of an infinite loop.
- The provider chain can make slow requests for many *different* images after an aggregate failure, and the stage logs a full stack trace for each occurrence. With a 90-second configured provider timeout and two HTTP attempts per provider, even one candidate can take considerable time. The UI currently has no Stop button; the existing DELETE route deliberately refuses queued/running jobs to avoid deleting files while a worker is writing them.

## Owner decisions

1. **After the first diagram whose available providers all fail**, stop AI diagram attempts for the rest of that job, regardless of whether failures were configuration, transport, or invalid output. Keep *all subsequent* diagram candidates as their original images, each with a review item; finish text/chapter extraction, packaging and private output as `PARTIAL`. This intentionally sacrifices retries on later diagrams, even when their image might have worked.
2. **Stop conversion removes the job and all its files**, including the source PDF and incomplete output. This requires explicit confirmation in the UI. It is not a pause, and the source must be re-uploaded/reselected from GitHub to convert again. Existing independent shelf snapshots, if any, are unaffected.

## Implementation design

### Diagram stage

- Put a per-job circuit breaker in `ConvertMermaidStage`: after the first aggregate `ProviderError` or invalid Mermaid from the provider chain, retain that image, set `skip_remaining`, and for all later candidates record original-image review items without opening image files or invoking a VLM. An isolated missing/corrupt asset remains a per-image asset failure and does not trip the VLM breaker. Preserve candidate ordering and one review item per occurrence, including repeats on the same page; keep all images in the original private ZIP. Maintain stage progress reporting even while skipping.
- Emit one safe, concise warning that the job switched to image fallback, then a final count. Do not print tracebacks for every *expected* provider failure. Unexpected non-provider errors still log appropriately, without keys or PDF contents. Add unit/integration tests for multiple images/providers: exactly one failed aggregate attempt, zero subsequent VLM calls, `PARTIAL` output with images/review items and no automatic AI retry.

### Stop conversion lifecycle

- Add a nullable `cancel_requested_at` to `jobs` in an additive Alembic migration. Expose `POST /api/v1/jobs/{job_id}/cancel` for an authenticated **owner** (existing admin inspection must not let an admin cancel somebody else's job without an explicit policy). Require CSRF, rate-limit, and conditionally accept only `QUEUED`/`RUNNING`; repeat requests are idempotent while cancellation is pending. Return `202` so the UI can show **Stopping…**, not a false assertion that deletion has already happened.
- Worker-side cooperative cancellation checks (a) before any status transition or task start, (b) at each pipeline-stage boundary, (c) between diagram candidates and relevant long page loops, and (d) after provider/network calls before publishing results. Do **not** SIGKILL a shared Celery worker. Stop may wait for the current OCR/provider/file operation to return; document the upper-bound influences (e.g. configured provider timeout and its retry). Add a terminal cancellation signal for SSE, then delete the job and all its job-owned files/objects only after the worker has ceased writing. Do not mark a cancelled job `SUCCEEDED`/`PARTIAL`, publish an incomplete ZIP, or index it. If storage cleanup fails, retain a visible terminal/error record so the owner can retry cleanup instead of claiming deletion.
- Resolve completion/cancellation races in SQL (conditional update or locked status/flag check): a cancellation acknowledged while the job is still active must not be overwritten by the worker's final status update. Terminal jobs return a conflict rather than destroying completed content. Queued jobs can be acknowledged by the worker before any conversion. Account for Celery `acks_late` redelivery and for a worker restart so a cancellation request does not resurrect an already-deleted job. For MinIO, remove the job's private prefix, including original upload and any partially published output; for local storage remove the job's upload/output trees and ZIP. Do not delete separate shelf copies. Extend stage/progress/UI terminal state handling only as needed; ordinary completed downloads and delete semantics remain unchanged.

### User interface and deployment

- Show **Stop conversion** only for `QUEUED`/`RUNNING`, with a confirmation warning that the PDF, job and incomplete output will be deleted. After `202`, disable repeat submissions and display **Stopping…** until the worker confirms removal; then clear selection and refresh jobs. Surface cleanup failures and let the owner retry. Keep the existing Delete action for terminal jobs. Expose the same capability for local and GitHub-sourced jobs.
- Tests: authenticated owner vs other-user/admin/guest isolation, CSRF, idempotency, queued/running/terminal races, worker checks and redelivery, local/MinIO cleanup failures, SSE/poll state, frontend confirmation and disabled/refresh states, existing Phase 9 upload/search flows. Run backend Ruff/mypy/pytest, frontend typecheck/Vitest/build/audit; update README/rollout notes and deliver an archive. **Live Docker/PostgreSQL/Celery/MinIO validation is not possible in this authoring workspace** and must be done on the deployment host.

## Rollout caution

Do not claim the current job is cancelled by reading these traceback lines: check its live status and recent stage/progress logs first. Deploying/restarting a worker mid-conversion may interrupt its current task and cause Celery `acks_late` redelivery; a safe two-phase Stop becomes available only after the new API **and worker** code are running. Back up the database and storage volumes before the migration/rebuild. No user should run `docker compose down -v` to address this.

**Approval requested before coding.**
