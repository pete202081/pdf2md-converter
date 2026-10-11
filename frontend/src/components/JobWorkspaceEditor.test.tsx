import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import JobWorkspace from './JobWorkspace';
import type { Job, JobDetail } from '../api/types';

const job: Job = {
  id: 'book-1', filename: 'book.pdf', status: 'SUCCEEDED', page_count: 2, chapter_count: 2,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', error: null,
};
const detail: JobDetail = { ...job, stages: [], chapters: [
  { order_index: 1, title: 'One', folder_name: '01_One', md_path: 'unused', mermaid_count: 0, image_count: 0, word_count: 3 },
  { order_index: 2, title: 'Two', folder_name: '02_Two', md_path: 'unused', mermaid_count: 0, image_count: 0, word_count: 4 },
] };
const markdown = '# One\n\nOriginal paragraph.';
const revised = '# One\n\nCorrected paragraph.';
const reply = (value: unknown) => ({ ok: true, status: 200, json: async () => value, text: async () => value });

afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ''; });

it('opens editor from the selected finished chapter, previews and saves changes, then undoes', async () => {
  let current = markdown;
  let canUndo = false;
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/edit/undo')) { current = markdown; canUndo = false; return reply({ hash: 'c'.repeat(64), message: 'Undone' }); }
    if (url.endsWith('/edit') && init?.method === 'PUT') {
      expect(JSON.parse(String(init.body))).toMatchObject({ expected_hash: 'a'.repeat(64), markdown: revised });
      current = revised; canUndo = true;
      return reply({ hash: 'b'.repeat(64), message: 'Saved' });
    }
    if (url.endsWith('/edit')) return reply({ markdown: current, hash: current === markdown ? 'a'.repeat(64) : 'b'.repeat(64), can_undo: canUndo });
    if (url.endsWith('/preview')) return { ok: true, status: 200, text: async () => current };
    throw new Error(`Unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('confirm', vi.fn().mockReturnValue(true));
  const onRefresh = vi.fn();
  render(<JobWorkspace job={job} detail={detail} events={[]} streamState="idle"
    onStop={vi.fn()} onDelete={vi.fn()} onRefresh={onRefresh} />);
  await waitFor(() => expect(screen.getByText('Original paragraph.')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Edit chapter' }));
  const editor = await screen.findByLabelText('Chapter Markdown');
  expect(editor).toHaveValue(markdown);
  fireEvent.change(editor, { target: { value: revised } });
  expect(screen.getByText('Corrected paragraph.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Save chapter & ZIP' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Chapter saved'));
  expect(onRefresh).toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Edit chapter' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Undo last edit' })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Undo last edit' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('undone'));
  expect(fetcher).toHaveBeenCalledWith('/api/v1/jobs/book-1/chapters/1/edit/undo', expect.objectContaining({ method: 'POST' }));
});

it('keeps a dirty draft and warns before switching chapters', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) => url.endsWith('/edit')
    ? reply({ markdown, hash: 'a'.repeat(64), can_undo: false })
    : { ok: true, status: 200, text: async () => markdown });
  vi.stubGlobal('fetch', fetcher);
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal('confirm', confirm);
  const dirty = vi.fn();
  render(<JobWorkspace job={job} detail={detail} events={[]} streamState="idle"
    onStop={vi.fn()} onDelete={vi.fn()} onRefresh={vi.fn()} onDirtyChange={dirty} />);
  await screen.findByRole('button', { name: 'Edit chapter' });
  fireEvent.click(screen.getByRole('button', { name: 'Edit chapter' }));
  const editor = await screen.findByLabelText('Chapter Markdown');
  fireEvent.change(editor, { target: { value: revised } });
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
  fireEvent.click(screen.getByRole('button', { name: /Two.*4 words/i }));
  expect(editor).toHaveValue(revised);
  expect(confirm).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: /Two.*4 words/i }));
  await waitFor(() => expect(screen.queryByLabelText('Chapter Markdown')).not.toBeInTheDocument());
  expect(confirm).toHaveBeenCalledTimes(2);
});

it('retains a draft after a stale-hash conflict and does not pretend it saved', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/edit') && init?.method === 'PUT') return {
      ok: false, status: 409, json: async () => ({ message: 'Chapter changed' }),
    };
    if (url.endsWith('/edit')) return reply({ markdown, hash: 'a'.repeat(64), can_undo: false });
    return { ok: true, status: 200, text: async () => markdown };
  });
  vi.stubGlobal('fetch', fetcher);
  const onRefresh = vi.fn();
  render(<JobWorkspace job={job} detail={detail} events={[]} streamState="idle"
    onStop={vi.fn()} onDelete={vi.fn()} onRefresh={onRefresh} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit chapter' }));
  const editor = await screen.findByLabelText('Chapter Markdown');
  fireEvent.change(editor, { target: { value: revised } });
  fireEvent.click(screen.getByRole('button', { name: 'Save chapter & ZIP' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('changed elsewhere'));
  expect(editor).toHaveValue(revised);
  expect(onRefresh).not.toHaveBeenCalled();
  expect(screen.queryByText(/Chapter saved/)).not.toBeInTheDocument();
});
