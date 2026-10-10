import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import UploadPanel from './UploadPanel';

const upload = vi.hoisted(() => vi.fn());
vi.mock('../api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../api/client')>(), uploadJob: upload,
}));
afterEach(() => { cleanup(); upload.mockReset(); });

describe('upload UI', () => {
  it('accepts a PDF, uploads it, and selects the returned job', async () => {
    const onUploaded = vi.fn();
    upload.mockResolvedValue({ id: 'job-created', filename: 'book.pdf' });
    render(<UploadPanel onUploaded={onUploaded} />);
    const submit = screen.getByRole('button', { name: /Convert to Markdown/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Choose PDF file'), { target: { files: [new File(['%PDF-'], 'book.pdf', { type: 'application/pdf' })] } });
    expect(screen.getByText('book.pdf')).toBeInTheDocument();
    fireEvent.click(submit);
    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ id: 'job-created' })));
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it('rejects a non-PDF before sending to the API', () => {
    render(<UploadPanel onUploaded={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Choose PDF file'), { target: { files: [new File(['hello'], 'notes.txt')] } });
    expect(screen.getByRole('alert')).toHaveTextContent('Please choose a PDF file');
    expect(screen.getByRole('button', { name: /Convert to Markdown/i })).toBeDisabled();
    expect(upload).not.toHaveBeenCalled();
  });
});
