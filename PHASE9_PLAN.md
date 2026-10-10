# Proposed Phase 9 — Find GitHub PDFs and convert without local upload

Status: **approved and locally implemented**. Live GitHub PAT, Docker/PostgreSQL and MinIO smoke tests remain for the deployment host.

## Confirmed choices

- Discover PDFs from **public GitHub repositories** and from **private repositories accessible to an administrator-managed PAT**. Only administrators may see/browse/convert private-repository PDFs; all approved users may browse public results. Guests have no GitHub search/import access.
- One **shared server-side PAT** is configured by the administrator, not collected from readers. Users review a result's repository, path, size and link, then explicitly select **Convert**; selection is not an automatic import or publication.
- GitHub Code Search **does not index PDF/binary files**. Therefore search is *best-effort repository discovery*: search relevant repository names/descriptions/topics, inspect a small bounded set of repository trees for `.pdf` paths, and also offer direct `owner/repository` browsing (including private repos for administrators). It is not a global exhaustive filename search.

## Product flow

1. Keep the existing **Upload PDF** panel intact. Add an adjacent **Find on GitHub** tab on the signed-in conversion workspace, with search input, public/private scope indicator, and an optional exact `owner/repository` browse field. The private scope and private-repository list are admin-only.
2. Public search displays matching repository PDF files, their repository name, path/filename, byte size where GitHub supplies it, and a GitHub source link. Show incomplete/limited-results notices rather than claiming all GitHub PDFs were searched. Paginate repository candidates; allow browsing folders in a selected repository. No PAT is sent to the browser or included in links.
3. Opening a result shows a confirmation/review view and the existing configured PDF size/page limits. **Convert** asks the backend to fetch the selected immutable Git revision and file and create a normal private job for the signed-in user. The existing upload/validation/storage/Celery pipeline then handles OCR, progress, review, ZIP, search indexing and optional local/MinIO storage. It does not automatically add the conversion to a shelf or publish it.
4. A PDF over the configured upload limit, an unavailable or changed file, inaccessible repository, unsupported LFS pointer, or invalid PDF produces a clear non-sensitive error and no queued conversion. Display link to the source for the user's own rights assessment; no recommendation that all ebooks are licensed for copying/conversion.

## Integration design

- Extend the existing encrypted `RuntimeSetting` secret mechanism with `github_pat`, with an optional `PDF2MD_GITHUB_PAT` environment default. Admin Settings allows set/replace/clear and a **Test GitHub access** action; responses expose only configured/connection status, never the token or API authorization headers. No new service or new container is required. The encrypted key-file volume must be included in backups.
- Introduce a small GitHub adapter (httpx, fixed `https://api.github.com`, strict response size/timeouts, typed results and error mapping) behind a discovery/import service. Repository search uses GitHub's REST repository search with server-enforced public filtering. A private-repository list uses authenticated `GET /user/repos?visibility=private`, accessible only through an admin route. Repository browsing uses GitHub's contents/tree APIs with bounded directories and explicit warnings when truncated. A best-effort search may inspect up to **5 repository trees per search page**, a bounded number of entries/bytes per tree, and a limited page count; it does not crawl GitHub broadly. Direct browsing is the fallback when search misses a PDF, notably when the name occurs only in the PDF path.
- Routes under `/api/v1/github`: authenticated public search and repository browse, admin-only private list/browse, and a POST conversion route. Server-side authorization is repeated for conversion: retrieve repository metadata from GitHub and reject private repos unless the caller is an administrator. Never accept an arbitrary URL or a browser-supplied `download_url`; validate repository, path and revision identifiers, retrieve only from fixed GitHub endpoints, and verify the selected file's current Git metadata/expected blob SHA. Pinned revision avoids converting a moving branch. Whitelist any unavoidable HTTPS download redirect targets and strip Authorization on cross-host redirects; never forward the PAT to an arbitrary host.
- Stream downloaded bytes into the existing upload service via a shared validation/storage entry point rather than duplicating PDF checks. Enforce size both from metadata and during streaming, PDF magic and page count, cleanup of partial files/objects, and the existing per-user upload quota. Add tighter per-user/IP GitHub search/browse limits and handle GitHub 401/403/404/429 or secondary-limit responses without leaking private repo names or credentials. Use CSRF protection on conversion and admin PAT changes. If GitHub serves an LFS pointer rather than PDF bytes, reject explicitly in this iteration instead of treating it as an uploaded PDF.
- Add optional private job provenance fields (`source_type`, repository, path and revision; no token) in an additive migration. Source metadata is visible only via existing job owner/admin routes, never public shelf or public search endpoints. An imported GitHub PDF is stored as an independent private job; deleting it uses existing job cleanup; GitHub updates cannot silently change its results.

## Privacy/security boundaries

- Because the shared PAT may read private repositories, **ordinary approved users must never receive any private-repo metadata or file content**, even if they guess identifiers, paths, SHAs or manipulate scope/query parameters. Restrict private endpoints before invoking GitHub and repeat role/visibility checks at conversion. Confirm that public search responses contain only repositories GitHub marks public.
- Secret redaction for logs, exception text, OpenAPI examples and response payloads. No direct browser fetch to GitHub with a PAT. Bounded network I/O, redirects, query/path validation and rate limits prevent SSRF and unbounded scanning. Publish operations remain separate and opt-in under Phase 8 rules.
- Recommend a fine-grained PAT with read-only **Contents** on only intended repositories (plus GitHub's required Metadata access), an expiry, and no write/admin permission. An admin should understand that enabling private access means this backend can fetch those private files. Revoke/rotate the PAT through Settings and GitHub as necessary.

## Verification and release

- Adapter tests with mocked GitHub responses: public-only filtering, admin-only private list/browse/import, guessed private identifiers denied, tree truncation, pagination, missing PDFs, unavailable files, SHA mismatch, changed default branch, malicious paths/redirects, JSON size caps, GitHub limits and 401/403/404/429 mapping, oversized/bad PDFs, LFS pointers, timeouts and cleanup.
- Integration tests: existing upload remains unchanged; selected GitHub PDF produces a private job owned by the requester using local and mocked MinIO storage; duplicate selections create separate jobs; no job on validation failure; no PAT/source details in guest or public shelf endpoints. Frontend tests: search/browse/review/convert, permissions and failure states, accessible controls and mobile layout.
- Run backend Ruff/mypy/pytest, frontend typecheck/Vitest/build/audit, migration checks, document PAT setup and operator smoke steps, and package a Phase 9 archive. Live GitHub/PAT and Docker/PostgreSQL/MinIO smoke tests require the deployment host; do not claim them from the authoring environment.

## Limitations and approval

This is **not** a global search of GitHub PDF contents/filenames. GitHub excludes PDFs from code search, repository search is relevance-ranked and limited, very large/truncated trees may need manual folder browsing, and inaccessible/private repos not granted to the PAT cannot be searched. LFS-hosted PDFs and release assets are out of scope for the first version; a direct repository file is required. The repository's licensing remains the user's responsibility. No arbitrary-URL PDF import, automatic shelf publication, or background indexing/crawling of all accessible repos.

**Approved by the owner** after confirming the scope, admin-managed PAT, review-before-convert, administrator-only private access, and bounded repository discovery.

## GitHub API references

- GitHub Code Search excludes binary files including PDFs: https://docs.github.com/en/search-github/github-code-search/about-github-code-search
- Git trees may truncate at 100,000 entries / 7 MB: https://docs.github.com/en/rest/git/trees
- Repository contents endpoint and size/permission constraints: https://docs.github.com/en/rest/repos/contents
- PAT-accessible repository listing: https://docs.github.com/en/rest/repos/repos
- Search rate/scope limits: https://docs.github.com/en/rest/search/search
