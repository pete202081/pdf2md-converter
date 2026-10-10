# PDF2MD Converter

A Docker Compose application that converts PDF eBooks into **chapter-wise Markdown**: text (including OCR for image-only pages), Markdown tables, linked images, and likely embedded diagrams transcribed as **Mermaid** code blocks. Output includes ordered chapter folders (`01_…`, `02_…`), a `chapters.json` manifest, and a downloadable ZIP. The React frontend lets you upload books, follow live conversion progress, browse chapters with rendered Mermaid diagrams, and download the result.

See [ARCHITECTURE_PLAN.md](./ARCHITECTURE_PLAN.md) for the approved design and locked decisions.

## Quickstart

Requires Docker and Docker Compose v2.24+ (for optional `env_file` syntax).

```bash
# From the repository root
cp .env.example .env       # optional; edit limits and other settings as needed
# Recommended on a fresh installation: add a URL-safe DB password before first start:
printf 'PDF2MD_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)" >> .env
docker compose up --build
```

- React app (upload, progress, preview, ZIP): http://localhost:8080
- Swagger API docs: http://localhost:8080/docs (ReDoc: http://localhost:8080/redoc). Both use self-hosted assets compatible with the site-wide CSP; the live OpenAPI schema comes from `/openapi.json`. ReDoc search is disabled because its blob-based worker conflicts with the strict CSP (Swagger UI remains searchable). For Docker updates, rebuild the **web** image (`docker compose up -d --build web`) because it contains the docs pages and assets.
- Health checks: http://localhost:8080/healthz and http://localhost:8080/readyz

Docker starts nginx (`web`, serving the built React app), FastAPI (`api`), a Celery worker, Redis and PostgreSQL. A one-shot `migrate` service upgrades the database before API/worker start; it refuses unexpected unversioned schemas. Uploads, generated output and database data persist in named Docker volumes. The backend image includes Tesseract for scanned PDFs. For diagram conversion, the default **hybrid** mode tries local Ollama first, then whichever cloud keys are configured; if none work, the original image is retained and the job becomes `PARTIAL`. Ordinary non-diagram images do not require a VLM.

### Upgrading a Phase 4 deployment safely (Milestone 5A)

Before running the new Compose file, **stop old API/worker instances and back up** the PostgreSQL database and the `uploads`, `outputs`, and `settings_keys` named volumes (these preserve PDFs, ZIPs, review revisions, and encrypted-key material). On a running Phase 4 stack, for example:

```bash
docker compose stop api worker
docker compose exec -T db pg_dump -U pdf2md -Fc pdf2md > pdf2md-before-phase5.dump
# Also back up the named volumes with your Docker volume backup procedure.
# After installing the new Compose file, run the read-only schema preflight:
docker compose run --rm migrate python -m app.migrate_cli check
# Then start/upgrade everything, checking migration logs before allowing uploads:
docker compose up -d --build
docker compose logs migrate
```

The preflight starts `db` if needed; it reads the schema without modifying it. On an existing unversioned **current Phase 4** schema, the one-shot service validates table/column signatures, stamps the baseline, then upgrades. It retains existing jobs and stored paths, assigns them to `admin`, and preserves reviews and encrypted settings. Existing admin sessions are invalidated: log in again with username **admin** and the previous password. No password is invented. On migration failure, do not retry against an unknown schema; restore the **database and all four volumes together** from the pre-upgrade backup, and inspect the failing migration. Downgrades intentionally refuse destructive changes.

### Deployment hardening and observability (Milestone 5B)

Compose binds the frontend to **127.0.0.1:8080** by default; API, Redis and PostgreSQL have no published ports. For access beyond the Docker host, terminate HTTPS at a trusted reverse proxy that forwards to localhost:8080. Set `PDF2MD_FORCE_SECURE_COOKIES=true` when TLS terminates ahead of nginx, so login cookies keep the Secure flag on the internal HTTP hop. Configure proxy forwarding and the trusted nginx peer CIDRs as needed (`PDF2MD_TRUSTED_PROXY_CIDRS`); do not allow clients to set `X-Real-IP`. Nginx sends restrictive security headers, uses a 64 MiB request cap and bounded log files. Resource limits are configurable using `PDF2MD_API_MEMORY`, `PDF2MD_WORKER_MEMORY`, etc. The development fallback PostgreSQL password is **not** suitable for a network deployment: set `PDF2MD_DB_PASSWORD` to a URL-safe random value *before creating a new `db_data` volume*. PostgreSQL ignores `POSTGRES_PASSWORD` changes on existing volumes; for an existing database, rotate the role password in PostgreSQL in a coordinated maintenance window. Never commit `.env` or expose port 8080 as plain HTTP on untrusted networks. HTTPS security, real proxy IPs, and resource sizing require verification on your deployment host.

Redis-backed per-IP and per-account limits return HTTP **429** with `Retry-After`; separate quotas cover signup, login, uploads, review edits, provider checks and SSE. Thresholds are set by the variables in `.env.example`. Health checks and normal job polling are not throttled. Monitoring is available internally from `api:8000/metrics` to a restricted scraper on the Compose network (or a separately authenticated tunnel); nginx deliberately does **not** forward `/metrics`. Metrics expose route *templates* and bounded stage/provider/outcome labels—never usernames, filenames or job IDs. The worker records stage/provider counters and a 30-second liveness heartbeat in Redis, so clearing Redis resets counters and briefly marks the worker unhealthy. Redis/worker availability and metric correctness should be checked on a Docker host.

### Optional private MinIO storage (Milestone 5C)

**Local named volumes remain the default.** To put *new* jobs in a private S3-compatible MinIO bucket, add `PDF2MD_MINIO_ROOT_USER` and a strong random `PDF2MD_MINIO_ROOT_PASSWORD` to your untracked `.env`, then run:

```bash
docker compose -f docker-compose.yml -f docker-compose.minio.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.minio.yml logs minio minio-init migrate
```

The override builds MinIO from a pinned upstream source commit (the first build downloads Go dependencies and may need substantial RAM/time), starts it on the **internal Docker network only**, creates a private bucket with the backend's S3 client, and waits for initialization before API/worker start. No public bucket URL or presigned links are used. The root account is used for this simple single-host deployment; for production use a scoped application identity. Per-job storage metadata keeps old local jobs on `uploads`/`outputs` even when new jobs use MinIO; do **not** remove the original volumes. The API initially validates an uploaded PDF on local disk, then streams it into MinIO and deletes its API-local copy. A worker stages each MinIO PDF into a private temporary directory, converts it, and uploads chapter files, images, and ZIP **before** marking the job downloadable. The API streams object-backed previews, assets and ZIPs only after ownership checks. Review edits publish versioned replacement objects before changing the database pointers. Keep MinIO credentials configured to access old MinIO jobs even if later returning new uploads to local storage. Back up `minio_data` together with `db_data` and `settings_keys`; never expose MinIO API/console ports by default. Image availability and the full MinIO/PostgreSQL Compose flow still need checking on a Docker host.

### Private accounts

The public **Request account** form records a username (or an email-shaped sign-in name of at most 64 characters) and password. Email-shaped names are **not verified email addresses**; this app sends no email. Only after the form displays **“Request saved as pending”** has the server confirmed the request. Accounts stay **pending** with no job access until the administrator checks **Settings → Account requests** and approves them. That list has a **Refresh requests** button and also refreshes every 20 seconds while Settings is open. The admin may reject/disable users, inspect all books, and issue one-time reset codes (15 minutes); share a reset code privately. Users can change their password from the workspace. A user's jobs, previews, review edits, images, SSE stream and ZIPs are private; legacy jobs are admin-owned. There is no email delivery or automatic approval. Admin password recovery remains `docker compose exec api python -m app.admin_cli reset-password`.

If a request is not visible, first refresh the administrator's Settings page and check that the signup browser showed the success message (not a red validation or network error). The applicant and administrator must be using the same deployment/database. To inspect the database **on your own Docker host**, without handling or displaying password hashes, run:

```bash
docker compose exec db psql -U pdf2md -d pdf2md -c "SELECT id, username, status, created_at FROM users WHERE role = 'USER' ORDER BY id DESC LIMIT 20;"
```

Do not post the unredacted output publicly: it contains account identifiers. If the expected identifier is absent, there is no saved request in this database; submit the form again after updating the app with `docker compose up -d --build` and look for the **pending** confirmation. If it is present but Settings still does not show it after Refresh requests, check that the administrator is signed into the same deployment and inspect the browser's `/api/v1/admin/users` response for errors (never share session cookies). A 401/403 indicates a session/permission issue; a failed request is shown in Settings instead of being mistaken for an empty list.

### First-time admin setup and provider configuration

1. **Start the application** from the repository root on your Docker host. Then generate a one-time administrator setup code:

   ```bash
   docker compose up -d --build
   docker compose exec api python -m app.admin_cli
   ```

   The second command prints a private setup code valid for **30 minutes**. If it expires, run that command again to generate a new one. You need the code only when creating the first administrator account; if an account already exists, sign in instead.

2. **Create the admin password:** open http://localhost:8080, select **Administrator setup / settings**, enter the printed setup code and choose a password of **at least 12 characters**. Select **Create account**. Do not share the code or password.
3. **Configure a provider:** in Settings, choose a provider mode and enter a reachable Ollama URL/model or an OpenRouter, OpenAI or Anthropic key and vision model. Click **Save settings** *before* clicking **Test saved configuration**. Cloud tests may incur a small charge; the Ollama test checks its `/api/tags` endpoint and configured model. Starting the optional Ollama Docker service still requires `docker compose --profile local-vlm up -d` (and allowing its model download to finish).
4. **Try a new conversion:** return to **Workspace** and upload the PDF again. Saved settings apply to **new uploads and conversions without further rebuilds or restarts**; existing or running jobs are not retried. PDFs converted before item-level diagram review was added must also be re-uploaded to populate the review queue.

The same Settings page also controls VLM timeout, maximum pages and PDF size (up to 50 MiB). If you later lose the admin password, run `docker compose exec api python -m app.admin_cli reset-password` from a trusted Docker host; this resets the password and signs out existing admin sessions.

Provider keys are encrypted in PostgreSQL with a key in the `settings_keys` Docker volume. Keep **both** `db_data` and `settings_keys` in backups: deleting the latter makes saved keys unrecoverable. Keys are never returned by the Settings API; blank means *keep*, **Clear saved key** means *remove*. `.env` provider values continue to work as defaults until overridden from the UI. Database, Redis, storage paths, nginx's 64 MiB request limit and starting the Ollama Compose service remain Docker/infrastructure configuration. The admin session is HttpOnly/SameSite and settings changes require a CSRF token; use HTTPS when accessing the app remotely. **Settings require an administrator account**; jobs, review edits, images and downloads require the owning approved account or an administrator. Do not expose plain HTTP on an untrusted network; terminate TLS at a trusted reverse proxy.

### Enable diagram → Mermaid conversion

**Local Ollama (default hybrid first choice):**

```bash
docker compose --profile local-vlm up --build
# The ollama-pull service downloads llama3.2-vision into a persistent volume.
# Wait for the model download to finish before submitting diagram-heavy books.
```

The vision model is a **large download** and can need substantial RAM or a GPU. To use an existing Ollama host instead, set `PDF2MD_OLLAMA_BASE_URL` in `.env` to a URL reachable **from the worker container**.

**OpenRouter (without a local model):** select **Settings → OpenRouter only**, enter a vision-capable model and API key, then **Save settings**. Alternatively, for environment-based configuration, set these values in `.env` and recreate the API and worker containers:

```dotenv
PDF2MD_VLM_PROVIDER=openrouter
PDF2MD_OPENROUTER_API_KEY=your-openrouter-key
PDF2MD_OPENROUTER_VISION_MODEL=openai/gpt-4o-mini
# Optional app attribution:
PDF2MD_OPENROUTER_SITE_URL=https://your-domain.example
PDF2MD_OPENROUTER_APP_NAME="PDF2MD Converter"
```

Choose an OpenRouter model that accepts **image inputs**. The model name is an OpenRouter model ID, not an OpenAI API key or endpoint. `PDF2MD_VLM_PROVIDER=hybrid` (default) tries Ollama → **OpenRouter** → OpenAI → Anthropic, skipping any cloud provider with no key. `cloud` uses configured cloud keys in that order without Ollama; `local` disables all cloud calls; `openrouter` uses only the OpenRouter key. Direct provider keys remain `PDF2MD_OPENAI_API_KEY` and `PDF2MD_ANTHROPIC_API_KEY`. Requests to OpenRouter send the diagram image to OpenRouter/the selected model provider and may incur charges. Never commit `.env`.

The model generates Mermaid **only for classified embedded raster diagrams**, not every image. A conservative heuristic skips photos, tiny icons, empty images and full-page scans. The generated code passes structural/unsafe-content checks but is **not guaranteed semantically faithful or parse-perfect**. The original asset stays in the ZIP, and successful Mermaid includes a link back to it. An unconverted likely diagram remains an image; `convert_mermaid` stage metrics and job status (`PARTIAL`) make the fallback visible. Vector-only diagrams drawn as PDF paths are not rasterized in this phase.

### Conversion quality review (Phase 6)

Back up the database and storage volumes before updating an existing installation. The one-shot `migrate` service adds page-warning and chapter-edit history tables, then you can deploy everything (API, worker and web all changed) using `docker compose up -d --build` **without** `down -v`. Inspect `docker compose logs migrate api worker web` if startup fails. Optional MinIO deployments must use both Compose files and retain the original local volumes and MinIO objects.

On a finished book, click **Review quality**. Select a chapter and a source page to compare the privately rendered PDF page with the chapter Markdown. New conversions show rule-based hints for OCR fallback, very little extracted text, sparse tables and unusually long single-chapter detection. Hints do **not** claim OCR confidence or guarantee accuracy. You may mark a flagged page reviewed. In **Edit text & tables**, correct the chapter Markdown, preview, and save to publish a revised chapter and ZIP; **Undo last edit** is available if no later change (including a diagram decision) replaced that revision. Protected metadata, diagram review blocks, and Mermaid code must remain unchanged; use **Review diagrams** for diagram changes. Another editor's saved revision causes a 409—reload instead of overwriting it. Saving or undoing resets that chapter's reviewed-page marks. These review marks do not change the historical conversion status. Prior revisions consume disk/object storage until job deletion.

Older jobs are not automatically reprocessed: they have no measured quality-hint inventory until re-uploaded. If an older job still has its original PDF and `chapters.json` page map, source comparison and manual editing are available; otherwise the panel explains what is missing. PDF pages and edits require the owner or administrator; original PDFs are never served as public URLs. Local and optional MinIO jobs keep their respective storage backends. The page-view limit defaults to 300 per user per hour (`PDF2MD_PAGE_VIEW_LIMIT_HOUR`); manual edit/review actions use the existing review limit. The source-page image is rendered on demand and may take longer with MinIO, because the private PDF must first be staged. The Docker/PostgreSQL/live MinIO flow should be exercised on your deployment host before relying on it for important data.

### Search your private library (Phase 7)

**Phase 7 indexing fix:** Some older PDF chapters contain embedded NUL/control bytes. The search index now replaces those bytes with spaces in its private searchable copy; it does not edit chapter Markdown or ZIP archives. After installing this update, rerun the normal reindex command below. Books already marked `READY` are skipped; books marked `ERROR` are retried.

After backing up the database and storage volumes, deploy with `docker compose up -d --build` (for optional MinIO, use both Compose files). The one-shot migration adds a private PostgreSQL full-text chapter index without reading your existing files. **New** completed conversions are indexed after local files or private MinIO objects are published; chapter text/table edits, undo and Mermaid review updates replace the matching index row in the same SQL transaction as the new chapter pointer. Search is optional: if indexing fails, the book and downloadable ZIP remain intact, and the search state is shown in the library. Deleted books disappear from search.

The **Search the shelf** box finds finished books by filename, chapter title, or extracted Markdown text (including table values). Users search only their own books; admins can search all books they are already allowed to inspect. A hit opens the matching chapter. The page range belongs to the **chapter**, not the exact phrase; **View first source page** opens the existing quality-review viewer, if the source PDF/page map is still available. Older jobs without a trustworthy chapter page range display “Source-page range unavailable.” Search excerpts are plain text, never interpreted as HTML. English-language PostgreSQL full-text search is used (not AI); there is no search for text not present in the converted chapter.

**Index already converted books:** after migration, from the project root on the Docker host run:

```bash
# Default local-storage stack; run without --force to skip READY books:
docker compose run --rm worker python -m app.search_reindex_cli
# Optional MinIO stack: use the same Compose overlays as your deployment:
docker compose -f docker-compose.yml -f docker-compose.minio.yml run --rm worker python -m app.search_reindex_cli
# Rebuild all finished indexes after a repair (optional):
docker compose run --rm worker python -m app.search_reindex_cli --force --batch-size 25
```

Indexing caps a chapter at 4 MiB of Markdown and indexes at most the first 2 million normalized characters; oversized/unreadable chapters mark the book unavailable without affecting its ZIP. The command is resumable/idempotent: it indexes one book per transaction, continues past unavailable files, marks failures `ERROR`, and reports totals without printing private chapter text. Repair missing paths/objects and rerun; the default command retries pending/error books. The worker must have access to the historical local `outputs` volume and/or MinIO credentials. It does **not** re-upload PDFs, regenerate Markdown or call a VLM. For an API check, sign in and send a CSRF-protected `POST /api/v1/search` with JSON `{"query":"a word or phrase","limit":20}`; max query length is 100 and max limit 50. Do not put private terms in URL query parameters. Search uses Redis-backed limits of 60 requests/user/hour and 240 requests/IP/hour. **PostgreSQL/Compose smoke test is still required on a Docker host**; the sandbox has no Docker CLI.

### Use the web app

Open http://localhost:8080 and drop a PDF into the upload panel. Select a recent job to watch its **SSE progress** and stage history; polling also refreshes if the stream drops. Click a chapter to switch between rendered Markdown (including Mermaid, tables and images) and raw source, copy its Markdown, or download the ordered ZIP. `PARTIAL` jobs remain downloadable with original diagram images preserved; finished jobs can be deleted from the workspace. The interface shows a clear offline notice when the API is not running.

**Review fallback diagrams:** Deploy this feature with `docker compose up -d --build` (the `migrate` service creates the review table for new databases). On a newly converted `PARTIAL` job, click **Review diagrams** in the amber notice. Each fallback appears with its original image, PDF page and chapter. Sign in to your approved account to **Accept original image**, or **Edit as Mermaid** and save a diagram that passes the browser preview and server safety checks. Saving Mermaid changes only that occurrence, preserves a link to the original image, updates the chapter/manifest and publishes a new ZIP; accepting an untouched image records approval without changing its ZIP. You can revise a prior choice. The review count reaches zero when every item has been considered. Conversion status remains historically `PARTIAL`; **Review complete** means you have reviewed all image fallbacks, not that every image became Mermaid. Jobs converted *before this feature* lack item-level records; re-upload their PDFs for an exact review queue. No AI retry runs automatically. Edited ZIP revisions are retained in that job's output directory for safe in-flight downloads and removed when the job is deleted; repeated edits use additional disk space.

In local frontend development, run `cd frontend && npm ci && npm run dev`; Vite proxies relative `/api` and health URLs to an API running on port 8000. Full conversion still requires the backend, worker, Redis and PostgreSQL from Docker Compose. The preview server alone cannot process books.

**Upload returns 413?** The production nginx proxy has a `client_max_body_size 64m` limit, leaving multipart overhead above the API's default 50 MiB PDF limit. If a small PDF gets a 413, you may still be running an older `web` image with nginx's 1 MiB default. From the repository root, rebuild and recreate it with `docker compose up -d --build web`, then retry. To check the active configuration, run `docker compose exec web nginx -T` and look for `client_max_body_size 64m;`. If you change `PDF2MD_MAX_UPLOAD_BYTES` above 50 MiB, also raise `client_max_body_size` in `frontend/nginx.conf` above that value (allow extra room for multipart overhead) and rebuild `web`. API and proxy limits are separate.

### Convert a book through the API

The API requires an approved account. First POST `{"username":"admin","password":"…"}` to `/api/v1/auth/login`, retaining its HttpOnly cookie and the `csrf_token` in the JSON response. Send the cookie with every request and `X-CSRF-Token` with each POST/PUT/DELETE. Do not hardcode your real password in shell history. For example, after saving the cookie to `./session.cookies` and setting `CSRF` from the login response:

```bash
# Submit a PDF; the JSON response includes its job ID.
curl -b ./session.cookies -H "X-CSRF-Token: $CSRF" -F "file=@my-book.pdf" http://localhost:8080/api/v1/jobs

# Replace JOB_ID with that returned ID.
curl -b ./session.cookies -N http://localhost:8080/api/v1/jobs/JOB_ID/events
curl -b ./session.cookies http://localhost:8080/api/v1/jobs/JOB_ID
curl -b ./session.cookies http://localhost:8080/api/v1/jobs/JOB_ID/chapters
curl -b ./session.cookies http://localhost:8080/api/v1/jobs/JOB_ID/chapters/1/preview
curl -b ./session.cookies -L -o my-book_markdown.zip http://localhost:8080/api/v1/jobs/JOB_ID/download
```

The download endpoint responds with **409** until conversion succeeds or finishes `PARTIAL`, and **404** if no output file exists. `DELETE /api/v1/jobs/JOB_ID` removes the record, upload, output tree, and ZIP (only for finished jobs).

ZIP layout:

```text
chapters.json
01_Introduction/chapter.md
02_Getting_Started/chapter.md
assets/img_p3_x42.png
```

Images are linked from chapter files as `../assets/<filename>`. The manifest includes chapter titles, order, page ranges, and content counts. Extraction order: PDF bookmarks first, then font-size heading heuristics, then a full-document fallback; scanned pages use Tesseract OCR. A broken OCR pass fails the job rather than silently omitting a page. The current extractor uses layout heuristics; complex multi-column books, inline vector diagrams and borderless tables may need manual review. Large books can be slow because table detection is page-by-page.

## Architecture

```text
React browser / curl → nginx :8080 → FastAPI → PostgreSQL (jobs, stages, chapters, progress)
                                   │
                                   └── Celery queue (Redis) → worker
                                                            │
                                                            └── validate → detect chapters
                                                                → text/OCR → tables → images
                                                                → classify → VLM → Mermaid
                                                                → Markdown + manifest → ZIP
FastAPI SSE events ← Redis pub/sub and persisted progress events
```

Code follows a composition-root/ports-and-adapters structure: `IFileStore`, repository, OCR and `IVlmProvider` ports with local filesystem/optional private MinIO objects, SQLAlchemy, Tesseract and Ollama/OpenRouter/OpenAI/Anthropic adapters; separate stage classes and a local-first fallback chain with bounded HTTP requests and a transient-error retry. The orchestrator records stage results, typed errors and structured logs. Its `ContentBlock` format positions Mermaid at the source image's page coordinates; a soft diagram failure yields `PARTIAL`, not data loss.

## Development / verification

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy app
pytest -q
```

Tests include provider HTTP-contract and fallback tests, classifier/validator tests, SQLite-backed full-pipeline tests using generated PDFs, HTTP-level ZIP/image-asset API tests, and Vitest unit/component tests for upload, SSE, preview safety, Mermaid SVG sanitization and download links. CI runs backend lint/types/tests and frontend types/tests/production build/dependency audit. Docker and live Ollama/cloud credentials are unavailable in the authoring sandbox; **run the full Compose deployment and a real model conversion on a Docker host**.

## Roadmap

| Phase | Status | Content |
|---|---|---|
| 0 | ✅ | Repo skeleton, Docker Compose, CI |
| 1 | ✅ | Upload/jobs/SSE API, Celery plumbing, pipeline orchestrator |
| 2 | ✅ | Real chapter detection, text + OCR, table/image extraction, Markdown, manifest, ZIP download |
| 3 | ✅ | Embedded raster diagram classification + Mermaid; hybrid Ollama → OpenRouter → OpenAI → Anthropic, image fallback |
| 4 | ✅ | React frontend: upload, SSE progress, chapter Markdown/Mermaid preview, ZIP download, job management |
| 5 | locally implemented; Docker-host smoke test outstanding | Migrations, private approved accounts, limits, internal metrics, optional MinIO backend |
| 6 | implemented; user-confirmed deployment | Quality review, revisions and source-page comparison |
| 7 | locally implemented; Docker-host smoke test outstanding | Private searchable book library and resumable backfill |

## Configuration

Settings use the `PDF2MD_` prefix; see [`.env.example`](./.env.example). Notable settings: `PDF2MD_MAX_UPLOAD_BYTES` (50 MiB default), `PDF2MD_MAX_PAGES` (1000), `PDF2MD_DATABASE_URL`, `PDF2MD_REDIS_URL`, `PDF2MD_VLM_PROVIDER`, `PDF2MD_OLLAMA_BASE_URL`, `PDF2MD_OPENROUTER_API_KEY`, `PDF2MD_OPENROUTER_VISION_MODEL`, optional cloud keys and `PDF2MD_VLM_TIMEOUT_SECONDS`. Storage defaults to local Docker volumes; the separate `docker-compose.minio.yml` override enables private MinIO for new jobs.
