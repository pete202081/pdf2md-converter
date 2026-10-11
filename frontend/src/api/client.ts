import type { ApiErrorBody, DiagramReviewItem, EditableChapter, Job, JobDetail, QualitySummary, LibrarySearchResponse, ShelfBook, ShelfBookDetail, ReadingProgress, GithubPDF, GithubSearch, GithubBrowse } from './types';

export const JOBS_URL = '/api/v1/jobs';
let csrfToken: string | null = null;
export function setSessionCsrf(token: string | null) { csrfToken = token; }
export interface AuthSession { authenticated: boolean; username: string | null; role: 'ADMIN' | 'USER' | null; csrf_token: string | null }
export async function getAuthSession(): Promise<AuthSession> {
  let response: Response;
  try { response = await fetch('/api/v1/auth/session'); }
  catch { throw new Error('Cannot connect to the API. Start Docker Compose and try again.'); }
  if (!response.ok) throw new Error('Cannot connect to the API. Start Docker Compose and try again.');
  const session = await response.json() as AuthSession;
  setSessionCsrf(session.csrf_token);
  return session;
}
export async function authAction(path: string, body: object): Promise<Record<string, string>> {
  const response = await fetch(`/api/v1/auth/${path}`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const detail = result.detail;
    const validation = Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join('; ') : null;
    throw new Error((typeof detail === 'string' ? detail : validation)
      || (typeof result.message === 'string' ? result.message : `Request failed (${response.status})`));
  }
  return result as Record<string, string>;
}
export function changePassword(current_password: string, new_password: string): Promise<{ message: string }> {
  return request('/api/v1/auth/change-password', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current_password, new_password }) });
}
export async function signOut(): Promise<void> {
  await request('/api/v1/auth/logout', { method: 'POST' });
  setSessionCsrf(null);
}


export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly correlationId?: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function networkError(): ApiError {
  return new ApiError('Cannot connect to the API. Start Docker Compose and try again.', 0, 'NETWORK_ERROR');
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { ...init, headers: { Accept: 'application/json',
      ...(init?.method && !['GET', 'HEAD'].includes(init.method.toUpperCase()) && csrfToken ? { 'X-CSRF-Token': csrfToken } : {}), ...init?.headers } });
  } catch {
    throw networkError();
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as ApiErrorBody;
    throw new ApiError(body.message || `Request failed (${response.status})`, response.status,
      body.error_code || 'HTTP_ERROR', body.correlation_id);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function searchLibrary(query: string, signal?: AbortSignal): Promise<LibrarySearchResponse> {
  return request('/api/v1/search', { method: 'POST', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, limit: 30 }),
  });
}

export function listJobs(signal?: AbortSignal): Promise<Job[]> {
  return request<Job[]>(`${JOBS_URL}?limit=100`, { signal });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<JobDetail> {
  return request<JobDetail>(`${JOBS_URL}/${encodeURIComponent(jobId)}`, { signal });
}

export async function getChapterMarkdown(jobId: string, order: number, signal?: AbortSignal): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${JOBS_URL}/${encodeURIComponent(jobId)}/chapters/${order}/preview`, { signal });
  } catch {
    throw networkError();
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as ApiErrorBody;
    throw new ApiError(body.message || 'Could not load chapter', response.status, body.error_code || 'HTTP_ERROR');
  }
  return response.text();
}

export function getChapterForEdit(jobId: string, order: number): Promise<EditableChapter> {
  return request<EditableChapter>(`${JOBS_URL}/${encodeURIComponent(jobId)}/chapters/${order}/edit`);
}
export function saveChapterEdit(jobId: string, order: number, expected_hash: string, markdown: string): Promise<{ hash: string; message: string }> {
  return request(`${JOBS_URL}/${encodeURIComponent(jobId)}/chapters/${order}/edit`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_hash, markdown }),
  });
}
export function undoChapterEdit(jobId: string, order: number, expected_hash: string): Promise<{ hash: string; message: string }> {
  return request(`${JOBS_URL}/${encodeURIComponent(jobId)}/chapters/${order}/edit/undo`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_hash }),
  });
}

export function getQualitySummary(jobId: string): Promise<QualitySummary> {
  return request<QualitySummary>(`${JOBS_URL}/${encodeURIComponent(jobId)}/quality`);
}
export function getEditableChapter(jobId: string, order: number): Promise<EditableChapter> {
  return request<EditableChapter>(`${JOBS_URL}/${encodeURIComponent(jobId)}/quality/chapters/${order}`);
}
export function sourcePageUrl(jobId: string, order: number, page: number): string {
  return `${JOBS_URL}/${encodeURIComponent(jobId)}/quality/chapters/${order}/pages/${page}.png`;
}
export function reviewQualityPage(jobId: string, page: number): Promise<QualitySummary> {
  return request<QualitySummary>(`${JOBS_URL}/${encodeURIComponent(jobId)}/quality/pages/${page}`, { method: 'PUT' });
}
export function saveQualityChapter(jobId: string, order: number, expected_hash: string, markdown: string): Promise<{ hash: string; message: string }> {
  return request(`${JOBS_URL}/${encodeURIComponent(jobId)}/quality/chapters/${order}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_hash, markdown }),
  });
}
export function undoQualityChapter(jobId: string, order: number, expected_hash: string): Promise<{ hash: string; message: string }> {
  return request(`${JOBS_URL}/${encodeURIComponent(jobId)}/quality/chapters/${order}/undo`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_hash }),
  });
}

export function stopJob(jobId: string): Promise<{ status: string }> {
  return request(`${JOBS_URL}/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
}

export function deleteJob(jobId: string): Promise<void> {
  return request<void>(`${JOBS_URL}/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
}

export function downloadUrl(jobId: string): string {
  return `${JOBS_URL}/${encodeURIComponent(jobId)}/download`;
}

export function eventsUrl(jobId: string): string {
  return `${JOBS_URL}/${encodeURIComponent(jobId)}/events`;
}

// The server only exposes generated image filenames inside this job's assets/.
const ASSET_RE = /^(?:\.\.\/)?assets\/([A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif|bmp|tiff?|jpx|jp2))$/i;
export function resolveAssetUrl(url: string, jobId: string): string | null {
  const match = ASSET_RE.exec(url);
  return match ? `${JOBS_URL}/${encodeURIComponent(jobId)}/assets/${encodeURIComponent(match[1])}` : null;
}

export function validatePdf(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.pdf')) return 'Please choose a PDF file.';
  if (file.size === 0) return 'This file is empty. Please choose another PDF.';
  return null;
}

export function uploadJob(
  file: File,
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', JOBS_URL);
    if (csrfToken) xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => {
      const body = xhr.response as Job | ApiErrorBody | null;
      if (xhr.status === 201) resolve(body as Job);
      else if (xhr.status === 413) {
        // Nginx can return an HTML error page, so responseType=json is null.
        reject(new ApiError(
          'The upload was rejected as too large. The default PDF limit is 50 MiB. If this file is smaller, rebuild the web container to apply the Nginx limit; otherwise use a smaller PDF or raise both limits.',
          413, 'UPLOAD_TOO_LARGE',
        ));
      } else {
        const error = body as ApiErrorBody | null;
        reject(new ApiError(error?.message || `Upload failed (${xhr.status})`, xhr.status,
          error?.error_code || 'UPLOAD_ERROR', error?.correlation_id));
      }
    };
    xhr.onerror = () => reject(networkError());
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    const abort = () => xhr.abort();
    signal?.addEventListener('abort', abort, { once: true });
    xhr.onloadend = () => signal?.removeEventListener('abort', abort);
    if (signal?.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });
}


export function listDiagramReviews(jobId: string, signal?: AbortSignal): Promise<DiagramReviewItem[]> {
  return request<DiagramReviewItem[]>(`${JOBS_URL}/${encodeURIComponent(jobId)}/reviews`, { signal });
}


export function decideDiagramReview(
  jobId: string, itemId: string, decision: 'ACCEPTED_IMAGE' | 'EDITED_MERMAID',
  csrf: string, mermaidCode?: string,
): Promise<DiagramReviewItem> {
  return request<DiagramReviewItem>(
    `${JOBS_URL}/${encodeURIComponent(jobId)}/reviews/${encodeURIComponent(itemId)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ decision, ...(mermaidCode !== undefined ? { mermaid_code: mermaidCode } : {}) }) },
  );
}

// Bookshelves: public reads never require a session; writes use the shared CSRF request.
const SHELVES = '/api/v1/shelves';
export function publicShelf(page = 1): Promise<{ books: ShelfBook[]; page: number }> {
  return request(`${SHELVES}/public?page=${page}`);
}
export function privateShelf(): Promise<ShelfBook[]> { return request(`${SHELVES}/mine`); }
export function shelfEligibleJobs(): Promise<{ id: string; filename: string }[]> { return request(`${SHELVES}/eligible-jobs`); }
export function shelfDetail(id: string, scope: 'public' | 'mine'): Promise<ShelfBookDetail> {
  return request(`${SHELVES}/${scope}/${encodeURIComponent(id)}`);
}
export function shelfChapterUrl(id: string, chapter: number, scope: 'public' | 'mine'): string {
  return `${SHELVES}/${scope}/${encodeURIComponent(id)}/chapters/${chapter}`;
}
export function shelfAssetUrl(id: string, name: string, scope: 'public' | 'mine'): string {
  return `${SHELVES}/${scope}/${encodeURIComponent(id)}/assets/${encodeURIComponent(name)}`;
}
export async function shelfMarkdown(id: string, chapter: number, scope: 'public' | 'mine', signal?: AbortSignal): Promise<string> {
  const response = await fetch(shelfChapterUrl(id, chapter, scope), { signal });
  if (!response.ok) throw new ApiError('Chapter unavailable', response.status, 'CHAPTER_UNAVAILABLE');
  return response.text();
}
export function addConvertedToShelf(jobId: string): Promise<ShelfBookDetail> {
  return request(`${SHELVES}/from-job/${encodeURIComponent(jobId)}`, { method: 'POST' });
}
export async function importShelfBook(file: File, title: string): Promise<ShelfBookDetail> {
  const body = new FormData(); body.append('file', file);
  if (file.name.toLowerCase().endsWith('.md')) body.append('title', title);
  return request(`${SHELVES}/import`, { method: 'POST', body });
}
export function updateShelfBook(id: string, changes: Partial<Pick<ShelfBook, 'visibility' | 'title' | 'author' | 'description'>>): Promise<ShelfBookDetail> {
  return request(`${SHELVES}/mine/${encodeURIComponent(id)}`, { method: 'PATCH',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) });
}
export function removeShelfBook(id: string): Promise<void> {
  return request(`${SHELVES}/mine/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
export function readProgress(id: string): Promise<ReadingProgress> {
  return request(`${SHELVES}/reading/${encodeURIComponent(id)}`);
}
export function saveReadProgress(id: string, chapter_id: number, completed?: boolean): Promise<ReadingProgress> {
  return request(`${SHELVES}/reading/${encodeURIComponent(id)}`, { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chapter_id, ...(completed === undefined ? {} : { completed }) }) });
}
export function resetReadProgress(id: string): Promise<void> {
  return request(`${SHELVES}/reading/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function githubSearch(query: string, page = 1): Promise<GithubSearch> {
  return request(`/api/v1/github/search?query=${encodeURIComponent(query)}&page=${page}`);
}
export function githubBrowse(repo: string, directory = ''): Promise<GithubBrowse> {
  return request('/api/v1/github/browse', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, directory }) });
}
export function githubPrivateRepositories(page = 1): Promise<{ repo: string; private: boolean }[]> {
  return request(`/api/v1/github/private-repositories?page=${page}`);
}
export function githubConvert(file: GithubPDF): Promise<Job> {
  return request('/api/v1/github/convert', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo: file.repo, path: file.path, revision: file.revision, sha: file.sha }) });
}
