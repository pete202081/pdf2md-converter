import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { setSessionCsrf } from '../api/client';
import type { QualitySummary } from '../api/types';
import QualityReviewPanel from './QualityReviewPanel';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setSessionCsrf(null); });

it('compares a private source page and saves corrected chapter text with CSRF', async () => {
  setSessionCsrf('csrf-owner');
  const summary: QualitySummary = { inventory_ready: true, chapters: [{ order: 1, source_pages: [1, 2] }], flags: [
    { id: 1, page_number: 1, chapter_order: 1, code: 'OCR_USED', message: 'Scanned page: OCR fallback was used', reviewed_at: null },
  ] };
  let markdown = '# One\n\nOriginal text.';
  let saved = false;
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/quality') && !init?.method) return ok(summary);
    if (url.endsWith('/quality/chapters/1') && !init?.method) return ok({ markdown, hash: saved ? 'b' : 'a', can_undo: saved });
    if (url.endsWith('/quality/chapters/1') && init?.method === 'PUT') {
      expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-owner');
      expect(JSON.parse(init.body as string).expected_hash).toBe('a');
      markdown = JSON.parse(init.body as string).markdown; saved = true;
      return ok({ hash: 'b', message: 'Chapter saved' });
    }
    if (url.endsWith('/quality/pages/1') && init?.method === 'PUT') {
      summary.flags[0].reviewed_at = '2026-10-10';
      return ok(summary);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  const onChanged = vi.fn();
  render(<QualityReviewPanel jobId="job-one" initialChapterOrder={1} onChanged={onChanged} />);
  await screen.findByText('Original text.');
  expect(screen.getByAltText('Original PDF page 1').getAttribute('src')).toBe('/api/v1/jobs/job-one/quality/chapters/1/pages/1.png');
  fireEvent.click(screen.getByRole('button', { name: 'Mark page reviewed' }));
  await screen.findByText('Page 1 marked reviewed.');
  fireEvent.click(screen.getByRole('button', { name: 'Edit text & tables' }));
  fireEvent.change(screen.getByLabelText('Markdown source'), { target: { value: '# One\n\nCorrected text.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save chapter & ZIP' }));
  await screen.findByText('Chapter saved. The updated ZIP is ready to download.');
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(markdown).toContain('Corrected text.');
  expect(screen.getByRole('button', { name: 'Undo last edit' })).toBeTruthy();
});

it('does not mistake an old conversion for a clean inventory', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({ inventory_ready: false, chapters: [], flags: [] })));
  render(<QualityReviewPanel jobId="old-job" initialChapterOrder={1} onChanged={vi.fn()} />);
  await screen.findByText(/older conversion has no quality-warning inventory/i);
  expect(screen.getByText(/No source-page mapping is available/i)).toBeTruthy();
});

it('keeps a draft on a stale-version conflict so the user can reload deliberately', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/quality') && !init?.method) return ok({ inventory_ready: true, chapters: [{ order: 1, source_pages: [1, 1] }], flags: [] });
    if (url.endsWith('/quality/chapters/1') && !init?.method) return ok({ markdown: '# First', hash: 'old', can_undo: false });
    if (url.endsWith('/quality/chapters/1') && init?.method === 'PUT') return { ok: false, status: 409, json: async () => ({ message: 'Chapter changed while you were editing; reload before saving' }) };
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<QualityReviewPanel jobId="job-stale" initialChapterOrder={1} onChanged={vi.fn()} />);
  await screen.findByText('First');
  fireEvent.click(screen.getByRole('button', { name: 'Edit text & tables' }));
  fireEvent.change(screen.getByLabelText('Markdown source'), { target: { value: '# My correction' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save chapter & ZIP' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('reload before saving');
  expect((screen.getByLabelText('Markdown source') as HTMLTextAreaElement).value).toBe('# My correction');
});
