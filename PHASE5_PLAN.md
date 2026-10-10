# Phase 5 — Production hardening plan

**Status:** Approved; 5A–5C implemented and locally verified (PostgreSQL/MinIO Docker-host smoke tests outstanding).

## Confirmed product decisions

- **Multi-user, private jobs with approval.** Anyone may request an account using a username and password; the account is **pending and cannot access jobs until an administrator approves it**. Admin may reject/disable accounts and inspect all jobs for support. No public job access or automatic approval.
- **Legacy ownership.** Existing jobs become admin-owned during migration; never become visible to every signed-in user.
- **Storage.** Local Docker volumes remain the default. An optional MinIO profile/backend can store *new* jobs; old local jobs remain readable after enabling it. No automatic migration of old files.
- The existing Settings page remains admin-only, and review changes require the owning user or admin (instead of any signed-in user). Infrastructure settings and credentials remain Docker-side.

## Milestone 5A — Versioned migrations and multi-user isolation

1. Add Alembic with a tested **baseline path for an existing Phase 4 database** and a clean-install path. For an existing database without `alembic_version`, a preflight validates the expected tables/columns and stamps the matching baseline; an empty database applies the baseline migration normally. Refuse unknown schemas rather than guessing. Subsequent migrations move the existing admin password hash to an admin user, assign all preexisting jobs to that user, and add non-null owner IDs and relevant indexes. Preserve jobs, ZIP paths, reviews, encrypted provider keys and the shared encryption-key volume. Migration failures stop startup; production API/worker no longer call `create_all`.
2. Add a one-shot Compose `migrate` service (`python -m app.migrate_cli`, with safe baseline preflight then Alembic upgrade) required before API/worker start. Back up `db_data`, `uploads`, `outputs`, and `settings_keys` before upgrading. Ship a dry-run/check command and rollback guidance; never silently drop or overwrite existing records.
3. Extend the current first-run admin setup into user accounts and sessions. A public, rate-limited **request account** form accepts a normalized unique username and password (no email/SMTP requirement); it creates a `PENDING` account. Pending or rejected users cannot use any job endpoint. The administrator reviews requests in a protected UI/API and can approve, reject or later disable an account; disabling revokes sessions. Users can change their own password; an admin can issue a short-lived single-use reset code if a user forgets it. Admin password recovery remains CLI-only. Migrate old admin sessions by requiring re-login (documented). Keep HttpOnly/SameSite cookies, CSRF on mutations, Argon2 password hashing and audit-safe logs. No email verification or notification is implied: admins must check the pending queue.
4. Require authentication on **all** job/list/detail/chapters/asset/ZIP/SSE endpoints, with per-owner repository filtering and 404 for cross-user IDs. Admin can inspect all jobs; approved regular users cannot see or modify others' jobs/reviews. Require owner/admin for diagram review mutations. Frontend gets sign-in, request-account and pending-approval screens, admin approval controls, account/logout controls, and a private job library; downloads and EventSource use same-origin cookies.
5. Security regression tests: legacy migration and fresh install, foreign-job ID guessing across every endpoint (including SSE/ZIP/assets), pending/rejected/disabled users blocked, approval and reset-code expiry/reuse, sign-up/login abuse limits, CSRF, logout/session expiry, and no password/key leakage.

## Milestone 5B — Rate limits, production defaults, and observability

1. Implement Redis-backed limits with explicit 429/Retry-After: public sign-up, login/setup/reset-code verification by IP *and* account identity, upload by account (and IP), expensive review/provider checks, and bounded concurrent SSE sessions. The sign-up endpoint gets strict per-IP and per-username throttles and an account-request cap to contain spam. Exempt health checks and tune polling reads so the existing frontend is not throttled. Make thresholds configurable via infrastructure settings, test with fakeredis and multiple API instances, and avoid trusting spoofable forwarded headers.
2. Harden Compose/nginx: safer defaults for exposed ports and secrets, HTTP security headers, appropriate upload/timeouts, resource and logging limits, and documented TLS/reverse-proxy expectations. Do not claim end-to-end HTTPS until a real TLS deployment is verified.
3. Add Prometheus-compatible metrics for requests by **route template** (not job ID), latency/status, jobs by outcome, stage durations, provider fallback/error counts, and queue/worker health. Never label metrics with filenames, users, job IDs or secrets. Keep `/metrics` off the public nginx route; make it available only to a restricted internal scraper/optional Prometheus profile. Add tests for metric names/labels and failure paths.

## Milestone 5C — Optional MinIO backend

1. Provide a profile/override that starts MinIO and initializes a private bucket with health checks; default Compose continues to use local volumes. Document activation, credentials, backups, and MinIO startup ordering without embedding a public default password.
2. Introduce **per-job storage backend metadata** and an object-store adapter. Keep safe object keys namespaced by job/user. Pipeline code currently requires filesystem paths (PDF parsing, OCR, chapter assembly, ZIP); stage each MinIO job into an isolated worker-local directory, upload finalized artifacts and ZIP to MinIO *before* declaring the job downloadable, and clean up staging on success/failure. Avoid buffering entire PDFs/ZIPs in RAM.
3. Route preview, original image, ZIP download, review edits/revisions, and delete through the backend recorded for that job. Serve objects via authenticated API streams; no public bucket or unprotected presigned URLs. Local jobs remain accessible when MinIO is enabled. Enforce rollback/no partial publication on failed uploads or review edits.
4. Test both backends with mocked S3 and a Docker-host MinIO integration smoke test: upload → convert → preview → review/edit → ZIP → delete; verify a legacy local job alongside a new object-store job.

## Delivery and acceptance gates

- Deliver 5A, 5B, 5C **in order**, reporting tests and limitations at each milestone. Do not switch existing installations to MinIO automatically.
- Backend Ruff/format/mypy/pytest; frontend types/Vitest/build/audit; migration upgrade tests for old and empty DB; Compose config validation. A Docker host is needed for Postgres migration, concurrency, Redis, worker and MinIO end-to-end verification—none will be claimed from the authoring sandbox if Docker is absent.
- Update README and deployment/security guidance after each milestone. No default-public access to existing PDF jobs, no plaintext provider keys returned to browsers, no destructive migration of saved jobs or review revisions.
