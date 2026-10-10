import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const job = {
  id: 'job-123', filename: 'Field Guide.pdf', status: 'SUCCEEDED', page_count: 12,
  chapter_count: 1, created_at: '2026-10-09T08:00:00Z', updated_at: '2026-10-09T08:03:00Z', error: null,
};
const chapter = { order_index: 1, title: 'Chapter One', folder_name: '01_Chapter_One',
  md_path: '/data/outputs/job-123/01_Chapter_One/chapter.md', mermaid_count: 0, image_count: 1, word_count: 42 };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.location.hash = ''; });

describe('dashboard wiring', () => {
  it('loads saved jobs, chapter preview, image asset and ZIP link', async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input.includes('/api/v1/auth/session')) return { ok: true, status: 200, json: async () => ({ authenticated: true, username: 'alice', role: 'USER', csrf_token: 'csrf-test' }) };
      if (input.includes('/preview')) return { ok: true, status: 200, text: async () => '# Chapter One\n\n![Diagram](../assets/img_p1_x7.png)' };
      if (input.includes('/job-123')) return { ok: true, status: 200, json: async () => ({ ...job, stages: [], chapters: [chapter] }) };
      return { ok: true, status: 200, json: async () => [job] };
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Chapter One' })).toBeInTheDocument());
    expect(screen.getByRole('img', { name: 'Diagram' })).toHaveAttribute('src', '/api/v1/jobs/job-123/assets/img_p1_x7.png');
    expect(screen.getByRole('link', { name: /Download ZIP/i })).toHaveAttribute('href', '/api/v1/jobs/job-123/download');
    expect(screen.getByRole('button', { name: /Field Guide.pdf/i })).toBeInTheDocument();
  });

  it('opens a searched chapter and its first source page without a query URL', async () => {
    const other = { ...job, id: 'job-other', filename: 'Another Book.pdf', chapter_count: 1 };
    const anotherChapter = { ...chapter, order_index: 4, title: 'Collected Tables', folder_name: '04_Collected_Tables' };
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === '/api/v1/auth/session') return { ok: true, status: 200, json: async () => ({ authenticated: true, username: 'admin', role: 'ADMIN', csrf_token: 'csrf-test' }) };
      if (input === '/api/v1/search') return { ok: true, status: 200, json: async () => ({ pending_books: 0,
        results: [{ job_id: 'job-other', filename: other.filename, chapter_order: 4,
          chapter_title: anotherChapter.title, first_page: 3, last_page: 5, excerpt: 'Interesting table value' }] }) };
      if (input === '/api/v1/jobs/job-other/quality') return { ok: true, status: 200, json: async () => ({ inventory_ready: false, chapters: [{ order: 4, source_pages: [3, 5] }], flags: [] }) };
      if (input === '/api/v1/jobs/job-other/quality/chapters/4') return { ok: true, status: 200, json: async () => ({ markdown: '# Collected Tables', hash: 'test', can_undo: false }) };
      if (input.includes('/preview')) return { ok: true, status: 200, text: async () => '# Collected Tables' };
      if (input === '/api/v1/jobs/job-other') return { ok: true, status: 200, json: async () => ({ ...other, stages: [], chapters: [anotherChapter] }) };
      if (input === '/api/v1/jobs/job-123') return { ok: true, status: 200, json: async () => ({ ...job, stages: [], chapters: [chapter] }) };
      return { ok: true, status: 200, json: async () => [job] }; // off the first library page
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Field Guide.pdf/i })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Search filenames, chapters & text'), { target: { value: 'Interesting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'View first source page' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'View first source page' }));
    await waitFor(() => expect(screen.getByText('Chapter Markdown')).toBeInTheDocument());
    expect(screen.getByText('04_Collected_Tables/chapter.md')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Page 3 of 3–5')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/jobs/job-other/chapters/4/preview', expect.anything());
  });

  it('shows a useful offline state without pretending a job succeeded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('Start Docker Compose');
    expect(screen.queryByRole('link', { name: /Download ZIP/i })).not.toBeInTheDocument();
  });
});
