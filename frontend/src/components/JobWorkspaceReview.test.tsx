import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import JobWorkspace from './JobWorkspace';
import type { Job, JobDetail } from '../api/types';

vi.mock('./DiagramReviewPanel', () => ({
  default: ({ onChanged }: { onChanged: () => void }) =>
    <button type="button" onClick={onChanged}>Mock review saved</button>,
}));
afterEach(() => vi.unstubAllGlobals());

it('reloads the selected chapter preview after a review updates Markdown', async () => {
  const job: Job = {
    id: 'j1', filename: 'book.pdf', status: 'PARTIAL', page_count: 2, chapter_count: 1,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), error: null,
    review_total: 1, review_pending: 1,
  };
  const detail: JobDetail = { ...job, stages: [], chapters: [{ order_index: 1, title: 'One', folder_name: '01_One',
    md_path: 'unused', mermaid_count: 0, image_count: 1, word_count: 9 }] };
  const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '# Chapter One' });
  vi.stubGlobal('fetch', fetcher);
  const onRefresh = vi.fn();
  render(<JobWorkspace job={job} detail={detail} events={[]} streamState="idle" onDelete={vi.fn()} onStop={vi.fn()} onRefresh={onRefresh} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText('Review diagrams'));
  fireEvent.click(screen.getByText('Mock review saved'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(onRefresh).toHaveBeenCalledTimes(1);
});

it('offers Stop on an active job, then disables it while cleanup is pending', () => {
  const job: Job = {
    id: 'running', filename: 'book.pdf', status: 'RUNNING', page_count: 0, chapter_count: 0,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), error: null,
  };
  const onStop = vi.fn();
  const { rerender } = render(<JobWorkspace job={job} detail={null} events={[]} streamState="idle"
    onStop={onStop} onDelete={vi.fn()} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Stop conversion' }));
  expect(onStop).toHaveBeenCalledWith('running');
  rerender(<JobWorkspace job={{ ...job, cancel_requested_at: new Date().toISOString() }} detail={null}
    events={[]} streamState="idle" onStop={onStop} onDelete={vi.fn()} onRefresh={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Stop conversion' })).toBeDisabled();
  expect(screen.getByText('Stopping… Waiting for worker cleanup.')).toBeInTheDocument();
});
