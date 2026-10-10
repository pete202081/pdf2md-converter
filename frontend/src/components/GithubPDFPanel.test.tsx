import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import GithubPDFPanel from './GithubPDFPanel';
import { setSessionCsrf } from '../api/client';

const pdf = { repo: 'books/guide', path: 'chapter.pdf', sha: 'a'.repeat(40), revision: 'b'.repeat(40), size: 2400, url: 'https://github.com/books/guide/blob/rev/chapter.pdf' };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setSessionCsrf(null); });

it('lets a signed-in user review a discovered PDF before queuing it', async () => {
  const onConverted = vi.fn();
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/v1/github/search')) return { ok: true, json: async () => ({ repositories: [{ repo: 'books/guide', files: [pdf], incomplete: false }], page: 1, has_more: false, incomplete: false }) };
    if (url === '/api/v1/github/convert' && init?.method === 'POST') return { ok: true, status: 201, json: async () => ({ id: 'job-new', filename: 'chapter.pdf' }) };
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  setSessionCsrf('csrf-reader');
  render(<GithubPDFPanel admin={false} onConverted={onConverted} />);
  expect(screen.queryByText('Show my PAT-accessible private repositories')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Search repository topics'), { target: { value: 'guide' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  await screen.findByText('chapter.pdf');
  expect(onConverted).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /Review PDF/i }));
  expect(screen.getByText(/This creates a private job/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Convert selected PDF' }));
  await waitFor(() => expect(onConverted).toHaveBeenCalledWith(expect.objectContaining({ id: 'job-new' })));
  expect(fetcher).toHaveBeenCalledWith('/api/v1/github/convert', expect.objectContaining({ headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-reader' }) }));
});

it('shows an admin-only private repository list and browse action', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.startsWith('/api/v1/github/private-repositories')) return { ok: true, json: async () => [{ repo: 'books/secret', private: true }] };
    if (url === '/api/v1/github/browse') return { ok: true, json: async () => ({ repo: 'books/secret', directory: '', files: [], folders: ['research'], private: true, incomplete: false }) };
    return { ok: false, status: 404, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetcher);
  render(<GithubPDFPanel admin onConverted={vi.fn()} />);
  fireEvent.click(screen.getByText('Show my PAT-accessible private repositories'));
  await screen.findByRole('button', { name: 'books/secret' });
  fireEvent.click(screen.getByRole('button', { name: 'books/secret' }));
  await screen.findByRole('heading', { name: /Private · books\/secret/ });
});
