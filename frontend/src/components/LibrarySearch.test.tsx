import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LibrarySearch from './LibrarySearch';
import { setSessionCsrf } from '../api/client';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setSessionCsrf(null); });

describe('private library search', () => {
  it('POSTs body with CSRF, renders text safely and opens the right chapter/source range', async () => {
    const onOpen = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ pending_books: 1,
      results: [{ job_id: 'job-2', filename: 'Other.pdf', chapter_order: 5, chapter_title: 'Tables',
        first_page: 8, last_page: 11, excerpt: '<script>alert(1)</script> | Reading | 55' }] }) });
    vi.stubGlobal('fetch', fetchMock);
    setSessionCsrf('private-token');
    render(<LibrarySearch onOpen={onOpen} />);
    fireEvent.change(screen.getByLabelText('Search filenames, chapters & text'), { target: { value: 'Reading' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(screen.getByText(/Chapter source pages 8–11/)).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/search', expect.objectContaining({ method: 'POST',
      body: JSON.stringify({ query: 'Reading', limit: 30 }), headers: expect.objectContaining({ 'X-CSRF-Token': 'private-token' }),
    }));
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText(/<script>alert/)).toBeInTheDocument();
    expect(screen.getByText(/not searchable yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Other.pdf · Tables/ }));
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ job_id: 'job-2', chapter_order: 5 }), false);
    fireEvent.click(screen.getByRole('button', { name: 'View first source page' }));
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ first_page: 8 }), true);
    fireEvent.change(screen.getByLabelText('Search filenames, chapters & text'), { target: { value: '' } });
    expect(screen.queryByText(/Chapter source pages/)).not.toBeInTheDocument();
  });

  it('handles empty results and a service failure without exposing old hits', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ pending_books: 0, results: [] }) })
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => ({ message: 'Search unavailable' }) });
    vi.stubGlobal('fetch', fetchMock);
    render(<LibrarySearch onOpen={vi.fn()} />);
    const input = screen.getByLabelText('Search filenames, chapters & text');
    fireEvent.change(input, { target: { value: 'missing' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(screen.getByText(/No matches/)).toBeInTheDocument());
    fireEvent.change(input, { target: { value: 'again' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Search unavailable'));
    expect(screen.queryByText(/No matches/)).not.toBeInTheDocument();
  });
});
