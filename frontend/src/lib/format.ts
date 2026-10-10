import type { JobStatus } from '../api/types';

export const isTerminal = (status: JobStatus) =>
  status === 'SUCCEEDED' || status === 'PARTIAL' || status === 'FAILED';

export const canDownload = (status: JobStatus) =>
  status === 'SUCCEEDED' || status === 'PARTIAL';

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
}

export function prettyStage(name: string): string {
  return name.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export function statusLabel(status: JobStatus, total = 0, pending = 0): string {
  if (status === 'PARTIAL' && total > 0 && pending === 0) return 'Review complete';
  return ({ QUEUED: 'Queued', RUNNING: 'Converting', SUCCEEDED: 'Completed',
    PARTIAL: 'Needs review', FAILED: 'Failed' } as const)[status];
}
