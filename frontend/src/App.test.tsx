import { cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const job = {
  id: 'job-123', filename: 'Field Guide.pdf', status: 'SUCCEEDED', page_count: 12,
  chapter_count: 1, created_at: '2026-10-09T08:00:00Z', updated_at: '2026-10-09T08:03:00Z', error: null,
};
const chapter = { order_index: 1, title: 'Chapter One', folder_name: '01_Chapter_One',
  md_path: '/data/outputs/job-123/01_Chapter_One/chapter.md', mermaid_count: 0, image_count: 1, word_count: 42 };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('dashboard wiring', () => {
  it('loads saved jobs, chapter preview, image asset and ZIP link', async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
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

  it('shows a useful offline state without pretending a job succeeded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<App />);
    await waitFor(() => expect(screen.getByText('API OFFLINE')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('Start Docker Compose');
    expect(screen.queryByRole('link', { name: /Download ZIP/i })).not.toBeInTheDocument();
  });
});
