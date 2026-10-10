# PDF2MD Converter

A Docker Compose application that converts PDF eBooks into **chapter-wise Markdown**: text (including OCR for image-only pages), Markdown tables, linked images, and likely embedded diagrams transcribed as **Mermaid** code blocks. Output includes ordered chapter folders (`01_…`, `02_…`), a `chapters.json` manifest, and a downloadable ZIP. The React frontend lets you upload books, follow live conversion progress, browse chapters with rendered Mermaid diagrams, and download the result.

See [ARCHITECTURE_PLAN.md](./ARCHITECTURE_PLAN.md) for the approved design and locked decisions.

## Quickstart

Requires Docker and Docker Compose v2.24+ (for optional `env_file` syntax).

```bash
# From the repository root
cp .env.example .env       # optional; edit limits and other settings as needed
docker compose up --build
```

- React app (upload, progress, preview, ZIP): http://localhost:8080
- Swagger API docs: http://localhost:8080/docs
- Health checks: http://localhost:8080/healthz and http://localhost:8080/readyz

Docker starts nginx (`web`, serving the built React app), FastAPI (`api`), a Celery worker, Redis and PostgreSQL. Uploads, generated output and database data persist in named Docker volumes. The backend image includes Tesseract for scanned PDFs. For diagram conversion, the default **hybrid** mode tries local Ollama first, then whichever cloud keys are configured; if none work, the original image is retained and the job becomes `PARTIAL`. Ordinary non-diagram images do not require a VLM.

### First-time admin setup and provider configuration

1. **Start the application** from the repository root on your Docker host. Then generate a one-time administrator setup code:

   ```bash
   docker compose up -d --build
   docker compose exec api python -m app.admin_cli
   ```

   The second command prints a private setup code valid for **30 minutes**. If it expires, run that command again to generate a new one. You need the code only when creating the first administrator account; if an account already exists, sign in instead.

2. **Create the admin password:** open http://localhost:8080, select **Settings**, enter the printed setup code and choose a password of **at least 12 characters**. Select **Create account**. Do not share the code or password.
3. **Configure a provider:** in Settings, choose a provider mode and enter a reachable Ollama URL/model or an OpenRouter, OpenAI or Anthropic key and vision model. Click **Save settings** *before* clicking **Test saved configuration**. Cloud tests may incur a small charge; the Ollama test checks its `/api/tags` endpoint and configured model. Starting the optional Ollama Docker service still requires `docker compose --profile local-vlm up -d` (and allowing its model download to finish).
4. **Try a new conversion:** return to **Workspace** and upload the PDF again. Saved settings apply to **new uploads and conversions without further rebuilds or restarts**; existing or running jobs are not retried. PDFs converted before item-level diagram review was added must also be re-uploaded to populate the review queue.

The same Settings page also controls VLM timeout, maximum pages and PDF size (up to 50 MiB). If you later lose the admin password, run `docker compose exec api python -m app.admin_cli reset-password` from a trusted Docker host; this resets the password and signs out existing admin sessions.

Provider keys are encrypted in PostgreSQL with a key in the `settings_keys` Docker volume. Keep **both** `db_data` and `settings_keys` in backups: deleting the latter makes saved keys unrecoverable. Keys are never returned by the Settings API; blank means *keep*, **Clear saved key** means *remove*. `.env` provider values continue to work as defaults until overridden from the UI. Database, Redis, storage paths, nginx's 64 MiB request limit and starting the Ollama Compose service remain Docker/infrastructure configuration. The admin session is HttpOnly/SameSite and settings changes require a CSRF token; use HTTPS when accessing the app remotely. **Settings and diagram review changes require admin login**—uploads, job listings, read-only reviews and downloads remain accessible to anyone who can reach this deployment, so do not expose port 8080 publicly without access control at your reverse proxy.

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

### Use the web app

Open http://localhost:8080 and drop a PDF into the upload panel. Select a recent job to watch its **SSE progress** and stage history; polling also refreshes if the stream drops. Click a chapter to switch between rendered Markdown (including Mermaid, tables and images) and raw source, copy its Markdown, or download the ordered ZIP. `PARTIAL` jobs remain downloadable with original diagram images preserved; finished jobs can be deleted from the workspace. The interface shows a clear offline notice when the API is not running.

**Review fallback diagrams:** Deploy this feature with `docker compose up -d --build` (the API creates the new review table at startup). On a newly converted `PARTIAL` job, click **Review diagrams** in the amber notice. Each fallback appears with its original image, PDF page and chapter. Sign in under **Settings** to **Accept original image**, or **Edit as Mermaid** and save a diagram that passes the browser preview and server safety checks. Saving Mermaid changes only that occurrence, preserves a link to the original image, updates the chapter/manifest and publishes a new ZIP; accepting an untouched image records approval without changing its ZIP. You can revise a prior choice. The review count reaches zero when every item has been considered. Conversion status remains historically `PARTIAL`; **Review complete** means you have reviewed all image fallbacks, not that every image became Mermaid. Jobs converted *before this feature* lack item-level records; re-upload their PDFs for an exact review queue. No AI retry runs automatically. Edited ZIP revisions are retained in that job's output directory for safe in-flight downloads and removed when the job is deleted; repeated edits use additional disk space.

In local frontend development, run `cd frontend && npm ci && npm run dev`; Vite proxies relative `/api` and health URLs to an API running on port 8000. Full conversion still requires the backend, worker, Redis and PostgreSQL from Docker Compose. The preview server alone cannot process books.

**Upload returns 413?** The production nginx proxy has a `client_max_body_size 64m` limit, leaving multipart overhead above the API's default 50 MiB PDF limit. If a small PDF gets a 413, you may still be running an older `web` image with nginx's 1 MiB default. From the repository root, rebuild and recreate it with `docker compose up -d --build web`, then retry. To check the active configuration, run `docker compose exec web nginx -T` and look for `client_max_body_size 64m;`. If you change `PDF2MD_MAX_UPLOAD_BYTES` above 50 MiB, also raise `client_max_body_size` in `frontend/nginx.conf` above that value (allow extra room for multipart overhead) and rebuild `web`. API and proxy limits are separate.

### Convert a book through the API

```bash
# Submit a PDF; the JSON response includes its job ID.
curl -F "file=@my-book.pdf" http://localhost:8080/api/v1/jobs

# Replace JOB_ID with that returned ID.
curl -N http://localhost:8080/api/v1/jobs/JOB_ID/events
curl http://localhost:8080/api/v1/jobs/JOB_ID
curl http://localhost:8080/api/v1/jobs/JOB_ID/chapters
curl http://localhost:8080/api/v1/jobs/JOB_ID/chapters/1/preview
curl -L -o my-book_markdown.zip http://localhost:8080/api/v1/jobs/JOB_ID/download
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

Code follows a composition-root/ports-and-adapters structure: `IFileStore`, repository, OCR and `IVlmProvider` ports with local/SQLAlchemy/Tesseract and Ollama/OpenRouter/OpenAI/Anthropic adapters; separate stage classes and a local-first fallback chain with bounded HTTP requests and a transient-error retry. The orchestrator records stage results, typed errors and structured logs. Its `ContentBlock` format positions Mermaid at the source image's page coordinates; a soft diagram failure yields `PARTIAL`, not data loss.

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
| 5 | next | Production hardening, migrations, security/rate limits, metrics, optional MinIO profile |

## Configuration

Settings use the `PDF2MD_` prefix; see [`.env.example`](./.env.example). Notable settings: `PDF2MD_MAX_UPLOAD_BYTES` (50 MiB default), `PDF2MD_MAX_PAGES` (1000), `PDF2MD_DATABASE_URL`, `PDF2MD_REDIS_URL`, `PDF2MD_VLM_PROVIDER`, `PDF2MD_OLLAMA_BASE_URL`, `PDF2MD_OPENROUTER_API_KEY`, `PDF2MD_OPENROUTER_VISION_MODEL`, optional cloud keys and `PDF2MD_VLM_TIMEOUT_SECONDS`. Storage is local Docker volumes; no S3 profile is shipped yet.
