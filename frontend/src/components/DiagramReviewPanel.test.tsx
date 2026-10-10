import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import DiagramReviewPanel from './DiagramReviewPanel';

vi.mock('./MermaidDiagram', () => ({
  default: ({ chart, onValidationChange }: { chart: string; onValidationChange?: (valid: boolean) => void }) => {
    useEffect(() => onValidationChange?.(chart.includes('flowchart TD\n') && chart.includes('-->')), [chart, onValidationChange]);
    return <div>Preview: {chart}</div>;
  },
}));
afterEach(() => vi.unstubAllGlobals());
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const item = {
  id: 'diagram-1', chapter_order: 3, page_number: 42, asset_name: 'img_p42_x1.png',
  decision: 'PENDING', reason: 'provider_unavailable', mermaid_code: null, reviewed_at: null,
};

it('locates a fallback, accepts its original with admin CSRF and updates progress', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok([item]))
    .mockResolvedValueOnce(ok({ authenticated: true, csrf_token: 'csrf-token' }))
    .mockResolvedValueOnce(ok({ ...item, decision: 'ACCEPTED_IMAGE' }));
  vi.stubGlobal('fetch', fetcher);
  const onChanged = vi.fn(), onSelectChapter = vi.fn();
  render(<DiagramReviewPanel jobId="job-1" onChanged={onChanged} onSelectChapter={onSelectChapter} />);
  await screen.findByText('Chapter 3 · PDF page 42');
  expect(screen.getByAltText('Original diagram from PDF page 42').getAttribute('src'))
    .toBe('/api/v1/jobs/job-1/assets/img_p42_x1.png');
  fireEvent.click(screen.getByText('Open chapter'));
  expect(onSelectChapter).toHaveBeenCalledWith(3);
  fireEvent.click(screen.getByText('Accept original image'));
  await screen.findByText('Original accepted');
  expect(onChanged).toHaveBeenCalled();
  expect(fetcher.mock.calls[2][1].headers['X-CSRF-Token']).toBe('csrf-token');
  expect(JSON.parse(fetcher.mock.calls[2][1].body).decision).toBe('ACCEPTED_IMAGE');
  expect(screen.getByText(/1 of 1 reviewed/)).toBeTruthy();
});

it('explains legacy jobs and requires sign-in before editing', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok([]))
    .mockResolvedValueOnce(ok({ authenticated: false, csrf_token: null })));
  const { unmount } = render(<DiagramReviewPanel jobId="older" onChanged={vi.fn()} onSelectChapter={vi.fn()} />);
  await screen.findByText(/predates item-level tracking/);
  unmount();
  const fetcher = vi.fn().mockResolvedValueOnce(ok([item]))
    .mockResolvedValueOnce(ok({ authenticated: false, csrf_token: null }));
  vi.stubGlobal('fetch', fetcher);
  render(<DiagramReviewPanel jobId="newer" onChanged={vi.fn()} onSelectChapter={vi.fn()} />);
  await screen.findByText(/sign in under Settings/);
  await waitFor(() => expect(screen.queryByText('Edit as Mermaid')).toBeNull());
  expect(screen.getByText('sign in under Settings').getAttribute('href')).toBe('#settings');
});


it('blocks invalid Mermaid then saves a valid edit and updates the ZIP action', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok([item]))
    .mockResolvedValueOnce(ok({ authenticated: true, csrf_token: 'csrf-token' }))
    .mockResolvedValueOnce(ok({ ...item, decision: 'EDITED_MERMAID', mermaid_code: 'flowchart TD\nA --> B' }));
  vi.stubGlobal('fetch', fetcher);
  render(<DiagramReviewPanel jobId="job-2" onChanged={vi.fn()} onSelectChapter={vi.fn()} />);
  await screen.findByText('Chapter 3 · PDF page 42');
  fireEvent.click(screen.getByText('Edit as Mermaid'));
  const editor = screen.getByLabelText('Mermaid source for page 42');
  fireEvent.change(editor, { target: { value: 'invalid' } });
  await waitFor(() => expect((screen.getByText('Save Mermaid & update ZIP') as HTMLButtonElement).disabled).toBe(true));
  fireEvent.change(editor, { target: { value: 'flowchart TD\nA --> B' } });
  await waitFor(() => expect((screen.getByText('Save Mermaid & update ZIP') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText('Save Mermaid & update ZIP'));
  await screen.findByText('Mermaid edited');
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toMatchObject({ decision: 'EDITED_MERMAID', mermaid_code: 'flowchart TD\nA --> B' });
});
