import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ShelvesPage from './ShelvesPage';
import ShelfReader from './ShelfReader';
import { setSessionCsrf } from '../api/client';

const id = 'ab'.repeat(16);
const book = { id, title: 'A Green Garden', author: 'Owner', description: null,
  visibility: 'PUBLIC', source_kind: 'MARKDOWN_IMPORT', chapter_count: 2,
  created_at: '2026-10-10T12:00:00Z' };
const detail = { ...book, chapters: [{ id: 11, title: 'First', order_index: 1 }, { id: 12, title: 'Second', order_index: 2 }] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setSessionCsrf(null); window.location.hash = ''; });

describe('guest shelves and secure reader', () => {
  it('lets guests browse and read without requesting or storing progress', async () => {
    window.location.hash = '#shelves';
    const mock = vi.fn().mockImplementation(async (url: string) => {
      if (url === '/api/v1/shelves/public?page=1') return { ok: true, json: async () => ({ books: [book], page: 1 }) };
      if (url === `/api/v1/shelves/public/${id}`) return { ok: true, json: async () => detail };
      if (url.endsWith('/chapters/11')) return { ok: true, text: async () => '# First\n\n![Allowed](../assets/rose.png)\n![Tracking](https://evil.test/pixel.png)\n<script>bad</script>' };
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', mock);
    render(<ShelvesPage auth={null} />);
    await waitFor(() => expect(screen.getByRole('link', { name: `Read ${book.title}` })).toBeInTheDocument());
    expect(screen.queryByText('My shelf')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: `Read ${book.title}` }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'First' })).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('img', { name: 'Allowed' })).toHaveAttribute('src', `/api/v1/shelves/public/${id}/assets/rose.png`));
    expect(screen.queryByRole('img', { name: 'Tracking' })).not.toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    expect(mock.mock.calls.every(([url]) => !String(url).includes('/reading/'))).toBe(true);
  });

  it('adds a converted book privately and confirms before public publishing', async () => {
    window.location.hash = '#shelves';
    const privateBook = { ...book, visibility: 'PRIVATE' };
    let books: typeof book[] = [];
    const mock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/v1/shelves/public?page=1') return { ok: true, json: async () => ({ books: [], page: 1 }) };
      if (url === '/api/v1/shelves/mine') return { ok: true, json: async () => books };
      if (url === '/api/v1/shelves/eligible-jobs') return { ok: true, json: async () => [{ id: 'job-one', filename: 'Garden.pdf', status: 'SUCCEEDED' }] };
      if (url === '/api/v1/shelves/from-job/job-one' && init?.method === 'POST') { books = [privateBook]; return { ok: true, json: async () => detail }; }
      if (url === `/api/v1/shelves/mine/${id}` && init?.method === 'PATCH') { books = [book]; return { ok: true, json: async () => detail }; }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', mock);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    setSessionCsrf('owner-csrf');
    render(<ShelvesPage auth={{ authenticated: true, username: 'owner', role: 'USER', csrf_token: 'owner-csrf' }} />);
    fireEvent.click(screen.getByRole('button', { name: /My shelf/ }));
    await waitFor(() => expect(screen.getByRole('option', { name: 'Garden.pdf' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Choose finished conversion'), { target: { value: 'job-one' } });
    fireEvent.click(screen.getByRole('button', { name: /Add converted book/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Publish publicly' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Publish publicly' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Unpublish' })).toBeInTheDocument());
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('readable by anyone'));
    expect(mock).toHaveBeenCalledWith(`/api/v1/shelves/mine/${id}`, expect.objectContaining({ method: 'PATCH',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'owner-csrf' }),
    }));
    vi.mocked(window.confirm).mockRestore();
  });

  it('saves only a signed-in reader’s position and explicit completion', async () => {
    const mock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === `/api/v1/shelves/public/${id}`) return { ok: true, json: async () => detail };
      if (url === `/api/v1/shelves/reading/${id}` && init?.method === 'PUT') return { ok: true, json: async () => ({
        last_chapter_id: 11, completed_chapter_ids: init.body?.toString().includes('"completed":true') ? [11] : [],
        resume_chapter_id: 12, all_complete: false,
      }) };
      if (url === `/api/v1/shelves/reading/${id}`) return { ok: true, json: async () => ({
        last_chapter_id: 11, completed_chapter_ids: [], resume_chapter_id: 11, all_complete: false,
      }) };
      if (url.endsWith('/chapters/11')) return { ok: true, text: async () => '# First\nStart reading.' };
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal('fetch', mock);
    setSessionCsrf('test-csrf');
    render(<ShelfReader bookId={id} scope="public" requestedChapter={null} authenticated />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mark chapter complete' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Mark chapter complete' }));
    await waitFor(() => expect(screen.getByText('1 / 2 complete')).toBeInTheDocument());
    expect(mock).toHaveBeenCalledWith(`/api/v1/shelves/reading/${id}`, expect.objectContaining({ method: 'PUT',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'test-csrf' }),
      body: JSON.stringify({ chapter_id: 11, completed: true }),
    }));
    const chapters = screen.getByRole('navigation', { name: 'Book chapters' });
    expect(within(chapters).getByRole('button', { name: /First/ })).toHaveTextContent('First');
  });
});
