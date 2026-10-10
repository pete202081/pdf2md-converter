import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, deleteJob, getChapterMarkdown, getJob, listJobs, resolveAssetUrl, uploadJob, validatePdf } from './client';

const fakeResponse = (status: number, body: unknown, contentType = 'json') => ({
  ok: status >= 200 && status < 300,
  status,
  json: vi.fn().mockResolvedValue(body),
  text: vi.fn().mockResolvedValue(contentType === 'text' ? body : JSON.stringify(body)),
});

afterEach(() => vi.unstubAllGlobals());

describe('API client', () => {
  it('uses relative API URLs and returns jobs/detail', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(fakeResponse(200, [{ id: 'job-1' }]))
      .mockResolvedValueOnce(fakeResponse(200, { id: 'job-1', chapters: [] }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await listJobs()).toEqual([{ id: 'job-1' }]);
    expect(await getJob('job-1')).toMatchObject({ id: 'job-1' });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/jobs?limit=100');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/jobs/job-1');
  });

  it('exposes API error messages, codes and correlation IDs', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse(409, {
      message: 'Job not ready', error_code: 'CONFLICT', correlation_id: 'trace-1',
    })));
    await expect(getJob('bad')).rejects.toMatchObject({
      message: 'Job not ready', status: 409, code: 'CONFLICT', correlationId: 'trace-1',
    });
  });

  it('handles unreachable API without leaking fetch internals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('socket refused')));
    await expect(listJobs()).rejects.toMatchObject({ code: 'NETWORK_ERROR', status: 0 });
  });

  it('loads raw chapter text and supports delete 204', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(fakeResponse(200, '# Hello', 'text'))
      .mockResolvedValueOnce(fakeResponse(204, null));
    vi.stubGlobal('fetch', fetchMock);
    expect(await getChapterMarkdown('job-1', 2)).toBe('# Hello');
    await expect(deleteJob('job-1')).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE');
  });

  it('limits asset URLs to generated filenames for this job', () => {
    expect(resolveAssetUrl('../assets/img_p3_x42.png', 'job-1'))
      .toBe('/api/v1/jobs/job-1/assets/img_p3_x42.png');
    expect(resolveAssetUrl('assets/photo.jpg', 'job-1'))
      .toBe('/api/v1/jobs/job-1/assets/photo.jpg');
    for (const url of ['../chapters.json', '../assets/../../etc/passwd', 'javascript:alert(1)', 'https://evil.test/image.png']) {
      expect(resolveAssetUrl(url, 'job-1')).toBeNull();
    }
  });

  it('validates uploads before they reach the API', () => {
    expect(validatePdf(new File(['pdf'], 'my-book.PDF'))).toBeNull();
    expect(validatePdf(new File(['abc'], 'notes.txt'))).toMatch(/PDF/);
    expect(validatePdf(new File([], 'empty.pdf'))).toMatch(/empty/);
  });

  it('uploads a multipart file and reports upload progress', async () => {
    const calls: string[] = [];
    class FakeXHR {
      status = 201;
      response = { id: 'job-queued' };
      responseType = '';
      upload = { onprogress: (_event: { lengthComputable: boolean; loaded: number; total: number }) => {} };
      onload = () => {};
      onerror = () => {};
      onabort = () => {};
      onloadend = () => {};
      open(method: string, url: string) { calls.push(`${method} ${url}`); }
      send(form: FormData) {
        expect(form.get('file')).toBeInstanceOf(File);
        this.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
        this.onload();
        this.onloadend();
      }
      abort() { this.onabort(); }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
    const progress = vi.fn();
    const job = await uploadJob(new File(['%PDF'], 'book.pdf'), progress);
    expect(job.id).toBe('job-queued');
    expect(progress).toHaveBeenCalledWith(50);
    expect(calls).toEqual(['POST /api/v1/jobs']);
  });

  it('explains a proxy 413 even when Nginx returns HTML instead of JSON', async () => {
    class ProxyRejectXHR {
      status = 413;
      response = null;
      responseType = '';
      upload = { onprogress: () => {} };
      onload = () => {};
      onerror = () => {};
      onabort = () => {};
      onloadend = () => {};
      open() {}
      send() { this.onload(); this.onloadend(); }
      abort() { this.onabort(); }
    }
    vi.stubGlobal('XMLHttpRequest', ProxyRejectXHR);
    await expect(uploadJob(new File(['%PDF'], 'book.pdf'), vi.fn())).rejects.toMatchObject({
      status: 413, code: 'UPLOAD_TOO_LARGE',
      message: expect.stringMatching(/upload was rejected as too large.*rebuild the web container.*raise both limits/i),
    });
  });

  it('keeps ApiError a typed Error', () => {
    expect(new ApiError('bad', 400, 'INVALID')).toBeInstanceOf(Error);
  });
});
